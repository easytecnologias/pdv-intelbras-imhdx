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
    parser.add_argument("--window", type=float, default=float(os.environ.get("AUDITOR_WINDOW", "5.0")))
    parser.add_argument("--item-before-window", type=float, default=float(os.environ.get("AUDITOR_ITEM_BEFORE", "20.0")))
    parser.add_argument("--item-after-window", type=float, default=float(os.environ.get("AUDITOR_ITEM_AFTER", "35.0")))
    parser.add_argument("--pending-suspect-delay", type=float, default=float(os.environ.get("AUDITOR_PENDING_DELAY", "30.0")))
    parser.add_argument("--consultation-window", type=float, default=float(os.environ.get("AUDITOR_CONSULTATION_WINDOW", "45.0")))
    parser.add_argument("--cluster-gap", type=float, default=float(os.environ.get("AUDITOR_CLUSTER_GAP", "3.0")))
    parser.add_argument("--max-cluster", type=float, default=float(os.environ.get("AUDITOR_MAX_CLUSTER", "7.0")))
    parser.add_argument("--post-item-ignore", type=float, default=float(os.environ.get("AUDITOR_POST_ITEM_IGNORE", "8.0")))
    parser.add_argument("--post-payment-ignore", type=float, default=float(os.environ.get("AUDITOR_POST_PAYMENT_IGNORE", "12.0")))
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
    if text.startswith("FECHACUPOM |") or text.startswith("FIN |"):
        return "payment"
    return ""


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
    return text


def event_kind(text):
    if text.startswith("CONSULTA"):
        return "consultation"
    if text == "ABRECUPOM":
        return "start"
    if text == "FECHACUPOM":
        return "payment"
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
    crop = image.crop(roi).convert("L").resize((46, 65))
    return image, list(crop.getdata())


def motion_score(previous, current):
    if previous is None:
        return 0.0
    total = sum(abs(a - b) for a, b in zip(previous, current))
    return total / float(len(current))


def save_evidence(image, roi, path):
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = roi
    for offset in range(3):
        draw.rectangle((x1 - offset, y1 - offset, x2 + offset, y2 + offset), outline=(255, 255, 0))
    image.save(str(path), "JPEG", quality=88)


def items_near(events, start, end, before_seconds, after_seconds):
    before = start - timedelta(seconds=before_seconds)
    after = end + timedelta(seconds=after_seconds)
    return [(ts, text) for ts, text in events if before <= ts <= after and event_kind(text) == "item"]


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


