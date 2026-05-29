#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from collections import deque
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw
from requests.auth import HTTPDigestAuth


SPY_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2}):(?P<text>.+)$")
REFUND_RE = re.compile(r"\b(cancel|cancela|cancelamento|estorno|devolucao|devolu..o)\b", re.IGNORECASE)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-host", default=os.environ.get("CAMERA_HOST", "10.10.10.20"))
    parser.add_argument("--camera-user", default=os.environ.get("CAMERA_USER", ""))
    parser.add_argument("--camera-pass", default=os.environ.get("CAMERA_PASS", ""))
    parser.add_argument("--pdv-station", default=os.environ.get("PDV_STATION", "001"))
    parser.add_argument("--pdv-base-dir", default=os.environ.get("PDV_BASE_DIR", "/home/rpdv/frente"))
    parser.add_argument("--roi", default=os.environ.get("AUDITOR_ROI", "382,172,474,302"))
    parser.add_argument("--outdir", default=os.environ.get("AUDITOR_OUTDIR", "/var/log/pdv-camera-auditor"))
    parser.add_argument("--duration", type=int, default=int(os.environ.get("AUDITOR_DURATION", "0")))
    parser.add_argument("--spy-tail", type=int, default=int(os.environ.get("AUDITOR_SPY_TAIL", "350")))
    parser.add_argument("--threshold", type=float, default=float(os.environ.get("AUDITOR_THRESHOLD", "9.0")))
    parser.add_argument("--virtual-lines", default=os.environ.get("AUDITOR_VIRTUAL_LINES", "entrada:335,354,486,354;scanner:330,223,453,223;saida:317,106,450,106"))
    parser.add_argument("--virtual-line-threshold", type=float, default=float(os.environ.get("AUDITOR_VIRTUAL_LINE_THRESHOLD", "18.0")))
    parser.add_argument("--virtual-line-band", type=int, default=int(os.environ.get("AUDITOR_VIRTUAL_LINE_BAND", "14")))
    parser.add_argument("--virtual-line-window", type=float, default=float(os.environ.get("AUDITOR_VIRTUAL_LINE_WINDOW", "5.0")))
    parser.add_argument("--virtual-line-cooldown", type=float, default=float(os.environ.get("AUDITOR_VIRTUAL_LINE_COOLDOWN", "0.8")))
    parser.add_argument("--virtual-scanner-pulse-cooldown", type=float, default=float(os.environ.get("AUDITOR_VIRTUAL_SCANNER_PULSE_COOLDOWN", "0.35")))
    parser.add_argument("--window", type=float, default=float(os.environ.get("AUDITOR_WINDOW", "5.0")))
    parser.add_argument("--item-before-window", type=float, default=float(os.environ.get("AUDITOR_ITEM_BEFORE", "20.0")))
    parser.add_argument("--item-after-window", type=float, default=float(os.environ.get("AUDITOR_ITEM_AFTER", "35.0")))
    parser.add_argument("--match-delay", type=float, default=float(os.environ.get("AUDITOR_MATCH_DELAY", "4.0")))
    parser.add_argument("--pending-suspect-delay", type=float, default=float(os.environ.get("AUDITOR_PENDING_DELAY", "30.0")))
    parser.add_argument("--suspect-cooldown", type=float, default=float(os.environ.get("AUDITOR_SUSPECT_COOLDOWN", "90.0")))
    parser.add_argument("--suspect-min-score", type=float, default=float(os.environ.get("AUDITOR_SUSPECT_MIN_SCORE", "24.0")))
    parser.add_argument("--suspect-min-moves", type=int, default=int(os.environ.get("AUDITOR_SUSPECT_MIN_MOVES", "4")))
    parser.add_argument("--suspect-min-duration", type=float, default=float(os.environ.get("AUDITOR_SUSPECT_MIN_DURATION", "3.0")))
    parser.add_argument("--skin-ignore-ratio", type=float, default=float(os.environ.get("AUDITOR_SKIN_IGNORE_RATIO", "0.12")))
    parser.add_argument("--consultation-window", type=float, default=float(os.environ.get("AUDITOR_CONSULTATION_WINDOW", "45.0")))
    parser.add_argument("--consultation-suspect-delay", type=float, default=float(os.environ.get("AUDITOR_CONSULTATION_SUSPECT_DELAY", "45.0")))
    parser.add_argument("--cluster-gap", type=float, default=float(os.environ.get("AUDITOR_CLUSTER_GAP", "3.0")))
    parser.add_argument("--max-cluster", type=float, default=float(os.environ.get("AUDITOR_MAX_CLUSTER", "7.0")))
    parser.add_argument("--post-item-ignore", type=float, default=float(os.environ.get("AUDITOR_POST_ITEM_IGNORE", "8.0")))
    parser.add_argument("--post-payment-ignore", type=float, default=float(os.environ.get("AUDITOR_POST_PAYMENT_IGNORE", "12.0")))
    parser.add_argument("--open-coupon-base-timeout", type=float, default=float(os.environ.get("AUDITOR_OPEN_COUPON_BASE_TIMEOUT", "60.0")))
    parser.add_argument("--open-coupon-item-seconds", type=float, default=float(os.environ.get("AUDITOR_OPEN_COUPON_ITEM_SECONDS", "8.0")))
    parser.add_argument("--open-coupon-max-timeout", type=float, default=float(os.environ.get("AUDITOR_OPEN_COUPON_MAX_TIMEOUT", "360.0")))
    parser.add_argument("--open-coupon-idle-timeout", type=float, default=float(os.environ.get("AUDITOR_OPEN_COUPON_IDLE_TIMEOUT", "60.0")))
    parser.add_argument("--ai-enabled", default=os.environ.get("AUDITOR_AI_ENABLED", "0"))
    parser.add_argument("--ai-model", default=os.environ.get("AUDITOR_AI_MODEL", "yolov8n.pt"))
    parser.add_argument("--ai-device", default=os.environ.get("AUDITOR_AI_DEVICE", "cpu"))
    parser.add_argument("--ai-conf", type=float, default=float(os.environ.get("AUDITOR_AI_CONF", "0.35")))
    parser.add_argument("--ai-imgsz", type=int, default=int(os.environ.get("AUDITOR_AI_IMGSZ", "640")))
    parser.add_argument("--ai-every-n", type=int, default=int(os.environ.get("AUDITOR_AI_EVERY_N", "3")))
    parser.add_argument("--telegram-token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--telegram-send-types", default=os.environ.get("TELEGRAM_SEND_TYPES", "suspeita"))
    args = parser.parse_args()
    if not args.camera_user or not args.camera_pass:
        raise SystemExit("camera user/pass ausentes")
    return args


