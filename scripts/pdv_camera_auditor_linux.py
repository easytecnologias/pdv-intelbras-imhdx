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


def save_evidence(image, roi, path):
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = roi
    for offset in range(3):
        draw.rectangle((x1 - offset, y1 - offset, x2 + offset, y2 + offset), outline=(255, 255, 0))
    image.save(str(path), "JPEG", quality=88)


def save_current_evidence(args, roi, evidences, prefix):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = evidences / ("%s_%s.jpg" % (prefix, stamp))
    image = Image.open(BytesIO(snapshot(args))).convert("RGB")
    save_evidence(image, roi, image_path)
    return image_path


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
    cupom_open_at = None
    cupom_last_activity_at = None
    cupom_item_count = 0
    cupom_open_alerted = False
    suspect_ignore_until = datetime.min
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
                            image_path = str(save_current_evidence(args, roi, evidences, "pdv%s_cancelamento" % args.pdv_station))
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
                        write_event(events_file, payload)
                        print("CANCELAMENTO_ESTORNO", ts.strftime("%H:%M:%S"), text, image_path, flush=True)
                        if image_path and should_send_telegram(args, "suspeita"):
                            caption = (
                                "PDV %s - SUSPEITA\nHora: %s\nMotivo: cancelamento/estorno\n%s"
                                % (args.pdv_station, payload["hora"], text)
                            )
                            try:
                                send_telegram_photo(args, image_path, caption)
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
                image_path = str(save_current_evidence(args, roi, evidences, "pdv%s_cupom_aberto" % args.pdv_station))
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
            write_event(events_file, payload)
            cupom_open_alerted = True
            print("CUPOM_ABERTO_TEMPO_DEMAIS", payload["hora"], reason, image_path, flush=True)
            if image_path and should_send_telegram(args, "suspeita"):
                caption = (
                    "PDV %s - SUSPEITA\nHora: %s\nFim: %s\nMotivo: %s"
                    % (args.pdv_station, payload["hora"], payload["fim"], reason)
                )
                try:
                    send_telegram_photo(args, image_path, caption)
                    print("TELEGRAM_ENVIADO", "cupom_aberto_tempo_demais", image_path, flush=True)
                except Exception as exc:
                    print("TELEGRAM_ERRO", type(exc).__name__, exc, flush=True)

        try:
            jpeg = snapshot(args)
            image, current, skin = motion_image(jpeg, roi)
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
                    open_cluster["skin"] = max(open_cluster["skin"], skin)
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
                        "skin": skin,
                        "count": 1,
                        "image": image_path,
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
        except Exception as exc:
            print("CAMERA_ERRO", type(exc).__name__, exc, flush=True)

        if open_cluster and (now - open_cluster["last"]).total_seconds() > args.cluster_gap:
            pending.append(open_cluster)
            open_cluster = None

        while pending:
            cluster = pending[0]
            cluster_age = (now - cluster["last"]).total_seconds()
            if cluster_age < args.match_delay:
                break
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
            consult_ready = consult and (now - consult[-1][0]).total_seconds() >= args.consultation_suspect_delay
            if near:
                pending.popleft()
                status = "casou"
                subtype = ""
                reason = near[-1][1].replace('"', "'")
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
                    cluster["score"] < args.suspect_min_score
                    or cluster["count"] < args.suspect_min_moves
                    or (cluster["last"] - cluster["start"]).total_seconds() < args.suspect_min_duration
                ):
                    status = "ignorado"
                    subtype = ""
                    reason = "consulta com movimento fraco/curto sem venda"
                    print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
                elif cluster.get("skin", 0.0) >= args.skin_ignore_ratio:
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
                cluster["score"] < args.suspect_min_score
                or cluster["count"] < args.suspect_min_moves
                or (cluster["last"] - cluster["start"]).total_seconds() < args.suspect_min_duration
            ):
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento fraco/curto sem item"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            elif cluster.get("skin", 0.0) >= args.skin_ignore_ratio:
                pending.popleft()
                status = "ignorado"
                subtype = ""
                reason = "movimento com mao/braco no scanner"
                print("IGNORADO", cluster["start"].strftime("%H:%M:%S"), reason, flush=True)
            else:
                pending.popleft()
                status = "suspeita"
                subtype = "movimento_sem_item"
                reason = "movimento sem item registrado"
                suspect_ignore_until = now + timedelta(seconds=args.suspect_cooldown)
                print("SUSPEITA", cluster["start"].strftime("%H:%M:%S"), reason, cluster["image"], flush=True)

            payload = {
                "hora": cluster["start"].strftime("%Y-%m-%d %H:%M:%S"),
                "fim": cluster["last"].strftime("%Y-%m-%d %H:%M:%S"),
                "pdv": args.pdv_station,
                "tipo": status,
                "subtipo": subtype,
                "score": round(cluster["score"], 2),
                "skin": round(cluster.get("skin", 0.0), 3),
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
