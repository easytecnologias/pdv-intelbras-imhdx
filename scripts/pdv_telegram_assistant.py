#!/usr/bin/env python3
import argparse
import calendar
import json
import os
import re
import subprocess
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
from requests.auth import HTTPDigestAuth
from PIL import Image, ImageDraw, ImageFont


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
    parser.add_argument("--imhdx-host", default=os.environ.get("IMHDX_HOST", ""))
    parser.add_argument("--imhdx-user", default=os.environ.get("IMHDX_USER", ""))
    parser.add_argument("--imhdx-pass", default=os.environ.get("IMHDX_PASS", ""))
    parser.add_argument("--imhdx-channel", type=int, default=int(os.environ.get("IMHDX_CHANNEL", "1")))
    parser.add_argument("--imhdx-window-before", type=int, default=int(os.environ.get("IMHDX_WINDOW_BEFORE", "2")))
    parser.add_argument("--imhdx-window-after", type=int, default=int(os.environ.get("IMHDX_WINDOW_AFTER", "8")))
    parser.add_argument("--ffmpegthumbnailer", default=os.environ.get("FFMPEGTHUMBNAILER", "ffmpegthumbnailer"))
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


def refresh_menu_keyboard(args):
    result = api(
        args,
        "sendMessage",
        data={
            "chat_id": args.chat_id,
            "text": "Menu",
            "reply_markup": json.dumps(main_keyboard()),
        },
    )
    time.sleep(1)
    try:
        delete_message(args, args.chat_id, result.get("message_id"))
    except Exception:
        pass


def send_calendar(args, dt=None):
    dt = dt or query_date(args)
    api(
        args,
        "sendMessage",
        data={
            "chat_id": args.chat_id,
            "text": calendar_title(dt),
            "reply_markup": json.dumps(calendar_keyboard(args, dt)),
        },
    )


def edit_calendar(args, chat_id, message_id, dt):
    api(
        args,
        "editMessageText",
        data={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": calendar_title(dt),
            "reply_markup": json.dumps(calendar_keyboard(args, dt)),
        },
    )


def edit_message(args, chat_id, message_id, text):
    api(
        args,
        "editMessageText",
        data={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:3900],
        },
    )


def delete_message(args, chat_id, message_id):
    api(args, "deleteMessage", data={"chat_id": chat_id, "message_id": message_id})


def answer_callback(args, callback_id, text=""):
    api(args, "answerCallbackQuery", data={"callback_query_id": callback_id, "text": text[:180]})


def send_photo(args, image_path, caption):
    with open(str(image_path), "rb") as photo:
        api(
            args,
            "sendPhoto",
            data={
                "chat_id": args.chat_id,
                "caption": caption[:1024],
                "reply_markup": json.dumps(main_keyboard()),
            },
            files={"photo": photo},
        )


def send_response(args, response):
    if isinstance(response, dict) and response.get("photo"):
        send_photo(args, response["photo"], response.get("caption", ""))
    elif isinstance(response, dict):
        send_message(args, response.get("text", "Sem resposta."))
    else:
        send_message(args, str(response))