def today_spy_path(args):
    name = "Espiao%s.%s" % (datetime.now().strftime("%d%m%y"), args.pdv_station)
    return Path(args.pdv_base_dir) / "Cm" / name


def field_value(text, name):
    match = re.search(r"\b%s:\s*([^|]+)" % re.escape(name), text)
    return match.group(1).strip() if match else ""


def spy_event_kind(text):
    if text.startswith("CSP |"):
        return "consultation"
    if text.startswith("VIT |"):
        return "item"
    if text.startswith("ABRECUPOM |"):
        return "start"
    if text.startswith("FIN |") and is_refund_text(text):
        return "refund"
    if text.startswith("FECHACUPOM |") or text.startswith("FIN |"):
        return "payment"
    return ""


def is_refund_text(text):
    return bool(REFUND_RE.search(text))


def normalize_spy_text(kind, text):
    if kind == "consultation":
        parts = ["CONSULTA"]
        for name in ("Cod", "Descricao"):
            value = field_value(text, name)
            if value:
                parts.append(value)
        value = field_value(text, "VlUnit")
        if value:
            parts.append("R$ %s" % value)
        return " | ".join(parts)
    if kind == "start":
        return "ABRECUPOM"
    if kind == "payment":
        return "FECHACUPOM"
    if kind == "refund":
        parts = ["CANCELAMENTO_ESTORNO"]
        for name in ("Cod", "Descricao"):
            value = field_value(text, name)
            if value:
                parts.append(value)
        value = field_value(text, "VlTotal") or field_value(text, "VlUnit")
        if value:
            parts.append("R$ %s" % value)
        return " | ".join(parts)
    return text


def event_kind(text):
    if text.startswith("CONSULTA"):
        return "consultation"
    if text == "ABRECUPOM":
        return "start"
    if text == "FECHACUPOM":
        return "payment"
    if text.startswith("CANCELAMENTO_ESTORNO"):
        return "refund"
    return "item"


def get_spy_events(args):
    path = today_spy_path(args)
    if not path.exists():
        return []
    lines = path.read_text(errors="replace").splitlines()[-args.spy_tail :]
    today = datetime.now().date()
    events = []
    for line in lines:
        match = SPY_RE.match(line.strip())
        if not match:
            continue
        raw_text = match.group("text").strip()
        kind = spy_event_kind(raw_text)
        if not kind:
            continue
        event_time = datetime.strptime(match.group("time"), "%H:%M:%S").time()
        events.append((datetime.combine(today, event_time), normalize_spy_text(kind, raw_text)))
    return events


def snapshot(args):
    url = "http://%s/cgi-bin/snapshot.cgi?channel=1&type=0" % args.camera_host
    response = requests.get(url, auth=HTTPDigestAuth(args.camera_user, args.camera_pass), timeout=5)
    if response.status_code != 200 or response.content[:2] != b"\xff\xd8":
        raise RuntimeError("snapshot falhou: HTTP %s" % response.status_code)
    return response.content


