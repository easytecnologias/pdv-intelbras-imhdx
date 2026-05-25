#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests


LINE_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2}):(?P<event>[A-Z]+)\s*\|\s*(?P<body>.*)$")
FIELD_RE = re.compile(r"(\w+):\s*([^|]+)")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--pdv-station", default=os.environ.get("PDV_STATION", "001"))
    parser.add_argument("--pdv-base-dir", default=os.environ.get("PDV_BASE_DIR", "/home/rpdv/frente"))
    parser.add_argument("--events-file", default=os.environ.get("AUDITOR_EVENTS_FILE", "/var/log/pdv-camera-auditor/events.jsonl"))
    parser.add_argument("--state-dir", default=os.environ.get("BOT_STATE_DIR", "/var/lib/pdv-telegram-assistant"))
    parser.add_argument("--poll-timeout", type=int, default=25)
    args = parser.parse_args()
    if not args.token or not args.chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ausentes")
    return args


def api(args, method, **kwargs):
    url = "https://api.telegram.org/bot%s/%s" % (args.token, method)
    response = requests.post(url, timeout=35, **kwargs)
    if response.status_code != 200:
        raise RuntimeError("telegram %s HTTP %s: %s" % (method, response.status_code, response.text[:200]))
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("telegram %s: %s" % (method, payload))
    return payload["result"]


def send_message(args, text):
    api(
        args,
        "sendMessage",
        data={
            "chat_id": args.chat_id,
            "text": text[:3900],
            "reply_markup": json.dumps(main_keyboard()),
        },
    )


