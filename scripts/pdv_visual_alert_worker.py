#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/pdv-telegram-assistant")

import pdv_telegram_assistant as bot


STATE_PATH = Path("/var/lib/pdv-visual-auditor/alert_worker_state.json")
POLL_SECONDS = float(os.environ.get("VISUAL_ALERT_POLL_SECONDS", "1"))
MIN_VALUE = float(os.environ.get("VISUAL_ALERT_MIN_VALUE", "20"))
SEND_CONFERE = os.environ.get("VISUAL_ALERT_SEND_CONFERE", "0") == "1"
START_FRESH = os.environ.get("VISUAL_ALERT_START_FRESH", "1") == "1"
DELAY_SECONDS = float(os.environ.get("VISUAL_ALERT_DELAY_SECONDS", "25"))
RISK_WORDS = (
    "CARNE",
    "BOV",
    "SUIN",
    "FRANGO",
    "PEIXE",
    "CERV",
    "WHISKY",
    "AZEITE",
    "SABAO",
    "REFRIGERANTE",
    "REFRI",
    "CIGARRO",
)


def log(message):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message), flush=True)


def clean_bot_args():
    sys.argv = [sys.argv[0]]


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"seen": []}


def save_seen(seen):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"seen": sorted(seen)}, sort_keys=True))
    tmp_path.replace(STATE_PATH)


def item_key(cupom, item):
    return "|".join(
        [
            str(cupom),
            str(item.get("time", "")),
            str(item.get("code", "")),
            str(item.get("desc", "")),
            str(item.get("qty", "")),
            "%.2f" % float(item.get("value") or 0),
        ]
    )


def iter_items(args):
    cups, _, _ = bot.read_sales(args)
    for cup in cups:
        cupom = cup.get("number", "")
        for item in cup.get("items", []):
            yield cup, item, item_key(cupom, item)


def item_is_target(item):
    description = bot.normalize_text(item.get("desc", ""))
    unit_value = bot.item_unit_value(item)
    line_total = bot.money(item.get("value"))
    return line_total >= MIN_VALUE or unit_value >= MIN_VALUE or any(
        word in description for word in RISK_WORDS
    )


def send_alert(args, response, elapsed):
    prefix = "ALERTA VISUAL EM TEMPO REAL\nTempo: %.1fs\n\n" % elapsed
    if response.get("caption"):
        response = dict(response)
        response["caption"] = prefix + response["caption"]
    elif response.get("text"):
        response = {"text": prefix + response["text"]}
    bot.send_response(args, response)


def audit_exact_item(args, cup, item):
    cupom = cup.get("number", "")
    event = bot.imhdx_photo_for_item(args, cupom, item)
    if not event:
        return {
            "text": (
                "Auditoria visual em tempo real\n\n"
                "Achei o item, mas a gravacao do iMHDX ainda nao estava "
                "disponivel perto do horario.\n"
                "Cupom %s %s - %s x %s - %s"
            )
            % (
                cupom,
                item.get("time", ""),
                item.get("qty", ""),
                item.get("desc", "").title(),
                bot.money_br(item.get("value", 0.0)),
            )
        }

    image_path = event["imagem"]
    source = event.get("fonte", "Gravacao iMHDX")
    audit = bot.run_visual_auditor(image_path, item)
    caption = bot.format_visual_audit_caption(
        args, cupom, item, audit, source
    )
    return {"photo": image_path, "caption": caption}


def should_send_response(response):
    text = response.get("caption") or response.get("text") or ""
    if "Resultado:" not in text:
        return False
    if SEND_CONFERE:
        return True
    return "Resultado: CONFERE" not in text


def main():
    clean_bot_args()
    args = bot.parse_args()
    state = load_state()
    seen = set(state.get("seen", []))

    if START_FRESH:
        seen = {key for _, _, key in iter_items(args)}
        save_seen(seen)
        log("baseline criado com %s itens; aguardando novos itens" % len(seen))

    log(
        "worker iMHDX iniciado poll=%ss min_value=%.2f delay=%.1fs"
        % (POLL_SECONDS, MIN_VALUE, DELAY_SECONDS)
    )
    pending = {}

    while True:
        try:
            for cup, item, key in iter_items(args):
                if key in seen:
                    continue

                seen.add(key)
                save_seen(seen)

                if not item_is_target(item):
                    log(
                        "ignorado cupom=%s item=%s valor_unit=%.2f total=%.2f"
                        % (
                            cup.get("number", ""),
                            item.get("desc", ""),
                            bot.item_unit_value(item),
                            bot.money(item.get("value")),
                        )
                    )
                    continue

                pending[key] = {
                    "cup": cup,
                    "item": item,
                    "created_at": time.monotonic(),
                }
                log(
                    "agendado cupom=%s item=%s qtd=%s valor_unit=%.2f "
                    "total=%.2f delay=%.1fs"
                    % (
                        cup.get("number", ""),
                        item.get("desc", ""),
                        item.get("qty", ""),
                        bot.item_unit_value(item),
                        bot.money(item.get("value")),
                        DELAY_SECONDS,
                    )
                )

            ready_keys = [
                key
                for key, row in pending.items()
                if time.monotonic() - row["created_at"] >= DELAY_SECONDS
            ]
            for key in ready_keys:
                row = pending.pop(key)
                cup = row["cup"]
                item = row["item"]
                started = time.monotonic()
                log(
                    "auditando cupom=%s item=%s qtd=%s valor_unit=%.2f "
                    "total=%.2f"
                    % (
                        cup.get("number", ""),
                        item.get("desc", ""),
                        item.get("qty", ""),
                        bot.item_unit_value(item),
                        bot.money(item.get("value")),
                    )
                )
                response = audit_exact_item(args, cup, item)
                elapsed = time.monotonic() - started
                send = should_send_response(response)
                log("auditado em %.2fs enviar=%s" % (elapsed, send))
                if send:
                    send_alert(args, response, elapsed)

            time.sleep(POLL_SECONDS)
        except Exception as exc:
            log("erro: %s: %s" % (type(exc).__name__, exc))
            time.sleep(5)


if __name__ == "__main__":
    main()