def main():
    args = parse_args()
    roi = tuple(int(v.strip()) for v in args.roi.split(","))
    outdir = Path(args.outdir)
    evidences = outdir / "evidencias"
    evidences.mkdir(parents=True, exist_ok=True)
    events_file = outdir / "events.jsonl"

    previous = None
    last_motion = datetime.min
    ignore_until = datetime.min
    open_cluster = None
    pending = deque()
    events = []
    seen = set()
    cupom_open = None
    start = datetime.now()
    last_spy = start - timedelta(seconds=20)

    print("AUDITOR_INICIO", start.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    print("ROI", roi, flush=True)
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
                    if kind in {"start", "item", "consultation"}:
                        cupom_open = True
                    elif kind == "payment":
                        cupom_open = False
                    print(kind.upper(), ts.strftime("%H:%M:%S"), text, flush=True)
                last_spy = now
            except Exception as exc:
                print("ESPIAO_ERRO", type(exc).__name__, exc, flush=True)

        try:
            jpeg = snapshot(args)
            image, current = motion_image(jpeg, roi)
            score = motion_score(previous, current)
            if score > args.threshold and now >= ignore_until and (now - last_motion).total_seconds() > 1:
                stamp = now.strftime("%Y%m%d_%H%M%S")
                image_path = evidences / ("pdv001_movimento_%s.jpg" % stamp)
                save_evidence(image, roi, image_path)
                if (
                    open_cluster
                    and (now - open_cluster["last"]).total_seconds() <= args.cluster_gap
                    and (now - open_cluster["start"]).total_seconds() <= args.max_cluster
                ):
                    open_cluster["last"] = now
                    open_cluster["score"] = max(open_cluster["score"], score)
                    open_cluster["count"] += 1
                    if score >= open_cluster["score"]:
                        open_cluster["image"] = image_path
                else:
                    if open_cluster:
                        pending.append(open_cluster)
                    open_cluster = {
                        "start": now,
                        "last": now,
                        "score": score,
                        "count": 1,
                        "image": image_path,
                    }
                print("MOVIMENTO", now.strftime("%H:%M:%S"), "score=", round(score, 2), image_path, flush=True)
                last_motion = now
            previous = current
        except Exception as exc:
            print("CAMERA_ERRO", type(exc).__name__, exc, flush=True)

        if open_cluster and (now - open_cluster["last"]).total_seconds() > args.cluster_gap:
            pending.append(open_cluster)
            open_cluster = None

        while pending and (now - pending[0]["last"]).total_seconds() >= args.pending_suspect_delay:
            cluster = pending.popleft()
            paid = typed_near(events, cluster["start"], cluster["last"], args.window, "payment")
            near = items_near(
                events,
                cluster["start"],
                cluster["last"],
                args.item_before_window,
                args.item_after_window,
            )
            consult = typed_near(events, cluster["start"], cluster["last"], args.consultation_window, "consultation")
            activity = activity_near(events, cluster["start"], cluster["last"], args.consultation_window)
            if near:
                status = "casou"
                reason = near[-1][1].replace('"', "'")
                ignore_until = now + timedelta(seconds=args.post_item_ignore)
                print("CASOU", cluster["start"].strftime("%H:%M:%S"), "item=", reason, flush=True)
            elif paid:
                status = "ignorado"
                reason = "movimento durante pagamento/finalizacao"
                ignore_until = now + timedelta(seconds=args.post_payment_ignore)
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif consult and (now - consult[-1][0]).total_seconds() < args.consultation_window:
                pending.append(cluster)
                print("AGUARDANDO_CONSULTA", cluster["start"].strftime("%H:%M:%S"), consult[-1][1], flush=True)
                break
            elif consult:
                status = "consulta"
                reason = "movimento durante consulta sem venda no prazo: %s" % consult[-1][1]
                print("CONSULTA_SEM_VENDA", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif cluster["start"] < ignore_until:
                status = "ignorado"
                reason = "movimento apos item casado"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif cupom_open is False and not activity:
                status = "ignorado"
                reason = "movimento fora de cupom aberto"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            else:
                status = "suspeita"
                reason = "movimento sem item registrado"
                print("SUSPEITA", cluster["start"].strftime("%H:%M:%S"), reason, cluster["image"], flush=True)

            payload = {
                "hora": cluster["start"].strftime("%Y-%m-%d %H:%M:%S"),
                "fim": cluster["last"].strftime("%Y-%m-%d %H:%M:%S"),
                "pdv": args.pdv_station,
                "tipo": status,
                "score": round(cluster["score"], 2),
                "movimentos": cluster["count"],
                "motivo": reason,
                "imagem": str(cluster["image"]),
            }
            write_event(events_file, payload)
            if should_send_telegram(args, status):
                caption = (
                    "PDV %s - %s\nHora: %s\nFim: %s\nMotivo: %s\nScore: %.2f\nMovimentos: %d"
                    % (
                        args.pdv_station,
                        status.upper(),
                        payload["hora"],
                        payload["fim"],
                        reason,
                        cluster["score"],
                        cluster["count"],
                    )
                )
                try:
                    send_telegram_photo(args, cluster["image"], caption)
                    print("TELEGRAM_ENVIADO", status, cluster["image"], flush=True)
                except Exception as exc:
                    print("TELEGRAM_ERRO", type(exc).__name__, exc, flush=True)

        cutoff = now - timedelta(minutes=5)
        events = [(ts, text) for ts, text in events if ts >= cutoff]
        time.sleep(0.35)

    print("AUDITOR_FIM", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()
