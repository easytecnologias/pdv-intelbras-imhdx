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
SEARCH_PAGE_SIZE = 10
PRODUCT_CATEGORIES = [
    "bebida",
    "biscoito",
    "carne",
    "hortifruti",
    "higiene",
    "limpeza",
    "mercearia",
    "sacola",
    "sem_produto",
    "imagem_ruim",
]


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
    parser.add_argument("--photo-frame-offset", type=int, default=int(os.environ.get("PHOTO_FRAME_OFFSET", "3")))
    parser.add_argument("--ffmpegthumbnailer", default=os.environ.get("FFMPEGTHUMBNAILER", "ffmpegthumbnailer"))
    parser.add_argument("--product-learning-dir", default=os.environ.get("PRODUCT_LEARNING_DIR", "/var/log/pdv-product-learning"))
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


def send_message(args, text, reply_markup=None):
    api(
        args,
        "sendMessage",
        data={
            "chat_id": args.chat_id,
            "text": text[:3900],
            "reply_markup": json.dumps(reply_markup or main_keyboard()),
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


def edit_message(args, chat_id, message_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:3900],
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    api(
        args,
        "editMessageText",
        data=data,
    )


def delete_message(args, chat_id, message_id):
    api(args, "deleteMessage", data={"chat_id": chat_id, "message_id": message_id})


def answer_callback(args, callback_id, text=""):
    api(args, "answerCallbackQuery", data={"callback_query_id": callback_id, "text": text[:180]})


def send_photo(args, image_path, caption, reply_markup=None):
    with open(str(image_path), "rb") as photo:
        api(
            args,
            "sendPhoto",
            data={
                "chat_id": args.chat_id,
                "caption": caption[:1024],
                "reply_markup": json.dumps(reply_markup or main_keyboard()),
            },
            files={"photo": photo},
        )


def send_response(args, response):
    if isinstance(response, dict) and response.get("photo"):
        send_photo(args, response["photo"], response.get("caption", ""), response.get("reply_markup"))
        if response.get("question"):
            send_message(args, response["question"])
    elif isinstance(response, dict):
        send_message(args, response.get("text", "Sem resposta."), response.get("reply_markup"))
        if response.get("next_teaching"):
            time.sleep(1)
            send_response(args, next_unknown_product(args, args.chat_id))
    else:
        send_message(args, str(response))


def main_keyboard():
    return {
        "keyboard": [
            [{"text": "Status"}, {"text": "Data"}],
            [{"text": "Caixa"}, {"text": "Cupom"}],
            [{"text": "Ultimo cupom"}, {"text": "Buscar produto"}],
            [{"text": "Foto produto"}, {"text": "Produto mais vendido"}],
            [{"text": "🧠 IA — O que aprendi"}],
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


def qty_number(value):
    try:
        return float(str(value or "0").strip().replace(",", "."))
    except Exception:
        return 0.0


def qty_br(value):
    if abs(value - int(value)) < 0.0001:
        return str(int(value))
    text = "{:,.3f}".format(value).rstrip("0").rstrip(".")
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


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


def product_search_state_file(args, chat_id):
    return Path(args.state_dir) / ("product_search_%s.json" % chat_id)


def product_learning_dir(args):
    path = Path(args.product_learning_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def product_knowledge_file(args):
    return product_learning_dir(args) / "products.json"


def product_labels_file(args):
    return product_learning_dir(args) / "labels.jsonl"


def pending_product_question_file(args, chat_id):
    return product_learning_dir(args) / ("pending_%s.json" % chat_id)


def teaching_state_file(args, chat_id):
    return product_learning_dir(args) / ("teaching_%s.json" % chat_id)


def load_product_knowledge(args):
    path = product_knowledge_file(args)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_product_knowledge(args, data):
    product_knowledge_file(args).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def product_is_known(args, code):
    if not code:
        return False
    item = load_product_knowledge(args).get(str(code), {})
    return item.get("status") == "conhecido" and item.get("labels_confirmados")


def known_product_category(args, code):
    if not code:
        return ""
    item = load_product_knowledge(args).get(str(code), {})
    return str(item.get("categoria") or "")


def save_pending_product_question(args, chat_id, payload):
    path = pending_product_question_file(args, chat_id)
    payload["created"] = int(time.time())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def pop_pending_product_question(args, chat_id):
    path = pending_product_question_file(args, chat_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = None
    try:
        path.unlink()
    except Exception:
        pass
    if not data or time.time() - int(data.get("created", 0)) > 3600:
        return None
    return data


def append_product_label(args, payload):
    path = product_labels_file(args)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def category_keyboard():
    rows = [
        [{"text": "Bebida", "callback_data": "learncat:bebida"}, {"text": "Biscoito", "callback_data": "learncat:biscoito"}],
        [{"text": "Carne", "callback_data": "learncat:carne"}, {"text": "Hortifruti", "callback_data": "learncat:hortifruti"}],
        [{"text": "Higiene", "callback_data": "learncat:higiene"}, {"text": "Limpeza", "callback_data": "learncat:limpeza"}],
        [{"text": "Mercearia", "callback_data": "learncat:mercearia"}, {"text": "Sacola", "callback_data": "learncat:sacola"}],
        [{"text": "Nao aparece", "callback_data": "learncat:sem_produto"}, {"text": "Imagem ruim", "callback_data": "learncat:imagem_ruim"}],
    ]
    return {"inline_keyboard": rows}


def normalize_product_category(text):
    clean = normalize_text(text).lower()
    mapping = {
        "refrigerante": "bebida",
        "guarana": "bebida",
        "coca": "bebida",
        "agua": "bebida",
        "suco": "bebida",
        "bebida": "bebida",
        "biscoito": "biscoito",
        "bolacha": "biscoito",
        "wafer": "biscoito",
        "carne": "carne",
        "frango": "carne",
        "bisteca": "carne",
        "suina": "carne",
        "limao": "hortifruti",
        "manga": "hortifruti",
        "laranja": "hortifruti",
        "banana": "hortifruti",
        "hortifruti": "hortifruti",
        "barb": "higiene",
        "sabonete": "higiene",
        "palmolive": "higiene",
        "colgate": "higiene",
        "higiene": "higiene",
        "limpeza": "limpeza",
        "detergente": "limpeza",
        "sabao": "limpeza",
        "colorifico": "mercearia",
        "arroz": "mercearia",
        "feijao": "mercearia",
        "macarrao": "mercearia",
        "requeijao": "mercearia",
        "palito": "mercearia",
        "mercearia": "mercearia",
        "sacola": "sacola",
    }
    if clean in PRODUCT_CATEGORIES:
        return clean
    for word, category in mapping.items():
        if word in clean:
            return category
    if is_negative_product_answer(text):
        return "sem_produto"
    return ""


def save_teaching_state(args, chat_id, payload):
    path = teaching_state_file(args, chat_id)
    payload["updated"] = int(time.time())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_teaching_state(args, chat_id):
    path = teaching_state_file(args, chat_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def clear_teaching_state(args, chat_id):
    try:
        teaching_state_file(args, chat_id).unlink()
    except Exception:
        pass


def parse_human_product_labels(text):
    clean = normalize_text(text).lower()
    remove_words = [
        "isso e",
        "isso eh",
        "e um",
        "e uma",
        "nessa imagem",
        "na imagem",
        "tem",
        "itens",
        "item",
        "outro",
        "outra",
    ]
    for word in remove_words:
        clean = clean.replace(word, " ")
    clean = re.sub(r"\bum\b", " ", clean)
    clean = re.sub(r"\buma\b", " ", clean)
    clean = re.sub(r"\be\b", ",", clean)
    clean = re.sub(r"\b\d+\b", " ", clean)
    clean = clean.replace(";", ",")
    clean = clean.replace("|", ",")
    labels = []
    for part in clean.split(","):
        label = " ".join(part.strip(" .:-").split())
        if label and label not in labels:
            labels.append(label)
    return labels


def is_negative_product_answer(text):
    clean = normalize_text(text).lower()
    patterns = [
        "nao tem",
        "nao ta",
        "nao esta",
        "nao aparece",
        "nao vejo",
        "sem produto",
        "imagem ruim",
        "produto nao visivel",
        "produto nao aparece",
    ]
    return any(pattern in clean for pattern in patterns)


def is_menu_or_command_answer(text):
    normalized = normalize_button_text(text)
    if normalized.startswith("/"):
        return True
    menu_texts = {
        "status",
        "data",
        "caixa",
        "cupom",
        "dinheiro",
        "menu",
        "ultimo cupom",
        "buscar produto",
        "foto produto",
        "ensinar produtos",
        "produto mais vendido",
    }
    return text.strip().lower() in menu_texts


def choose_item_label(labels, desc):
    desc_norm = normalize_text(desc).lower()
    for label in labels:
        label_norm = normalize_text(label).lower()
        words = [word for word in label_norm.split() if len(word) >= 4]
        if label_norm and (label_norm in desc_norm or any(word in desc_norm for word in words)):
            return label
    return ""


def learn_product_from_answer(args, chat_id, text):
    pending = pop_pending_product_question(args, chat_id)
    if not pending:
        return ""
    if is_menu_or_command_answer(text):
        save_pending_product_question(args, chat_id, pending)
        return ""

    category = normalize_product_category(text)
    negative = category in ("sem_produto", "imagem_ruim") or is_negative_product_answer(text)
    labels = parse_human_product_labels(text)
    if not labels and not negative:
        return "Nao entendi o produto. Pode responder tipo: isso e arroz."

    code = str(pending.get("code") or "")
    desc = pending.get("desc") or ""
    item_label = "" if negative else choose_item_label(labels, desc)
    payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pdv": args.pdv_station,
        "source": "telegram_human_confirmed",
        "image": pending.get("image", ""),
        "cupom": pending.get("cupom", ""),
        "code": code,
        "descricao": desc,
        "qty": pending.get("qty", ""),
        "item_time": pending.get("item_time", ""),
        "labels": labels,
        "item_label": item_label,
        "category": category,
        "visibility": "produto_nao_visivel" if negative else "produto_visivel",
        "raw_answer": text,
    }
    append_product_label(args, payload)

    if negative:
        message = "Salvei como produto nao visivel. Nao marquei esse codigo como conhecido."
        if load_teaching_state(args, chat_id).get("active"):
            return {"text": message + "\n\nVou procurar o proximo produto.", "next_teaching": True}
        return message

    if code and category and not negative:
        knowledge = load_product_knowledge(args)
        product = knowledge.setdefault(
            code,
            {
                "descricao": desc,
                "labels_confirmados": [],
                "confirmacoes": 0,
                "status": "novo",
                "examples": [],
            },
        )
        confirmed = product.setdefault("labels_confirmados", [])
        if item_label not in confirmed:
            confirmed.append(item_label)
        product["categoria"] = category
        product["descricao"] = desc
        product["confirmacoes"] = int(product.get("confirmacoes", 0)) + 1
        product["status"] = "conhecido"
        product["last_seen"] = payload["time"]
        examples = product.setdefault("examples", [])
        if len(examples) < 20:
            examples.append({"image": pending.get("image", ""), "cupom": pending.get("cupom", ""), "label": item_label, "category": category})
        save_product_knowledge(args, knowledge)
        message = "Aprendi: %s = categoria %s. Nao vou perguntar de novo para esse codigo." % (desc.title(), category)
        if load_teaching_state(args, chat_id).get("active"):
            return {"text": message + "\n\nVou procurar o proximo produto.", "next_teaching": True}
        return message

    message = "Salvei os rotulos da imagem: %s. Ainda nao marquei o codigo como conhecido porque havia varios itens." % ", ".join(labels)
    if load_teaching_state(args, chat_id).get("active"):
        return {"text": message + "\n\nVou procurar o proximo produto.", "next_teaching": True}
    return message


def save_product_search(args, chat_id, term):
    path = product_search_state_file(args, chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"term": term, "created": int(time.time())}), encoding="utf-8")


def load_product_search(args, chat_id):
    path = product_search_state_file(args, chat_id)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - int(data.get("created", 0)) > 1800:
            return ""
        return str(data.get("term") or "")
    except Exception:
        return ""


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


def product_search_keyboard(page, pages):
    buttons = []
    nav = []
    if page > 0:
        nav.append({"text": "◀️ Anterior", "callback_data": "search:%d" % (page - 1)})
    nav.append({"text": "%d/%d" % (page + 1, pages), "callback_data": "noop"})
    if page < pages - 1:
        nav.append({"text": "Próxima ▶️", "callback_data": "search:%d" % (page + 1)})
    buttons.append(nav)
    return {"inline_keyboard": buttons}


def search_items(args, term, page=0):
    term_low = term.lower()
    cups, _, _ = read_sales(args)
    hits = []
    for cup in cups:
        for item in cup["items"]:
            if term_low in item["desc"].lower() or term_low in item["code"]:
                hits.append((cup, item))
    if not hits:
        return "🔎 Produto nao encontrado\n\n📅 %s\n📝 Busca: %s" % (
            date_label(query_date(args)),
            term,
        )
    pages = max(1, (len(hits) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    start = page * SEARCH_PAGE_SIZE
    shown = hits[start:start + SEARCH_PAGE_SIZE]
    total_value = sum(item["value"] for _, item in hits)
    lines = [
        "🔎 Buscar produto",
        "📅 %s" % date_label(query_date(args)),
        "📝 Produto: %s" % term,
        "📦 Ocorrencias: %d" % len(hits),
        "💰 Valor somado: %s" % money_br(total_value),
        "📄 Pagina: %d/%d" % (page + 1, pages),
        "",
        "🧾 Resultados",
    ]
    for cup, item in shown:
        lines.extend([
            "Cupom %s  •  %s" % (cup.get("number", "-"), item["time"]),
            "    %s" % item["desc"].title(),
            "    %s x %s  •  %s" % (item["qty"], item.get("code") or "sem codigo", money_br(item["value"])),
            "",
        ])
    return {
        "text": "\n".join(lines).strip(),
        "reply_markup": product_search_keyboard(page, pages),
    }


def top_products(args, limit=10):
    cups, _, _ = read_sales(args)
    products = {}
    for cup in cups:
        seen_in_cup = set()
        for item in cup["items"]:
            code = item.get("code") or "sem codigo"
            desc = item.get("desc") or "Produto sem descricao"
            key = (code, normalize_text(desc))
            if key not in products:
                products[key] = {
                    "code": code,
                    "desc": desc,
                    "qty": 0.0,
                    "value": 0.0,
                    "coupons": 0,
                }
            products[key]["qty"] += qty_number(item.get("qty"))
            products[key]["value"] += item.get("value", 0.0)
            if key not in seen_in_cup:
                products[key]["coupons"] += 1
                seen_in_cup.add(key)

    if not products:
        return "📦 Produto mais vendido\n\n📅 %s\nAinda nao achei itens vendidos nessa data." % date_label(query_date(args))

    ranking = sorted(
        products.values(),
        key=lambda row: (row["qty"], row["value"]),
        reverse=True,
    )[:limit]
    leader = ranking[0]
    lines = [
        "🏆 Produto mais vendido",
        "📅 %s" % date_label(query_date(args)),
        "",
        "🥇 %s" % leader["desc"].title(),
        "🔢 Codigo: %s" % leader["code"],
        "📦 Quantidade: %s" % qty_br(leader["qty"]),
        "💰 Valor vendido: %s" % money_br(leader["value"]),
        "🧾 Cupons: %d" % leader["coupons"],
        "",
        "📊 Top %d produtos" % len(ranking),
    ]
    for idx, item in enumerate(ranking, 1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "%02d." % idx
        lines.extend([
            "%s %s" % (medal, item["desc"].title()),
            "    📦 %s  •  💰 %s  •  🧾 %d cupons" % (
                qty_br(item["qty"]),
                money_br(item["value"]),
                item["coupons"],
            ),
        ])
    return "\n".join(lines)


def parse_event_time(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def item_datetime(args, item):
    dt = query_date(args)
    return datetime.strptime(dt.strftime("%Y-%m-%d") + " " + item["time"], "%Y-%m-%d %H:%M:%S")


def photo_target_datetime(args, item):
    return item_datetime(args, item) + timedelta(seconds=args.photo_frame_offset)


def find_photo_for_item(args, item, seconds=60):
    events_path = Path(args.events_file)
    if not events_path.exists():
        return None
    item_dt = photo_target_datetime(args, item)
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
    target_dt = photo_target_datetime(args, item)
    start = target_dt - timedelta(seconds=args.imhdx_window_before)
    end = target_dt + timedelta(seconds=args.imhdx_window_after)
    start_text = quote(start.strftime("%Y-%m-%d %H:%M:%S"))
    end_text = quote(end.strftime("%Y-%m-%d %H:%M:%S"))
    url = (
        "http://%s/cgi-bin/loadfile.cgi?action=startLoad&channel=%s&startTime=%s&endTime=%s"
        % (args.imhdx_host, args.imhdx_channel, start_text, end_text)
    )

    out_dir = Path(args.state_dir) / "imhdx"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = target_dt.strftime("%Y%m%d_%H%M%S")
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
                "hora": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
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
        "📸 Foto do produto\n"
        "📅 %s\n"
        "🧾 Cupom: %s\n\n"
        "📦 Produto: %s\n"
        "🔢 Codigo: %s\n"
        "⚖️ Quantidade: %s\n"
        "💰 Valor: %s\n"
        "🕒 Hora do item: %s\n\n"
        "🎥 Fonte: %s"
        % (
            date_label(query_date(args)),
            cupom,
            item["desc"].title(),
            item["code"] or "sem codigo",
            item["qty"],
            money_br(item["value"]),
            item["time"],
            event.get("fonte", "auditor local"),
        )
    )
    category = known_product_category(args, item.get("code", ""))
    if product_is_known(args, item.get("code", "")):
        if category:
            caption += "\n\nCategoria aprendida: %s" % category
        return {"photo": event["imagem"], "caption": caption}

    save_pending_product_question(
        args,
        args.chat_id,
        {
            "image": event["imagem"],
            "cupom": str(cupom),
            "code": item.get("code", ""),
            "desc": item.get("desc", ""),
            "qty": item.get("qty", ""),
            "item_time": item.get("time", ""),
            "date": query_date(args).strftime("%Y-%m-%d"),
        },
    )
    question = (
        "Esse produto ainda nao esta conhecido.\n"
        "Escolha a categoria ou responda com texto.\n\n"
        "Exemplos:\n"
        "isso e arroz\n"
        "tem coca cola, macarrao e laranja"
    )
    return {"photo": event["imagem"], "caption": caption, "question": question, "reply_markup": category_keyboard()}


def product_photo_for_item(args, cupom, item):
    event = imhdx_photo_for_item(args, cupom, item)
    if not event:
        event = find_photo_for_item(args, item)
        if event:
            try:
                event["imagem"] = overlay_pdv_caption(args, event["imagem"], cupom, item, "auditor local")
            except Exception:
                pass
    if not event:
        return None

    caption = (
        "Aprendizado de produto\n"
        "Data: %s\n"
        "Cupom: %s\n\n"
        "Produto no PDV: %s\n"
        "Codigo: %s\n"
        "Quantidade: %s\n"
        "Hora do item: %s\n\n"
        "Fonte: %s"
        % (
            date_label(query_date(args)),
            cupom,
            item["desc"].title(),
            item["code"] or "sem codigo",
            item["qty"],
            item["time"],
            event.get("fonte", "auditor local"),
        )
    )
    save_pending_product_question(
        args,
        args.chat_id,
        {
            "image": event["imagem"],
            "cupom": str(cupom),
            "code": item.get("code", ""),
            "desc": item.get("desc", ""),
            "qty": item.get("qty", ""),
            "item_time": item.get("time", ""),
            "date": query_date(args).strftime("%Y-%m-%d"),
        },
    )
    return {
        "photo": event["imagem"],
        "caption": caption,
        "question": "Escolha a categoria ou responda com texto.",
        "reply_markup": category_keyboard(),
    }


def next_unknown_product(args, chat_id):
    state = load_teaching_state(args, chat_id)
    tried = set(state.get("tried", []))
    cups, _, _ = read_sales(args)
    candidates = []
    for cup in reversed(cups):
        for item in reversed(cup.get("items", [])):
            code = item.get("code") or ""
            if not code or product_is_known(args, code) or code in tried:
                continue
            candidates.append((cup, item))

    for cup, item in candidates:
        code = item.get("code") or ""
        tried.add(code)
        save_teaching_state(args, chat_id, {"active": True, "tried": sorted(tried)})
        response = product_photo_for_item(args, cup.get("number", ""), item)
        if response:
            return response

    clear_teaching_state(args, chat_id)
    return {"text": "Nao achei mais produto desconhecido com foto para ensinar nessa data."}


def start_product_teaching(args, chat_id):
    save_teaching_state(args, chat_id, {"active": True, "tried": []})
    return next_unknown_product(args, chat_id)


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


def clean_search_term(text):
    clean = " ".join(text.strip().split())
    parsed = split_cupom_product(clean)
    if parsed:
        return parsed[1]
    parts = clean.split()
    if len(parts) > 1 and parts[0].isdigit():
        return " ".join(parts[1:])
    if len(parts) > 1 and parts[-1].isdigit():
        return " ".join(parts[:-1])
    return clean


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


# ─────────────────── IA / Aprendizado ────────────────────────────────────────

def _ia_read_handoff():
    path = Path("/var/log/pdv-learning-agent/knowledge/future_antitheft_handoff.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ia_read_shadow():
    path = Path("/var/log/pdv-shadow-antitheft/summary.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ia_read_train_history():
    path = Path("/var/log/pdv-antitheft/models/train_history.jsonl")
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    except Exception:
        pass
    return records


def _ia_count_alerts_today():
    day = datetime.now().strftime("%Y%m%d")
    path = Path("/var/log/pdv-antitheft/alerts/{}/alerts.jsonl".format(day))
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except Exception:
        return 0


def _ia_count_images_today():
    day = datetime.now().strftime("%Y%m%d")
    img_dir = Path("/var/log/pdv-learning-agent/{}/images".format(day))
    if not img_dir.exists():
        return 0
    return len(list(img_dir.glob("*.jpg")))


def ia_resumo_text():
    handoff = _ia_read_handoff()
    shadow = _ia_read_shadow()
    history = _ia_read_train_history()
    alertas_hoje = _ia_count_alerts_today()
    imagens_hoje = _ia_count_images_today()

    total = handoff.get("total_samples", 0)
    labels = handoff.get("labels", {})

    label_icons = {
        "venda_confirmada": "✅",
        "movimento_sem_evento_pdv": "👁",
        "consulta_preco": "💰",
        "pagamento": "💳",
        "cupom_aberto": "🧾",
        "ambiente": "🌫",
    }

    linhas = ["🧠 *O que o modelo aprendeu até agora:*\n"]
    linhas.append("📸 Imagens coletadas hoje: *{}*".format(imagens_hoje))
    linhas.append("📚 Total de amostras: *{}*\n".format(total))

    linhas.append("*Categorias aprendidas:*")
    for label, info in sorted(labels.items(), key=lambda x: -x[1].get("samples", 0)):
        icon = label_icons.get(label, "•")
        n = info.get("samples", 0)
        linhas.append("{} {}: *{}* amostras".format(icon, label.replace("_", " "), n))

    linhas.append("")
    obs = shadow.get("total_observations", 0)
    rev = shadow.get("review_candidates", 0)
    pct = round(rev / obs * 100, 1) if obs else 0
    linhas.append("👁 Observações analisadas: *{}*".format(obs))
    linhas.append("🔍 Suspeitos para revisão: *{}* ({}%)".format(rev, pct))
    linhas.append("🚨 Alertas hoje: *{}*".format(alertas_hoje))

    treinos = [h for h in history if h.get("action") == "trained"]
    if treinos:
        ultimo = treinos[-1]
        linhas.append("")
        linhas.append("*Último treino:*")
        linhas.append("📅 {}".format(ultimo.get("time", "?")))
        linhas.append("🎯 mAP50: *{:.3f}*".format(ultimo.get("new_map", 0)))
        linhas.append("🔢 Amostras usadas: *{}*".format(ultimo.get("total_samples", 0)))
        deployed = "✅ Sim" if ultimo.get("deployed") else "⏳ Aguardando melhora"
        linhas.append("🚀 Modelo em produção: {}".format(deployed))
    else:
        linhas.append("")
        linhas.append("⏳ Nenhum treino completo ainda (roda às 09:00)")

    return "\n".join(linhas)


def ia_alertas_text():
    day = datetime.now().strftime("%Y%m%d")
    path = Path("/var/log/pdv-antitheft/alerts/{}/alerts.jsonl".format(day))
    if not path.exists():
        return "🟢 Nenhum alerta hoje."
    try:
        records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return "Erro ao ler alertas."
    if not records:
        return "🟢 Nenhum alerta hoje."
    linhas = ["🚨 *Alertas de hoje ({} total):*\n".format(len(records))]
    for r in records[-10:]:
        hora = r.get("time", "?")[-8:]
        motivo = r.get("motivo_llava") or r.get("tipo", "?")
        dets = r.get("deteccoes") or []
        det_str = ", ".join("{} {:.0f}%".format(d["label"], d["conf"]*100) for d in dets[:2])
        linhas.append("🕐 {} — {}\n   _{}_".format(hora, motivo[:60], det_str))
    return "\n".join(linhas)


def ia_treinos_text():
    history = _ia_read_train_history()
    if not history:
        return "⏳ Nenhum treino realizado ainda.\nPrimeiro treino ocorre às 09:00 do próximo dia."
    linhas = ["📈 *Histórico de treinos:*\n"]
    for h in reversed(history[-8:]):
        if h.get("action") == "skipped":
            linhas.append("⏭ {} — pulado ({})".format(
                h.get("time", "?")[:16], h.get("reason", "")[:40]))
        elif h.get("action") == "trained":
            deployed = "✅" if h.get("deployed") else "↩️"
            linhas.append("{} {} — mAP *{:.3f}* | {} amostras".format(
                deployed, h.get("time", "?")[:16],
                h.get("new_map", 0), h.get("total_samples", 0)))
        elif h.get("action") == "failed":
            linhas.append("❌ {} — falhou em {}".format(
                h.get("time", "?")[:16], h.get("step", "?")))
    return "\n".join(linhas)


def ia_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Resumo", "callback_data": "ia:resumo"},
                {"text": "🚨 Alertas", "callback_data": "ia:alertas"},
            ],
            [
                {"text": "📈 Treinos", "callback_data": "ia:treinos"},
                {"text": "🔄 Atualizar", "callback_data": "ia:resumo"},
            ],
        ]
    }


# ─────────────────────────────────────────────────────────────────────────────


def handle_command(args, text):
    text = normalize_button_text(text)
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/ajuda", "/help", "/start", "/menu"):
        return help_text()
    if cmd in ("/ia", "/modelo", "/antifurto", "/oque aprendeu"):
        return {"text": ia_resumo_text(), "reply_markup": ia_keyboard()}
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
    if cmd in ("/maisvendido", "/maisvendidos", "/topprodutos"):
        return top_products(args)
    if cmd in ("/ensinar", "/aprendizado", "/comecar_aprendizado"):
        return start_product_teaching(args, args.chat_id)
    if cmd in ("/ultimo", "/ultimocupom"):
        return latest_coupon(args)
    if cmd == "/cupom":
        return cupom_detail(args, rest) if rest else "Digite o numero do cupom. Exemplo: 216530"
    if cmd in ("/buscar", "/produto"):
        term = clean_search_term(rest)
        return search_items(args, term) if term else "Digite assim: /buscar bombom\nOu toque em Buscar produto e depois envie o nome."
    if cmd in ("/foto", "/imagem", "/print"):
        parsed = split_cupom_product(rest)
        if not parsed:
            return "Use: /foto 216657 arroz\nOu: arroz 216657"
        return product_photo(args, parsed[0], parsed[1])
    return ""


def normalize_button_text(text):
    clean = " ".join(text.strip().split())
    mapping = {
        "status": "/status",
        "data": "/data",
        "caixa": "/caixa",
        "cupom": "/cupom",
        "dinheiro": "/dinheiro",
        "ajuda": "/ajuda",
        "menu": "/menu",
        "ultimo cupom": "/ultimo",
        "buscar produto": "/buscar",
        "foto produto": "/foto",
        "ensinar produtos": "/ensinar",
        "produto mais vendido": "/maisvendido",
        "ia": "/ia",
        "modelo": "/ia",
        "antifurto": "/ia",
        "o que aprendeu": "/ia",
        "🧠 ia — o que aprendi": "/ia",
        "ia — o que aprendi": "/ia",
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
    if data.startswith("search:"):
        term = load_product_search(args, chat_id)
        if not term:
            answer_callback(args, callback_id, "Busca expirada. Toque em Buscar produto novamente.")
            return
        page = int(data.split(":", 1)[1])
        result = search_items(args, term, page)
        if isinstance(result, dict):
            edit_message(args, chat_id, message_id, result["text"], result.get("reply_markup"))
            answer_callback(args, callback_id)
        else:
            edit_message(args, chat_id, message_id, result)
            answer_callback(args, callback_id)
        return
    if data.startswith("learncat:"):
        category = data.split(":", 1)[1]
        if category not in PRODUCT_CATEGORIES:
            answer_callback(args, callback_id, "Categoria invalida.")
            return
        answer_callback(args, callback_id, category)
        result = learn_product_from_answer(args, chat_id, category)
        if result:
            send_response(args, result)
        return
    if data.startswith("ia:"):
        acao = data.split(":", 1)[1]
        if acao == "resumo":
            text = ia_resumo_text()
        elif acao == "alertas":
            text = ia_alertas_text()
        elif acao == "treinos":
            text = ia_treinos_text()
        else:
            text = ia_resumo_text()
        edit_message(args, chat_id, message_id, text, ia_keyboard())
        answer_callback(args, callback_id)
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
                    learned = ""
                    if not text.startswith("/"):
                        learned = learn_product_from_answer(args, chat.get("id"), text)
                    if learned:
                        answer = learned
                    elif normalized == "/buscar":
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
                            term = clean_search_term(text)
                            save_product_search(args, chat.get("id"), term)
                            answer = search_items(args, term)
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
                            if normalized.startswith("/buscar ") or normalized.startswith("/produto "):
                                save_product_search(args, chat.get("id"), clean_search_term(normalized.split(maxsplit=1)[1]))
                            answer = handle_command(args, text)
                    else:
                        if normalized.startswith("/buscar ") or normalized.startswith("/produto "):
                            save_product_search(args, chat.get("id"), clean_search_term(normalized.split(maxsplit=1)[1]))
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