def motion_image(jpeg, roi):
    image = Image.open(BytesIO(jpeg)).convert("RGB")
    crop_rgb = image.crop(roi).resize((46, 65))
    crop = crop_rgb.convert("L")
    return image, list(crop.getdata()), skin_ratio(crop_rgb)


def skin_ratio(image):
    pixels = list(image.getdata())
    if not pixels:
        return 0.0
    skin = 0
    for r, g, b in pixels:
        if (
            r > 80
            and g > 35
            and b > 20
            and r > g
            and r > b
            and abs(r - g) > 12
            and max(r, g, b) - min(r, g, b) > 25
        ):
            skin += 1
    return skin / float(len(pixels))


def motion_score(previous, current):
    if previous is None:
        return 0.0
    total = sum(abs(a - b) for a, b in zip(previous, current))
    return total / float(len(current))


def parse_virtual_lines(text):
    lines = []
    for raw in text.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        name, coords = raw.split(":", 1)
        x1, y1, x2, y2 = [int(value.strip()) for value in coords.split(",")]
        lines.append({"name": name.strip().lower(), "coords": (x1, y1, x2, y2)})
    return lines


def line_motion_data(image, line, band):
    x1, y1, x2, y2 = line["coords"]
    left = max(0, min(x1, x2) - band)
    top = max(0, min(y1, y2) - band)
    right = min(image.width, max(x1, x2) + band)
    bottom = min(image.height, max(y1, y2) + band)
    if right <= left or bottom <= top:
        return []
    crop = image.crop((left, top, right, bottom)).resize((80, 16)).convert("L")
    return list(crop.getdata())


def virtual_line_hits(previous_lines, current_lines, threshold):
    hits = []
    for name, current in current_lines.items():
        score = motion_score(previous_lines.get(name), current) if previous_lines else 0.0
        if score >= threshold:
            hits.append((name, score))
    return hits


def save_evidence(image, roi, path):
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = roi
    for offset in range(3):
        draw.rectangle((x1 - offset, y1 - offset, x2 + offset, y2 + offset), outline=(255, 255, 0))
    image.save(str(path), "JPEG", quality=88)


def save_virtual_line_evidence(image, lines, path):
    draw = ImageDraw.Draw(image)
    colors = {
        "entrada": (0, 190, 255),
        "scanner": (0, 220, 80),
        "saida": (180, 80, 190),
    }
    for line in lines:
        color = colors.get(line["name"], (255, 255, 255))
        x1, y1, x2, y2 = line["coords"]
        for offset in range(-2, 3):
            draw.line((x1, y1 + offset, x2, y2 + offset), fill=color)
        draw.text((x1, max(0, y1 - 18)), line["name"].upper(), fill=color)
    image.save(str(path), "JPEG", quality=88)


def save_current_evidence(args, roi, evidences, prefix, virtual_lines=None, ai_runtime=None):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = evidences / ("%s_%s.jpg" % (prefix, stamp))
    image = Image.open(BytesIO(snapshot(args))).convert("RGB")
    if ai_runtime:
        ai_summary, ai_tracks = analyze_ai_frame(ai_runtime, image, virtual_lines or [], roi, 0, True)
        save_ai_or_regular_evidence(image, roi, virtual_lines or [], image_path, ai_summary, ai_tracks)
    else:
        save_evidence(image, roi, image_path)
    return image_path


def items_near(events, start, end, before_seconds, after_seconds):
    before = start - timedelta(seconds=before_seconds)
    after = end + timedelta(seconds=after_seconds)
    return [(ts, text) for ts, text in events if before <= ts <= after and event_kind(text) == "item"]


def item_visual_quantity(text):
    unit = field_value(text, "Und").strip().upper()
    raw_qty = field_value(text, "Quant").replace(",", ".")
    try:
        qty = float(raw_qty)
    except ValueError:
        qty = 1.0
    if unit in {"KG", "KILO", "KILOS"}:
        return 1
    return max(1, int(round(qty)))


def item_is_weighted(text):
    return field_value(text, "Und").strip().upper() in {"KG", "KILO", "KILOS"}


def items_have_weighted(items):
    return any(item_is_weighted(text) for _, text in items)


def items_visual_quantity(items):
    return sum(item_visual_quantity(text) for _, text in items)


def typed_near(events, start, end, seconds, kind):
    before = start - timedelta(seconds=seconds)
    after = end + timedelta(seconds=seconds)
    return [(ts, text) for ts, text in events if before <= ts <= after and event_kind(text) == kind]


def activity_near(events, start, end, seconds):
    before = start - timedelta(seconds=seconds)
    after = end + timedelta(seconds=seconds)
    return [(ts, text) for ts, text in events if before <= ts <= after]


def should_send_telegram(args, status):
    allowed = {item.strip().lower() for item in args.telegram_send_types.split(",") if item.strip()}
    return status.lower() in allowed or "todos" in allowed or "all" in allowed