def main_keyboard():
    return {
        "keyboard": [
            [{"text": "Status"}, {"text": "Data"}],
            [{"text": "Caixa"}, {"text": "Cupom"}],
            [{"text": "Ultimo cupom"}, {"text": "Buscar produto"}],
            [{"text": "Foto produto"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
        "input_field_placeholder": "Escolha uma opcao",
    }


MONTHS_BR = [
    "",
    "Janeiro",
    "Fevereiro",
    "Marco",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
]


def calendar_title(dt):
    return "📅 Escolha a data\n%s de %s" % (MONTHS_BR[dt.month], dt.year)


def add_month(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def calendar_keyboard(args, dt):
    first_weekday, days_in_month = calendar.monthrange(dt.year, dt.month)
    active = query_date(args)
    rows = [
        [{"text": "◀️", "callback_data": "cal:%s" % add_month(dt, -1).strftime("%Y-%m")},
         {"text": "%s %s" % (MONTHS_BR[dt.month], dt.year), "callback_data": "noop"},
         {"text": "▶️", "callback_data": "cal:%s" % add_month(dt, 1).strftime("%Y-%m")}],
        [{"text": "Seg", "callback_data": "noop"}, {"text": "Ter", "callback_data": "noop"}, {"text": "Qua", "callback_data": "noop"},
         {"text": "Qui", "callback_data": "noop"}, {"text": "Sex", "callback_data": "noop"}, {"text": "Sab", "callback_data": "noop"},
         {"text": "Dom", "callback_data": "noop"}],
    ]
    week = [{"text": " ", "callback_data": "noop"} for _ in range(first_weekday)]
    for day in range(1, days_in_month + 1):
        date_value = dt.replace(day=day)
        label = str(day)
        if date_value.strftime("%Y-%m-%d") == active.strftime("%Y-%m-%d"):
            label = "✅ %s" % day
        week.append({"text": label, "callback_data": "date:%s" % date_value.strftime("%Y-%m-%d")})
        if len(week) == 7:
            rows.append(week)
            week = []
    if week:
        week.extend([{"text": " ", "callback_data": "noop"} for _ in range(7 - len(week))])
        rows.append(week)
    rows.append([
        {"text": "Hoje", "callback_data": "date:%s" % datetime.now().strftime("%Y-%m-%d")},
        {"text": "Fechar", "callback_data": "close"},
    ])
    return {"inline_keyboard": rows}


def query_date(args):
    path = active_date_file(args)
    if path.exists():
        try:
            return datetime.strptime(path.read_text().strip(), "%Y-%m-%d")
        except Exception:
            pass
    return datetime.now()


def active_date_file(args):
    return Path(args.state_dir) / "active_date.txt"


def set_query_date(args, dt):
    path = active_date_file(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dt.strftime("%Y-%m-%d"))


def date_label(dt):
    return dt.strftime("%d/%m/%Y")


def parse_date_text(text):
    clean = text.strip().lower()
    now = datetime.now()
    if clean in ("hoje", "hj"):
        return now
    if clean in ("ontem",):
        return now - timedelta(days=1)
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(clean, fmt)
            if fmt == "%d/%m":
                parsed = parsed.replace(year=now.year)
            return parsed
        except Exception:
            pass
    raise ValueError("data invalida")


def spy_path(args, dt=None):
    dt = dt or query_date(args)
    name = "Espiao%s.%s" % (dt.strftime("%d%m%y"), args.pdv_station)
    return Path(args.pdv_base_dir) / "Cm" / name


def money(value):
    try:
        return float(str(value).strip().replace(",", "."))
    except Exception:
        return 0.0


def money_br(value):
    text = "{:,.2f}".format(value)
    return "R$ " + text.replace(",", "X").replace(".", ",").replace("X", ".")


def normalize_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.upper()


def payment_icon(name):
    clean = normalize_text(name)
    if "PIX" in clean:
        return "🔷"
    if "DINHEIRO" in clean:
        return "💵"
    if "CREDITO" in clean:
        return "💳"
    if "DEBITO" in clean:
        return "🏧"
    if "CARTAO" in clean or "POS" in clean:
        return "💳"
    return "💰"


def parse_fields(body):
    return {name: value.strip() for name, value in FIELD_RE.findall(body)}


def read_sales(args):
    path = spy_path(args)
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
    cups, _, _ = read_sales(args)
    closed = [cup for cup in cups if cup.get("closed")]
    total = sum(cup.get("total") or cup.get("subtotal") or sum(item["value"] for item in cup["items"]) for cup in closed)
    payments = defaultdict(float)
    item_count = 0
    for cup in cups:
        item_count += len(cup["items"])
        for payment in cup["payments"]:
            payments[payment["desc"] or ("Cod %s" % payment["code"])] += payment["value"]

    lines = [
        "💼 Caixa PDV %s" % args.pdv_station,
        "📅 %s" % date_label(query_date(args)),
        "",
        "🧾 Cupons fechados: %d" % len(closed),
        "📦 Itens registrados: %d" % item_count,
        "💰 Total vendido: %s" % money_br(total),
        "",
        "💳 Formas de pagamento",
    ]
    if payments:
        for name, value in sorted(payments.items()):
            lines.append("%s %s: %s" % (payment_icon(name), name.title(), money_br(value)))
    else:
        lines.append("• Sem pagamentos registrados ainda")
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
    return "PDV %s - Dinheiro %s\nLancamentos: %d\nTotal: %s" % (
        args.pdv_station,
        date_label(query_date(args)),
        count,
        money_br(total),
    )


def cupom_detail(args, number):
    _, by_number, _ = read_sales(args)
    cup = by_number.get(number)
    if not cup:
        return "Nao achei o cupom %s no Espiao de %s." % (number, date_label(query_date(args)))

    total = cup.get("total") or cup.get("subtotal") or sum(item["value"] for item in cup["items"])
    status_text = "Fechado" if cup.get("closed") else "Em aberto"
    lines = [
        "🧾 Cupom %s" % cup["number"],
        "📅 %s" % date_label(query_date(args)),
        "",
        "📌 Status: %s" % status_text,
        "🕒 Inicio: %s" % (cup.get("start") or "-"),
        "✅ Fechou: %s" % (cup.get("closed") or "-"),
        "👤 Operador: %s" % (cup.get("operator") or "-"),
        "💰 Total: %s" % money_br(total),
        "",
        "📦 Itens",
    ]
    for idx, item in enumerate(cup["items"], 1):
        lines.append(
            "%02d. %s" % (idx, item["desc"].title())
        )
        lines.append(
            "    %s x %s  •  %s" % (item["qty"], item.get("code") or "sem codigo", money_br(item["value"]))
        )
    if not cup["items"]:
        lines.append("    Nenhum item registrado")
    lines.append("")
    lines.append("💳 Pagamentos")
    for payment in cup["payments"]:
        lines.append("%s %s: %s" % (
            payment_icon(payment["desc"]),
            payment["desc"].title(),
            money_br(payment["value"]),
        ))
    if not cup["payments"]:
        lines.append("    Nenhum pagamento registrado")
    lines.append("")
    lines.append("🏁 Total do cupom: %s" % money_br(total))
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
        return "Nao achei '%s' nos itens de %s." % (term, date_label(query_date(args)))
    lines = ["Busca: %s" % term, "Data: %s" % date_label(query_date(args)), "Resultados: %d" % len(hits), ""]
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


def parse_event_time(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def item_datetime(args, item):
    dt = query_date(args)
    return datetime.strptime(dt.strftime("%Y-%m-%d") + " " + item["time"], "%Y-%m-%d %H:%M:%S")


def find_photo_for_item(args, item, seconds=60):
    events_path = Path(args.events_file)
    if not events_path.exists():
        return None
    item_dt = item_datetime(args, item)
    best = None
    best_delta = None
    desc = item["desc"].lower()
    code = item["code"]
    for line in events_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("tipo") != "casou":
            continue
        try:
            event_dt = parse_event_time(event.get("hora", ""))
        except Exception:
            continue
        delta = abs((event_dt - item_dt).total_seconds())
        if delta > seconds:
            continue
        reason = str(event.get("motivo", "")).lower()
        if code and code not in event.get("motivo", "") and desc not in reason:
            continue
        image = event.get("imagem")
        if not image or not Path(image).exists():
            continue
        if best is None or delta < best_delta:
            best = event
            best_delta = delta
    return best


def text_width(draw, text, font):
    try:
        return draw.textbbox((0, 0), text, font=font)[2]
    except Exception:
        return draw.textsize(text, font=font)[0]


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if current and text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def overlay_pdv_caption(args, image_path, cupom, item, source):
    image_path = Path(image_path)
    image = Image.open(str(image_path)).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size

    font_path = first_existing_path([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ])
    if font_path:
        font = ImageFont.truetype(font_path, 26)
        small_font = ImageFont.truetype(font_path, 22)
    else:
        font = ImageFont.load_default()
        small_font = font
    lines = [
        "PDV %s  CUPOM %s  %s" % (int(args.pdv_station), cupom, item["time"]),
        "%s x %s" % (item["qty"], item["desc"]),
        "COD %s  VALOR %s  %s" % (item["code"], money_br(item["value"]), source),
    ]

    wrapped = []
    for idx, line in enumerate(lines):
        wrapped.extend(wrap_text(draw, line, font if idx == 0 else small_font, width - 36))

    line_height = 32
    box_height = 22 + (len(wrapped) * line_height)
    draw.rectangle((0, 0, width, min(height, box_height)), fill=(0, 0, 0, 150))

    y = 10
    for idx, line in enumerate(wrapped):
        active_font = font if idx == 0 else small_font
        draw.text((18 + 2, y + 2), line, font=active_font, fill=(0, 0, 0, 220))
        draw.text((18, y), line, font=active_font, fill=(255, 230, 0, 255))
        y += line_height

    out_path = image_path.with_name(image_path.stem + "_pdv.jpg")
    image.save(str(out_path), quality=88)
    return str(out_path)


def first_existing_path(paths):
    for path in paths:
        if Path(path).exists():
            return path
    return ""


def imhdx_photo_for_item(args, cupom, item):
    if not args.imhdx_host or not args.imhdx_user or not args.imhdx_pass:
        return None

    item_dt = item_datetime(args, item)
    start = item_dt - timedelta(seconds=args.imhdx_window_before)
    end = item_dt + timedelta(seconds=args.imhdx_window_after)
    start_text = quote(start.strftime("%Y-%m-%d %H:%M:%S"))
    end_text = quote(end.strftime("%Y-%m-%d %H:%M:%S"))
    url = (
        "http://%s/cgi-bin/loadfile.cgi?action=startLoad&channel=%s&startTime=%s&endTime=%s"
        % (args.imhdx_host, args.imhdx_channel, start_text, end_text)
    )

    out_dir = Path(args.state_dir) / "imhdx"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = item_dt.strftime("%Y%m%d_%H%M%S")
    base = "%s_%s" % (stamp, re.sub(r"[^0-9A-Za-z_-]+", "_", item.get("code") or "item"))
    dav_path = out_dir / ("%s.dav" % base)
    jpg_path = out_dir / ("%s.jpg" % base)

    try:
        response = requests.get(
            url,
            auth=HTTPDigestAuth(args.imhdx_user, args.imhdx_pass),
            timeout=25,
        )
        if response.status_code != 200 or len(response.content) < 1024:
            return None
        if not response.content.startswith(b"DHAV"):
            return None
        dav_path.write_bytes(response.content)
        result = subprocess.run(
            [
                args.ffmpegthumbnailer,
                "-i",
                str(dav_path),
                "-o",
                str(jpg_path),
                "-s",
                "0",
                "-t",
                "00:00:%02d" % max(1, args.imhdx_window_before),
                "-q",
                "8",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and jpg_path.exists() and jpg_path.stat().st_size > 1024:
            source = "Gravacao PDV%s / iMHDX" % int(args.pdv_station)
            stamped_path = overlay_pdv_caption(args, jpg_path, cupom, item, source)
            return {
                "imagem": stamped_path,
                "hora": item_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "fonte": source,
            }
    except Exception:
        return None
    return None


def product_photo(args, cupom, term):
    _, by_number, _ = read_sales(args)
    cup = by_number.get(str(cupom).strip())
    if not cup:
        return {"text": "Nao achei o cupom %s em %s." % (cupom, date_label(query_date(args)))}
    term_low = term.lower().strip()
    matches = [
        item for item in cup["items"]
        if term_low in item["desc"].lower() or term_low in item["code"]
    ]
    if not matches:
        return {"text": "Nao achei '%s' no cupom %s." % (term, cupom)}
    item = matches[0]
    event = imhdx_photo_for_item(args, cupom, item)
    if not event:
        event = find_photo_for_item(args, item)
        if event:
            try:
                event["imagem"] = overlay_pdv_caption(args, event["imagem"], cupom, item, "auditor local")
            except Exception:
                pass
    if not event:
        return {
            "text": (
                "Achei o item, mas nao consegui gerar foto perto do horario.\n"
                "Cupom %s %s - %s x %s - %s"
            ) % (cupom, item["time"], item["qty"], item["desc"], money_br(item["value"]))
        }
    caption = (
        "Cupom %s\n%s - %s x %s\nValor: %s\nHora item: %s\nFonte: %s"
        % (
            cupom,
            item["code"],
            item["qty"],
            item["desc"],
            money_br(item["value"]),
            item["time"],
            event.get("fonte", "auditor local"),
        )
    )
    return {"photo": event["imagem"], "caption": caption}


def split_cupom_product(text):
    clean = " ".join(text.strip().split())
    match = re.match(r"^(\d{4,})\s+(.+)$", clean)
    if match:
        return match.group(1), match.group(2).strip()
    match = re.match(r"^(.+?)\s+(\d{4,})$", clean)
    if match:
        return match.group(2), match.group(1).strip()
    return None


def parse_product_photo_request(text):
    clean = " ".join(text.strip().split())
    if clean.startswith("/"):
        return None
    match = re.match(r"(?i)^produto\s+(.+?)\s+(?:do\s+)?cupom\s+(\d+)$", clean)
    if match:
        return match.group(2), match.group(1).strip()
    match = re.match(r"(?i)^foto\s+produto\s+(.+?)\s+(?:do\s+)?cupom\s+(\d+)$", clean)
    if match:
        return match.group(2), match.group(1).strip()
    return split_cupom_product(clean)


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
        event_date = str(event.get("hora", ""))[:10]
        if event.get("tipo") == "suspeita" and event_date == query_date(args).strftime("%Y-%m-%d"):
            rows.append(event)
    if not rows:
        return "Nenhuma suspeita registrada em %s." % date_label(query_date(args))
    lines = ["Ultimas suspeitas em %s:" % date_label(query_date(args))]
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
    cups, _, _ = read_sales(args)
    last_cup = next((cup for cup in reversed(cups) if cup["items"]), None)
    closed = [cup for cup in cups if cup.get("closed")]
    item_count = sum(len(cup["items"]) for cup in cups)
    total = sum(cup.get("total") or cup.get("subtotal") or sum(item["value"] for item in cup["items"]) for cup in closed)
    lines = [
        "✅ PDV %s online" % args.pdv_station,
        "",
        "📅 Data da consulta: %s" % date_label(query_date(args)),
        "🧾 Cupons fechados: %d" % len(closed),
        "📦 Itens registrados: %d" % item_count,
        "💰 Total vendido: %s" % money_br(total),
    ]
    if last_cup:
        last_item = last_cup["items"][-1]
        lines.extend([
            "",
            "🕒 Ultimo movimento",
            "Cupom %s - %s" % (last_cup.get("number", "-"), last_item["time"]),
            "%s x %s" % (last_item["qty"], last_item["desc"]),
        ])
    else:
        lines.extend(["", "🕒 Ainda sem movimento registrado para esta data."])
    return "\n".join(lines)


def help_text():
    return "Menu atualizado. Use os botoes fixos abaixo."


def handle_command(args, text):
    natural_photo = parse_product_photo_request(text)
    if natural_photo:
        cupom, term = natural_photo
        return product_photo(args, cupom, term)
    text = normalize_button_text(text)
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/ajuda", "/help", "/start", "/menu"):
        return help_text()
    if cmd == "/status":
        return status(args)
    if cmd == "/data":
        if not rest:
            return "Use: /data 24/05/2026\nOu toque em Data e envie hoje, ontem ou dd/mm/aaaa."
        dt = parse_date_text(rest)
        set_query_date(args, dt)
        return "Data ativa alterada para %s." % date_label(dt)
    if cmd == "/caixa":
        return caixa_summary(args)
    if cmd == "/dinheiro":
        return dinheiro_summary(args)
    if cmd in ("/ultimo", "/ultimocupom"):
        return latest_coupon(args)
    if cmd == "/cupom":
        return cupom_detail(args, rest) if rest else "Digite o numero do cupom. Exemplo: 216530"
    if cmd in ("/buscar", "/produto"):
        return search_items(args, rest) if rest else "Digite assim: /buscar bombom\nOu toque em Buscar produto e depois envie o nome."
    if cmd in ("/foto", "/imagem", "/print"):
        parsed = split_cupom_product(rest)
        if not parsed:
            return "Use: /foto 216657 arroz\nOu: arroz 216657"
        return product_photo(args, parsed[0], parsed[1])
    if cmd == "/suspeitas":
        return suspect_summary(args)
    return ""


def normalize_button_text(text):
    clean = " ".join(text.strip().split())
    mapping = {
        "status": "/status",
        "data": "/data",
        "caixa": "/caixa",
        "cupom": "/cupom",
        "dinheiro": "/dinheiro",
        "suspeitas": "/suspeitas",
        "ajuda": "/ajuda",
        "menu": "/menu",
        "ultimo cupom": "/ultimo",
        "buscar produto": "/buscar",
        "foto produto": "/foto",
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


def pending_mode_file(args, chat_id):
    return Path(args.state_dir) / ("pending_mode_%s.txt" % chat_id)


def set_pending_search(args, chat_id):
    path = pending_mode_file(args, chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("search %d" % int(time.time()))


def set_pending_mode(args, chat_id, mode):
    path = pending_mode_file(args, chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("%s %d" % (mode, int(time.time())))


def pop_pending_search(args, chat_id):
    mode = pop_pending_mode(args, chat_id)
    return mode == "search"


def pop_pending_mode(args, chat_id):
    path = pending_mode_file(args, chat_id)
    if not path.exists():
        return ""
    try:
        content = path.read_text().strip().split()
        mode = content[0]
        created = int(content[1]) if len(content) > 1 else 0
    except Exception:
        mode = ""
        created = 0
    try:
        path.unlink()
    except Exception:
        pass
    return mode if (time.time() - created) <= 300 else ""


def handle_callback(args, callback):
    callback_id = callback.get("id")
    data = callback.get("data") or ""
    print("CALLBACK", data, flush=True)
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if str(chat_id) != str(args.chat_id):
        if callback_id:
            answer_callback(args, callback_id, "Chat nao autorizado.")
        return

    if data == "noop":
        answer_callback(args, callback_id)
        return
    if data == "close":
        answer_callback(args, callback_id, "Calendario fechado.")
        delete_message(args, chat_id, message_id)
        return
    if data.startswith("cal:"):
        dt = datetime.strptime(data[4:] + "-01", "%Y-%m-%d")
        edit_calendar(args, chat_id, message_id, dt)
        answer_callback(args, callback_id)
        return
    if data.startswith("date:"):
        dt = datetime.strptime(data[5:], "%Y-%m-%d")
        set_query_date(args, dt)
        text = "✅ Data ativa alterada para %s." % date_label(dt)
        edit_message(args, chat_id, message_id, text)
        answer_callback(args, callback_id, text)
        send_message(args, text)
        return
    answer_callback(args, callback_id)


def main():
    args = parse_args()
    state_dir = Path(args.state_dir)
    offset_file = state_dir / "offset.txt"
    offset = read_offset(offset_file)
    print("ASSISTENTE_INICIO", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    try:
        refresh_menu_keyboard(args)
    except Exception as exc:
        print("MENU_REFRESH_ERRO", type(exc).__name__, exc, flush=True)

    while True:
        try:
            updates = api(
                args,
                "getUpdates",
                data={"offset": offset + 1, "timeout": args.poll_timeout, "allowed_updates": json.dumps(["message", "callback_query"])},
            )
            for update in updates:
                offset = max(offset, int(update["update_id"]))
                write_offset(offset_file, offset)
                callback = update.get("callback_query")
                if callback:
                    try:
                        handle_callback(args, callback)
                    except Exception as exc:
                        print("CALLBACK_ERRO", type(exc).__name__, exc, flush=True)
                    continue
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                text = (message.get("text") or "").strip()
                if str(chat.get("id")) != str(args.chat_id):
                    continue
                print("COMANDO", text, flush=True)
                try:
                    normalized = normalize_button_text(text)
                    if normalized == "/buscar":
                        set_pending_mode(args, chat.get("id"), "search")
                        answer = "Qual produto voce quer buscar? Exemplo: bombom, arroz, coca, leite."
                    elif normalized == "/data":
                        send_calendar(args)
                        continue
                    elif normalized == "/cupom":
                        set_pending_mode(args, chat.get("id"), "cupom")
                        answer = "Qual numero do cupom? Exemplo: 216530."
                    elif normalized == "/foto":
                        set_pending_mode(args, chat.get("id"), "photo")
                        answer = "Envie o cupom e o produto. Exemplo: 216657 arroz."
                    elif not text.startswith("/"):
                        mode = pop_pending_mode(args, chat.get("id"))
                        if mode == "search":
                            answer = search_items(args, text)
                        elif mode == "date":
                            dt = parse_date_text(text)
                            set_query_date(args, dt)
                            answer = "Data ativa alterada para %s." % date_label(dt)
                        elif mode == "cupom":
                            answer = cupom_detail(args, text.strip())
                        elif mode == "photo":
                            parsed = split_cupom_product(text)
                            if not parsed:
                                answer = "Envie assim: arroz 216657."
                            else:
                                answer = product_photo(args, parsed[0], parsed[1])
                        else:
                            answer = handle_command(args, text)
                    else:
                        answer = handle_command(args, text)
                except Exception as exc:
                    answer = "Erro ao executar comando: %s %s" % (type(exc).__name__, exc)
                if answer:
                    send_response(args, answer)
        except Exception as exc:
            print("ASSISTENTE_ERRO", type(exc).__name__, exc, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