def main_keyboard():
    return {
        "keyboard": [
            [{"text": "Status"}, {"text": "Caixa"}],
            [{"text": "Dinheiro"}, {"text": "Suspeitas"}],
            [{"text": "Ultimo cupom"}, {"text": "Buscar produto"}],
            [{"text": "Ajuda"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }


def today_spy_path(args):
    name = "Espiao%s.%s" % (datetime.now().strftime("%d%m%y"), args.pdv_station)
    return Path(args.pdv_base_dir) / "Cm" / name


def money(value):
    try:
        return float(str(value).strip().replace(",", "."))
    except Exception:
        return 0.0


def money_br(value):
    text = "%.2f" % value
    return "R$ " + text.replace(".", ",")


def parse_fields(body):
    return {name: value.strip() for name, value in FIELD_RE.findall(body)}


def read_sales(args):
    path = today_spy_path(args)
    cups = []
    by_number = {}
    current = None
    if not path.exists():
        return cups, by_number, path

    for raw in path.read_text(errors="replace").splitlines():
        match = LINE_RE.match(raw.strip())
        if not match:
            continue
        ts = match.group("time")
        event = match.group("event")
        fields = parse_fields(match.group("body"))

        if event == "ABRECUPOM":
            number = fields.get("Cod") or "SEM_CUPOM"
            current = {
                "number": number,
                "start": ts,
                "operator": fields.get("Descricao", ""),
                "items": [],
                "payments": [],
                "subtotal": 0.0,
                "total": 0.0,
                "closed": "",
            }
            cups.append(current)
            by_number[number] = current
        elif event == "VIT":
            if current is None:
                current = {
                    "number": "SEM_CUPOM",
                    "start": ts,
                    "operator": "",
                    "items": [],
                    "payments": [],
                    "subtotal": 0.0,
                    "total": 0.0,
                    "closed": "",
                }
                cups.append(current)
            item = {
                "time": ts,
                "code": fields.get("Cod", ""),
                "desc": fields.get("Descricao", ""),
                "qty": fields.get("Quant", "1"),
                "unit": fields.get("Und", ""),
                "value": money(fields.get("VlTotal", "0")),
            }
            current["items"].append(item)
        elif event == "SBT" and current is not None:
            current["subtotal"] = money(fields.get("VlTotal", "0"))
        elif event == "FIN" and current is not None:
            value = money(fields.get("VlTotal", "0"))
            current["payments"].append({
                "time": ts,
                "code": fields.get("Cod", ""),
                "desc": fields.get("Descricao", ""),
                "value": value,
            })
        elif event == "FECHACUPOM":
            number = fields.get("Cod", "")
            cup = by_number.get(number, current)
            if cup is not None:
                cup["closed"] = ts
                cup["total"] = money(fields.get("VlTotal", "0"))
                if number:
                    cup["number"] = number
                    by_number[number] = cup
                current = None

    return cups, by_number, path


def caixa_summary(args):
    cups, _, path = read_sales(args)
    closed = [cup for cup in cups if cup.get("closed")]
    total = sum(cup.get("total") or cup.get("subtotal") or sum(item["value"] for item in cup["items"]) for cup in closed)
    payments = defaultdict(float)
    item_count = 0
    for cup in cups:
        item_count += len(cup["items"])
        for payment in cup["payments"]:
            payments[payment["desc"] or ("Cod %s" % payment["code"])] += payment["value"]

    lines = [
        "PDV %s - Caixa de hoje" % args.pdv_station,
        "Arquivo: %s" % path.name,
        "Cupons fechados: %d" % len(closed),
        "Itens registrados: %d" % item_count,
        "Total fechado: %s" % money_br(total),
        "",
        "Pagamentos:",
    ]
    if payments:
        for name, value in sorted(payments.items()):
            lines.append("- %s: %s" % (name, money_br(value)))
    else:
        lines.append("- sem pagamentos ainda")
    return "\n".join(lines)


def dinheiro_summary(args):
    cups, _, _ = read_sales(args)
    total = 0.0
    count = 0
    for cup in cups:
        for payment in cup["payments"]:
            if "DINHEIRO" in payment["desc"].upper():
                total += payment["value"]
                count += 1
    return "PDV %s - Dinheiro hoje\nLancamentos: %d\nTotal: %s" % (args.pdv_station, count, money_br(total))


def cupom_detail(args, number):
    _, by_number, _ = read_sales(args)
    cup = by_number.get(number)
    if not cup:
        return "Nao achei o cupom %s no Espiao de hoje." % number

    lines = [
        "Cupom %s" % cup["number"],
        "Inicio: %s  Fechou: %s" % (cup.get("start") or "-", cup.get("closed") or "-"),
        "Operador: %s" % (cup.get("operator") or "-"),
        "",
        "Itens:",
    ]
    for idx, item in enumerate(cup["items"], 1):
        lines.append("%d. %s x %s - %s" % (idx, item["qty"], item["desc"], money_br(item["value"])))
    if not cup["items"]:
        lines.append("- sem itens")
    lines.append("")
    lines.append("Pagamentos:")
    for payment in cup["payments"]:
        lines.append("- %s: %s" % (payment["desc"], money_br(payment["value"])))
    if not cup["payments"]:
        lines.append("- sem pagamento")
    if cup.get("closed"):
        total = cup.get("total") or cup.get("subtotal") or sum(item["value"] for item in cup["items"])
    else:
        total = sum(item["value"] for item in cup["items"])
    lines.append("")
    lines.append("Total: %s" % money_br(total))
    return "\n".join(lines)


def search_items(args, term):
    term_low = term.lower()
    cups, _, _ = read_sales(args)
    hits = []
    for cup in cups:
        for item in cup["items"]:
            if term_low in item["desc"].lower() or term_low in item["code"]:
                hits.append((cup, item))
    if not hits:
        return "Nao achei '%s' nos itens de hoje." % term
    lines = ["Busca: %s" % term, "Resultados: %d" % len(hits), ""]
    for cup, item in hits[-25:]:
        lines.append("Cupom %s %s - %s x %s - %s" % (
            cup.get("number", "-"),
            item["time"],
            item["qty"],
            item["desc"],
            money_br(item["value"]),
        ))
    if len(hits) > 25:
        lines.append("Mostrando os ultimos 25.")
    return "\n".join(lines)


def suspect_summary(args):
    path = Path(args.events_file)
    if not path.exists():
        return "Sem arquivo de eventos do auditor ainda."
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("tipo") == "suspeita":
            rows.append(event)
    if not rows:
        return "Nenhuma suspeita registrada hoje."
    lines = ["Ultimas suspeitas:"]
    for event in rows[-10:]:
        lines.append("%s score=%s skin=%s\n%s" % (
            event.get("hora", "-"),
            event.get("score", "-"),
            event.get("skin", "-"),
            event.get("imagem", "-"),
        ))
    return "\n".join(lines)


def latest_coupon(args):
    cups, _, _ = read_sales(args)
    for cup in reversed(cups):
        if cup["items"] or cup["payments"] or cup.get("closed"):
            return cupom_detail(args, cup["number"])
    return "Ainda nao achei cupom com movimento hoje."


def status(args):
    cups, _, path = read_sales(args)
    last_cup = next((cup for cup in reversed(cups) if cup["items"]), None)
    lines = [
        "PDV %s - Assistente ativo" % args.pdv_station,
        "Espiao: %s" % path,
        "Cupons lidos: %d" % len(cups),
    ]
    if last_cup:
        last_item = last_cup["items"][-1]
        lines.append("Ultimo item: cupom %s %s - %s" % (last_cup.get("number", "-"), last_item["time"], last_item["desc"]))
    return "\n".join(lines)


def help_text():
    return "\n".join([
        "Toque nos botoes ou digite:",
        "Status",
        "Caixa",
        "Dinheiro",
        "Ultimo cupom",
        "Buscar produto",
        "Suspeitas",
        "",
        "Tambem aceita:",
        "/cupom 216530",
        "/buscar bombom",
        "/produto arroz",
    ])


def handle_command(args, text):
    text = normalize_button_text(text)
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/ajuda", "/help", "/start"):
        return help_text()
    if cmd == "/status":
        return status(args)
    if cmd == "/caixa":
        return caixa_summary(args)
    if cmd == "/dinheiro":
        return dinheiro_summary(args)
    if cmd in ("/ultimo", "/ultimocupom"):
        return latest_coupon(args)
    if cmd == "/cupom":
        return cupom_detail(args, rest) if rest else "Use: /cupom 216530"
    if cmd in ("/buscar", "/produto"):
        return search_items(args, rest) if rest else "Digite assim: /buscar bombom\nOu toque em Buscar produto e depois envie o nome."
    if cmd == "/suspeitas":
        return suspect_summary(args)
    return "Comando nao reconhecido.\n\n%s" % help_text()


def normalize_button_text(text):
    clean = " ".join(text.strip().split())
    mapping = {
        "status": "/status",
        "caixa": "/caixa",
        "dinheiro": "/dinheiro",
        "suspeitas": "/suspeitas",
        "ajuda": "/ajuda",
        "ultimo cupom": "/ultimo",
        "buscar produto": "/buscar",
    }
    return mapping.get(clean.lower(), clean)


def read_offset(path):
    try:
        return int(path.read_text().strip())
    except Exception:
        return 0


def write_offset(path, offset):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset))


def pending_search_file(args, chat_id):
    return Path(args.state_dir) / ("pending_search_%s.txt" % chat_id)


def set_pending_search(args, chat_id):
    path = pending_search_file(args, chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(time.time())))