def alert_title(payload):
    subtype = payload.get("subtipo") or payload.get("tipo") or "suspeita"
    labels = {
        "quantidade_visual_maior": "Quantidade visual maior que o PDV",
        "linhas_virtuais_sem_item": "Passagem visual sem item",
        "consulta_sem_venda": "Consulta sem venda",
        "movimento_sem_item": "Movimento sem item",
        "cancelamento_estorno": "Cancelamento/estorno",
        "cupom_aberto_tempo_demais": "Cupom aberto tempo demais",
    }
    return labels.get(subtype, subtype.replace("_", " ").title())


def compact_item_text(text):
    if "VIT |" in text:
        text = text[text.find("VIT |") :]
    code = field_value(text, "Cod")
    desc = field_value(text, "Descricao")
    qty = field_value(text, "Quant")
    unit = field_value(text, "Und")
    total = field_value(text, "VlTotal") or field_value(text, "VlUnit")
    parts = []
    if desc:
        parts.append(desc)
    if code:
        parts.append("Cod: %s" % code)
    if qty:
        parts.append("Qtd: %s%s" % (qty, " %s" % unit if unit else ""))
    if total:
        parts.append("Valor: R$ %s" % total)
    return "\n".join(parts) if parts else text[:220]


def telegram_caption(payload):
    lines = [
        "🚨 ALERTA ANTIFRAUDE",
        "🏪 PDV: %s" % payload.get("pdv", ""),
        "⚠️ Tipo: %s" % alert_title(payload),
        "🕒 Início: %s" % payload.get("hora", ""),
    ]
    if payload.get("fim") and payload.get("fim") != payload.get("hora"):
        lines.append("🏁 Fim: %s" % payload.get("fim"))
    if payload.get("origem"):
        lines.append("🎥 Origem: %s" % payload.get("origem"))
    if payload.get("quantidade_visual"):
        lines.append("👁️ Quantidade visual: %s" % payload.get("quantidade_visual"))
    if payload.get("quantidade_pdv"):
        lines.append("🧾 Quantidade no PDV: %s" % payload.get("quantidade_pdv"))
    if payload.get("score"):
        lines.append("📊 Score: %s" % payload.get("score"))
    if payload.get("movimentos"):
        lines.append("🔁 Movimentos: %s" % payload.get("movimentos"))
    reason = str(payload.get("motivo") or "")
    if "VIT |" in reason:
        lines.append("🛒 Item no PDV:")
        lines.append(compact_item_text(reason))
    elif reason:
        lines.append("📌 Motivo: %s" % reason[:260])
    lines.append("✅ Ação: conferir cupom e vídeo.")
    return "\n".join(lines)[:1024]


def send_telegram_photo(args, image_path, caption):
    if not args.telegram_token or not args.telegram_chat_id:
        return
    url = "https://api.telegram.org/bot%s/sendPhoto" % args.telegram_token
    with open(str(image_path), "rb") as photo:
        response = requests.post(
            url,
            data={"chat_id": args.telegram_chat_id, "caption": caption[:1024]},
            files={"photo": photo},
            timeout=15,
        )
    if response.status_code != 200:
        raise RuntimeError("telegram HTTP %s: %s" % (response.status_code, response.text[:200]))


def write_event(events_file, payload):
    with events_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "sim", "on"}


def ai_empty(enabled=False, reason="ai desativada"):
    try:
        from fraud_decision_ai import empty_ai_summary

        return empty_ai_summary(enabled, reason)
    except Exception:
        return {
            "ai_enabled": bool(enabled),
            "objects_detected": 0,
            "product_detected": False,
            "hand_detected": False,
            "person_detected": False,
            "basket_detected": False,
            "scanner_interaction": False,
            "track_id": "",
            "ai_confidence": 0.0,
            "ai_reason": reason,
        }


def init_ai_runtime(args):
    if not truthy(args.ai_enabled):
        return None
    try:
        from ai_detector import YoloObjectDetector
        from ai_tracker import SimpleObjectTracker

        runtime = {
            "detector": YoloObjectDetector(args.ai_model, args.ai_device, args.ai_conf, args.ai_imgsz),
            "tracker": SimpleObjectTracker(),
            "tracks": [],
            "summary": ai_empty(True, "aguardando frame"),
            "failed": False,
        }
        print("AI_INICIO", args.ai_model, args.ai_device, "conf=", args.ai_conf, flush=True)
        return runtime
    except Exception as exc:
        print("AI_FALLBACK", type(exc).__name__, exc, flush=True)
        return None


def analyze_ai_frame(ai_runtime, image, virtual_lines, roi, frame_index, force=False):
    if not ai_runtime or ai_runtime.get("failed"):
        return ai_empty(False), []
    every_n = max(1, int(ai_runtime.get("every_n", 1)))
    if not force and frame_index % every_n != 0:
        return ai_runtime.get("summary", ai_empty(True, "frame reaproveitado")), ai_runtime.get("tracks", [])
    try:
        from fraud_decision_ai import build_ai_context

        detections = ai_runtime["detector"].detect(image)
        tracks = ai_runtime["tracker"].update(detections)
        summary = build_ai_context(tracks, virtual_lines, roi)
        ai_runtime["tracks"] = tracks
        ai_runtime["summary"] = summary
        return summary, tracks
    except Exception as exc:
        ai_runtime["failed"] = True
        print("AI_FALLBACK", type(exc).__name__, exc, flush=True)
        return ai_empty(False, "ai falhou: %s" % type(exc).__name__), []


def add_ai_to_payload(payload, ai_summary):
    try:
        from fraud_decision_ai import add_ai_payload

        return add_ai_payload(payload, ai_summary)
    except Exception:
        payload.update(ai_empty(False))
        return payload


def ai_should_ignore(subtype, ai_summary):
    try:
        from fraud_decision_ai import should_ignore_without_product

        return should_ignore_without_product(subtype, ai_summary)
    except Exception:
        return False


def save_ai_or_regular_evidence(image, roi, lines, path, ai_summary, ai_tracks, line_mode=False):
    if ai_summary and ai_summary.get("ai_enabled"):
        try:
            from evidence_drawer import save_ai_evidence

            save_ai_evidence(image, lines, roi, ai_tracks, ai_summary, path)
            return
        except Exception as exc:
            print("AI_EVIDENCIA_ERRO", type(exc).__name__, exc, flush=True)
    if line_mode:
        save_virtual_line_evidence(image, lines, path)
    else:
        save_evidence(image, roi, path)