def pop_pending_search(args, chat_id):
    path = pending_search_file(args, chat_id)
    if not path.exists():
        return False
    try:
        created = int(path.read_text().strip() or "0")
    except Exception:
        created = 0
    try:
        path.unlink()
    except Exception:
        pass
    return (time.time() - created) <= 300


def main():
    args = parse_args()
    state_dir = Path(args.state_dir)
    offset_file = state_dir / "offset.txt"
    offset = read_offset(offset_file)
    print("ASSISTENTE_INICIO", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)

    while True:
        try:
            updates = api(
                args,
                "getUpdates",
                data={"offset": offset + 1, "timeout": args.poll_timeout, "allowed_updates": json.dumps(["message"])},
            )
            for update in updates:
                offset = max(offset, int(update["update_id"]))
                write_offset(offset_file, offset)
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                text = (message.get("text") or "").strip()
                if str(chat.get("id")) != str(args.chat_id):
                    continue
                print("COMANDO", text, flush=True)
                try:
                    normalized = normalize_button_text(text)
                    if normalized == "/buscar":
                        set_pending_search(args, chat.get("id"))
                        answer = "Qual produto voce quer buscar? Exemplo: bombom, arroz, coca, leite."
                    elif not text.startswith("/") and pop_pending_search(args, chat.get("id")):
                        answer = search_items(args, text)
                    else:
                        answer = handle_command(args, text)
                except Exception as exc:
                    answer = "Erro ao executar comando: %s %s" % (type(exc).__name__, exc)
                send_message(args, answer)
        except Exception as exc:
            print("ASSISTENTE_ERRO", type(exc).__name__, exc, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