def main():
    args = parse_args()
    roi = tuple(int(v.strip()) for v in args.roi.split(","))
    virtual_lines = parse_virtual_lines(args.virtual_lines)
    outdir = Path(args.outdir)
    evidences = outdir / "evidencias"
    evidences.mkdir(parents=True, exist_ok=True)
    events_file = outdir / "events.jsonl"
    ai_runtime = init_ai_runtime(args)
    if ai_runtime:
        ai_runtime["every_n"] = max(1, args.ai_every_n)

    previous = None
    previous_lines = {}
    virtual_flow = None
    last_line_hit = {}
    last_motion = datetime.min
    ignore_until = datetime.min
    open_cluster = None
    pending = deque()
    events = []
    seen = set()
    cupom_open = None
    cupom_open_at = None
    cupom_last_activity_at = None
    cupom_item_count = 0
    cupom_open_alerted = False
    suspect_ignore_until = datetime.min
    start = datetime.now()
    last_spy = start - timedelta(seconds=20)
    frame_index = 0
    ai_summary = ai_empty(False)
    ai_tracks = []

    print("AUDITOR_INICIO", start.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    print("ROI", roi, flush=True)
    print("LINHAS_VIRTUAIS", args.virtual_lines, flush=True)
    print("EVENTOS", events_file, flush=True)

    while args.duration <= 0 or (datetime.now() - start).total_seconds() < args.duration:
        now = datetime.now()

        if (now - last_spy).total_seconds() >= 2:
            try:
                for ts, text in get_spy_events(args):
                    if ts < last_spy - timedelta(seconds=2):
                        continue
                    key = (ts, text)
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(key)
                    kind = event_kind(text)
                    if kind == "start":
                        cupom_open = True
                        cupom_open_at = ts
                        cupom_last_activity_at = ts
                        cupom_item_count = 0
                        cupom_open_alerted = False
                    elif kind in {"item", "consultation"}:
                        cupom_open = True
                        cupom_last_activity_at = ts
                        if kind == "item":
                            cupom_item_count += 1
                    elif kind in {"payment", "refund"}:
                        cupom_open = False
                        cupom_open_at = None
                        cupom_last_activity_at = None
                        cupom_item_count = 0
                        cupom_open_alerted = False
                    print(kind.upper(), ts.strftime("%H:%M:%S"), text, flush=True)
                    if kind == "refund":
                        image_path = ""
                        try:
                            image_path = str(save_current_evidence(args, roi, evidences, "pdv%s_cancelamento" % args.pdv_station, virtual_lines, ai_runtime))
                        except Exception as exc:
                            print("CANCELAMENTO_FOTO_ERRO", type(exc).__name__, exc, flush=True)
                        payload = {
                            "hora": ts.strftime("%Y-%m-%d %H:%M:%S"),
                            "fim": ts.strftime("%Y-%m-%d %H:%M:%S"),
                            "pdv": args.pdv_station,
                            "tipo": "suspeita",
                            "subtipo": "cancelamento_estorno",
                            "score": 0,
                            "skin": 0,
                            "movimentos": 0,
                            "motivo": text,
                            "imagem": image_path,
                        }
                        add_ai_to_payload(payload, ai_summary)
                        write_event(events_file, payload)
                        print("CANCELAMENTO_ESTORNO", ts.strftime("%H:%M:%S"), text, image_path, flush=True)
                        if image_path and should_send_telegram(args, "suspeita"):
                            try:
                                send_telegram_photo(args, image_path, telegram_caption(payload))
                                print("TELEGRAM_ENVIADO", "cancelamento_estorno", image_path, flush=True)
                            except Exception as exc:
                                print("TELEGRAM_ERRO", type(exc).__name__, exc, flush=True)
                last_spy = now
            except Exception as exc:
                print("ESPIAO_ERRO", type(exc).__name__, exc, flush=True)

        if (
            cupom_open
            and cupom_open_at
            and cupom_last_activity_at
            and not cupom_open_alerted
            and (now - cupom_open_at).total_seconds()
            >= min(
                args.open_coupon_max_timeout,
                args.open_coupon_base_timeout + (cupom_item_count * args.open_coupon_item_seconds),
            )
            and (now - cupom_last_activity_at).total_seconds() >= args.open_coupon_idle_timeout
        ):
            image_path = ""
            try:
                image_path = str(save_current_evidence(args, roi, evidences, "pdv%s_cupom_aberto" % args.pdv_station, virtual_lines, ai_runtime))
            except Exception as exc:
                print("CUPOM_ABERTO_FOTO_ERRO", type(exc).__name__, exc, flush=True)
            reason = (
                "cupom aberto ha %.0f segundos sem fechamento; parado ha %.0f segundos; itens=%d"
                % (
                    (now - cupom_open_at).total_seconds(),
                    (now - cupom_last_activity_at).total_seconds(),
                    cupom_item_count,
                )
            )
            payload = {
                "hora": cupom_open_at.strftime("%Y-%m-%d %H:%M:%S"),
                "fim": now.strftime("%Y-%m-%d %H:%M:%S"),
                "pdv": args.pdv_station,
                "tipo": "suspeita",
                "subtipo": "cupom_aberto_tempo_demais",
                "score": 0,
                "skin": 0,
                "movimentos": 0,
                "motivo": reason,
                "imagem": image_path,
            }
            add_ai_to_payload(payload, ai_summary)
            write_event(events_file, payload)
            cupom_open_alerted = True
            print("CUPOM_ABERTO_TEMPO_DEMAIS", payload["hora"], reason, image_path, flush=True)
            if image_path and should_send_telegram(args, "suspeita"):
                try:
                    send_telegram_photo(args, image_path, telegram_caption(payload))
                    print("TELEGRAM_ENVIADO", "cupom_aberto_tempo_demais", image_path, flush=True)
                except Exception as exc:
                    print("TELEGRAM_ERRO", type(exc).__name__, exc, flush=True)

        try:
            jpeg = snapshot(args)
            image, current, skin = motion_image(jpeg, roi)
            frame_index += 1
            ai_summary, ai_tracks = analyze_ai_frame(ai_runtime, image, virtual_lines, roi, frame_index)
            current_lines = {line["name"]: line_motion_data(image, line, args.virtual_line_band) for line in virtual_lines}
            for name, line_score in virtual_line_hits(previous_lines, current_lines, args.virtual_line_threshold):
                cooldown = args.virtual_scanner_pulse_cooldown if name == "scanner" else args.virtual_line_cooldown
                if (now - last_line_hit.get(name, datetime.min)).total_seconds() < cooldown:
                    continue
                last_line_hit[name] = now
                print("LINHA_VIRTUAL", name, now.strftime("%H:%M:%S"), "score=", round(line_score, 2), flush=True)

                if virtual_flow and (now - virtual_flow["start"]).total_seconds() > args.virtual_line_window:
                    virtual_flow = None
                if name == "entrada":
                    virtual_flow = {
                        "start": now,
                        "last": now,
                        "scanner_pulses": 0,
                        "score": line_score,
                    }
                elif name == "scanner" and virtual_flow:
                    virtual_flow["last"] = now
                    virtual_flow["scanner_pulses"] += 1
                    virtual_flow["score"] = max(virtual_flow["score"], line_score)
                elif name == "saida" and virtual_flow and virtual_flow["scanner_pulses"] > 0:
                    stamp = now.strftime("%Y%m%d_%H%M%S")
                    image_path = evidences / ("pdv%s_linhas_virtuais_%s.jpg" % (args.pdv_station, stamp))
                    save_ai_or_regular_evidence(image.copy(), roi, virtual_lines, image_path, ai_summary, ai_tracks, True)
                    pending.append(
                        {
                            "start": virtual_flow["start"],
                            "last": now,
                            "score": max(virtual_flow["score"], line_score),
                            "skin": skin,
                            "count": virtual_flow["scanner_pulses"],
                            "visual_count": virtual_flow["scanner_pulses"],
                            "image": image_path,
                            "source": "linhas_virtuais",
                            "ai_summary": ai_summary,
                            "ai_tracks": ai_tracks,
                        }
                    )
                    print(
                        "PASSAGEM_ITEM_VISUAL",
                        virtual_flow["start"].strftime("%H:%M:%S"),
                        "qtd_visual=",
                        virtual_flow["scanner_pulses"],
                        image_path,
                        flush=True,
                    )
                    virtual_flow = None

            score = motion_score(previous, current)
            if score > args.threshold and now >= ignore_until and (now - last_motion).total_seconds() > 1:
                stamp = now.strftime("%Y%m%d_%H%M%S")
                image_path = evidences / ("pdv001_movimento_%s.jpg" % stamp)
                save_ai_or_regular_evidence(image, roi, virtual_lines, image_path, ai_summary, ai_tracks)
                if (
                    open_cluster
                    and (now - open_cluster["last"]).total_seconds() <= args.cluster_gap
                    and (now - open_cluster["start"]).total_seconds() <= args.max_cluster
                ):
                    open_cluster["last"] = now
                    open_cluster["score"] = max(open_cluster["score"], score)
                    open_cluster["skin"] = max(open_cluster["skin"], skin)
                    open_cluster["count"] += 1
                    if score >= open_cluster["score"]:
                        open_cluster["image"] = image_path
                        open_cluster["ai_summary"] = ai_summary
                        open_cluster["ai_tracks"] = ai_tracks
                else:
                    if open_cluster:
                        pending.append(open_cluster)
                    open_cluster = {
                        "start": now,
                        "last": now,
                        "score": score,
                        "skin": skin,
                        "count": 1,
                        "image": image_path,
                        "ai_summary": ai_summary,
                        "ai_tracks": ai_tracks,
                    }
                print(
                    "MOVIMENTO",
                    now.strftime("%H:%M:%S"),
                    "score=",
                    round(score, 2),
                    "skin=",
                    round(skin, 3),
                    image_path,
                    flush=True,
                )
                last_motion = now
            previous = current
            previous_lines = current_lines
        except Exception as exc:
            print("CAMERA_ERRO", type(exc).__name__, exc, flush=True)

        if open_cluster and (now - open_cluster["last"]).total_seconds() > args.cluster_gap:
            pending.append(open_cluster)
            open_cluster = None

        while pending:
            cluster = pending[0]
            visual_item = cluster.get("source") == "linhas_virtuais"
            cluster_ai = cluster.get("ai_summary") or ai_empty(False)
            cluster_age = (now - cluster["last"]).total_seconds()
            if cluster_age < args.match_delay:
                break
            paid = typed_near(events, cluster["start"], cluster["last"], args.window, "payment")
            item_before = args.window if visual_item else args.item_before_window
            item_after = args.window if visual_item else args.item_after_window
            near = items_near(
                events,
                cluster["start"],
                cluster["last"],
                item_before,
                item_after,
            )
            consult = typed_near(events, cluster["start"], cluster["last"], args.consultation_window, "consultation")
            activity = activity_near(events, cluster["start"], cluster["last"], args.consultation_window)
            consult_ready = consult and (now - consult[-1][0]).total_seconds() >= args.consultation_suspect_delay
            if near:
                pending.popleft()
                pdv_count = items_visual_quantity(near)
                visual_count = int(cluster.get("visual_count", 1))
                if visual_item and cluster_ai.get("ai_enabled") and int(cluster_ai.get("product_count", 0)) > 0:
                    visual_count = int(cluster_ai.get("product_count", 0))
                    cluster["visual_count"] = visual_count
                visual_skin = visual_item and cluster.get("skin", 0.0) >= args.skin_ignore_ratio
                has_weighted_item = visual_item and items_have_weighted(near)
                if visual_item and visual_count > pdv_count and visual_skin:
                    status = "casou"
                    subtype = ""
                    reason = "qtd visual %d / qtd PDV %d com mao/braco detectado; sem fraude automatica: %s" % (
                        visual_count,
                        pdv_count,
                        near[-1][1].replace('"', "'"),
                    )
                    ignore_until = now + timedelta(seconds=args.post_item_ignore)
                    print("CASOU", cluster["start"].strftime("%H:%M:%S"), "item=", reason, flush=True)
                elif visual_item and visual_count > pdv_count and has_weighted_item:
                    status = "casou"
                    subtype = ""
                    reason = "item pesado/KG; qtd visual %d nao comparada com qtd PDV %d: %s" % (
                        visual_count,
                        pdv_count,
                        near[-1][1].replace('"', "'"),
                    )
                    ignore_until = now + timedelta(seconds=args.post_item_ignore)
                    print("CASOU", cluster["start"].strftime("%H:%M:%S"), "item=", reason, flush=True)
                elif visual_item and visual_count > pdv_count:
                    status = "suspeita"
                    subtype = "quantidade_visual_maior"
                    reason = "qtd visual %d maior que qtd PDV %d: %s" % (
                        visual_count,
                        pdv_count,
                        near[-1][1].replace('"', "'"),
                    )
                    suspect_ignore_until = now + timedelta(seconds=args.suspect_cooldown)
                    print("SUSPEITA_QTD", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
                else:
                    status = "casou"
                    subtype = ""
                    reason = near[-1][1].replace('"', "'")
                    if visual_item:
                        reason = "qtd visual %d / qtd PDV %d: %s" % (visual_count, pdv_count, reason)
                    ignore_until = now + timedelta(seconds=args.post_item_ignore)
                    print("CASOU", cluster["start"].strftime("%H:%M:%S"), "item=", reason, flush=True)
            elif paid:
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento durante pagamento/finalizacao"
                ignore_until = now + timedelta(seconds=args.post_payment_ignore)
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif consult and not consult_ready:
                print("AGUARDANDO_CONSULTA", cluster["start"].strftime("%H:%M:%S"), consult[-1][1], flush=True)
                break
            elif cluster_age < args.pending_suspect_delay:
                break
            elif consult:
                pending.popleft()
                if (
                    not visual_item
                    and (
                        cluster["score"] < args.suspect_min_score
                        or cluster["count"] < args.suspect_min_moves
                        or (cluster["last"] - cluster["start"]).total_seconds() < args.suspect_min_duration
                    )
                ):
                    status = "ignorado"
                    subtype = ""
                    reason = "consulta com movimento fraco/curto sem venda"
                    print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
                elif not visual_item and cluster.get("skin", 0.0) >= args.skin_ignore_ratio:
                    status = "ignorado"
                    subtype = ""
                    reason = "consulta com mao/braco no scanner"
                    print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
                else:
                    status = "suspeita"
                    subtype = "consulta_sem_venda"
                    reason = "CSP com movimento e sem VIT no prazo: %s" % consult[-1][1]
                    suspect_ignore_until = now + timedelta(seconds=args.suspect_cooldown)
                    print("CONSULTA_SEM_VENDA", cluster["start"].strftime("%H:%M:%S"), reason, cluster["image"], flush=True)
            elif cluster["start"] < ignore_until:
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento apos item casado"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif cupom_open is False and not activity:
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento fora de cupom aberto"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif now < suspect_ignore_until:
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento dentro do cooldown de suspeita"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif (
                not visual_item
                and (
                    cluster["score"] < args.suspect_min_score
                    or cluster["count"] < args.suspect_min_moves
                    or (cluster["last"] - cluster["start"]).total_seconds() < args.suspect_min_duration
                )
            ):
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento fraco/curto sem item"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif not visual_item and cluster.get("skin", 0.0) >= args.skin_ignore_ratio:
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento com mao/braco no scanner"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif visual_item and cluster.get("skin", 0.0) >= args.skin_ignore_ratio:
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "passagem nas linhas com mao/braco; sem item PDV correspondente"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            else:
                pending.popleft()
                status = "suspeita"
                subtype = "linhas_virtuais_sem_item" if visual_item else "movimento_sem_item"
                reason = "passagem pelas 3 linhas sem item registrado" if visual_item else "movimento sem item registrado"
                suspect_ignore_until = now + timedelta(seconds=args.suspect_cooldown)
                print("SUSPEITA", cluster["start"].strftime("%H:%M:%S"), reason, cluster["image"], flush=True)

            if status == "suspeita" and ai_should_ignore(subtype, cluster_ai):
                original_subtype = subtype
                status = "ignorado"
                subtype = ""
                reason = "IA nao detectou produto para %s; %s" % (
                    original_subtype,
                    cluster_ai.get("ai_reason", ""),
                )
                print("IGNORADO_AI", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)

            payload = {
                "hora": cluster["start"].strftime("%Y-%m-%d %H:%M:%S"),
                "fim": cluster["last"].strftime("%Y-%m-%d %H:%M:%S"),
                "pdv": args.pdv_station,
                "tipo": status,
                "subtipo": subtype,
                "score": round(cluster["score"], 2),
                "skin": round(cluster.get("skin", 0.0), 3),
                "movimentos": cluster["count"],
                "origem": cluster.get("source", "roi"),
                "quantidade_visual": int(cluster.get("visual_count", 1)) if visual_item else 0,
                "quantidade_pdv": items_visual_quantity(near) if visual_item else 0,
                "motivo": reason,
                "imagem": str(cluster["image"]),
            }
            add_ai_to_payload(payload, cluster_ai)
            write_event(events_file, payload)
            if should_send_telegram(args, status):
                try:
                    send_telegram_photo(args, cluster["image"], telegram_caption(payload))
                    print("TELEGRAM_ENVIADO", status, cluster["image"], flush=True)
                except Exception as exc:
                    print("TELEGRAM_ERRO", type(exc).__name__, exc, flush=True)

        cutoff = now - timedelta(minutes=5)
        events = [(ts, text) for ts, text in events if ts >= cutoff]
        time.sleep(0.35)

    print("AUDITOR_FIM", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()
