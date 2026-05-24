import argparse
import os
import re
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import requests
from requests.auth import HTTPDigestAuth


ITEM_RE = re.compile(r"\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] enviado: (?P<text>.+)$")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-host", default="10.10.10.20")
    parser.add_argument("--camera-user", required=True)
    parser.add_argument("--camera-pass", required=True)
    parser.add_argument("--pdv-host", default="192.168.24.97")
    parser.add_argument("--pdv-user", default="root")
    parser.add_argument("--pdv-pass", required=True)
    parser.add_argument("--pdv-hostkey", required=True)
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--window", type=float, default=5.0)
    parser.add_argument("--cluster-gap", type=float, default=3.0)
    parser.add_argument("--max-cluster", type=float, default=7.0)
    parser.add_argument("--post-item-ignore", type=float, default=8.0)
    parser.add_argument("--post-payment-ignore", type=float, default=12.0)
    parser.add_argument("--outdir", default="tmp/pdv1_auditor_live")
    parser.add_argument("--roi", default="325,185,485,355")
    parser.add_argument("--telegram-token", default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    parser.add_argument("--telegram-send-types", default="suspeita")
    return parser.parse_args()


def get_journal_events(args, since):
    since_text = since.strftime("%Y-%m-%d %H:%M:%S")
    remote = (
        "journalctl -u pdv-intelbras-bridge.service "
        f"--since '{since_text}' --no-pager -o cat"
    )
    cmd = [
        "plink",
        "-batch",
        "-ssh",
        f"{args.pdv_user}@{args.pdv_host}",
        "-hostkey",
        args.pdv_hostkey,
        "-pw",
        args.pdv_pass,
        remote,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    events = []
    for line in proc.stdout.splitlines():
        match = ITEM_RE.search(line)
        if not match:
            continue
        ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S")
        events.append((ts, match.group("text")))
    return events


def snapshot(args):
    url = f"http://{args.camera_host}/cgi-bin/snapshot.cgi?channel=1&type=0"
    response = requests.get(
        url,
        auth=HTTPDigestAuth(args.camera_user, args.camera_pass),
        timeout=5,
    )
    if response.status_code != 200 or response.content[:2] != b"\xff\xd8":
        raise RuntimeError(f"snapshot falhou: HTTP {response.status_code}")
    arr = np.frombuffer(response.content, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("snapshot nao decodificou")
    return frame


def telegram_enabled(args):
    return bool(args.telegram_token and args.telegram_chat_id)


def send_telegram_photo(args, image_path, caption):
    if not telegram_enabled(args):
        return
    url = f"https://api.telegram.org/bot{args.telegram_token}/sendPhoto"
    with open(image_path, "rb") as photo:
        response = requests.post(
            url,
            data={"chat_id": args.telegram_chat_id, "caption": caption[:1024]},
            files={"photo": photo},
            timeout=15,
        )
    if response.status_code != 200:
        raise RuntimeError(f"telegram HTTP {response.status_code}: {response.text[:200]}")


def should_send_telegram(args, status):
    allowed = {item.strip().lower() for item in args.telegram_send_types.split(",") if item.strip()}
    return status.lower() in allowed or "todos" in allowed or "all" in allowed


def item_near(items, target, seconds):
    before = target - timedelta(seconds=seconds)
    after = target + timedelta(seconds=seconds)
    near = [(ts, text) for ts, text in items if before <= ts <= after and event_kind(text) == "item"]
    return near


def item_near_cluster(items, start, end, seconds):
    before = start - timedelta(seconds=seconds)
    after = end + timedelta(seconds=seconds)
    return [(ts, text) for ts, text in items if before <= ts <= after and event_kind(text) == "item"]


def payment_near_cluster(items, start, end, seconds):
    before = start - timedelta(seconds=seconds)
    after = end + timedelta(seconds=seconds)
    return [(ts, text) for ts, text in items if before <= ts <= after and event_kind(text) == "payment"]


def event_kind(text):
    if "| FIM" in text or " | FIM" in text:
        return "payment"
    if "| INICIO" in text or " | INICIO " in text:
        return "start"
    return "item"


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    evidences = outdir / "evidencias"
    evidences.mkdir(parents=True, exist_ok=True)
    events_file = outdir / "events.jsonl"
    x1, y1, x2, y2 = [int(v.strip()) for v in args.roi.split(",")]

    previous = None
    last_motion = datetime.min
    ignore_until = datetime.min
    open_cluster = None
    pending = deque()
    items = []
    seen_items = set()
    start = datetime.now()
    last_journal = start - timedelta(seconds=20)

    print("AUDITOR_INICIO", start.strftime("%Y-%m-%d %H:%M:%S"))
    print("ROI", (x1, y1, x2, y2))
    print("EVENTOS", events_file)

    while (datetime.now() - start).total_seconds() < args.duration:
        now = datetime.now()

        if (now - last_journal).total_seconds() >= 2:
            try:
                for ts, text in get_journal_events(args, last_journal - timedelta(seconds=2)):
                    key = (ts, text)
                    if key in seen_items:
                        continue
                    seen_items.add(key)
                    items.append(key)
                    print("ITEM", ts.strftime("%H:%M:%S"), text)
                last_journal = now
            except Exception as exc:
                print("JOURNAL_ERRO", type(exc).__name__, exc)

        try:
            frame = snapshot(args)
            crop = frame[y1:y2, x1:x2]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (9, 9), 0)
            if previous is not None:
                diff = cv2.absdiff(previous, gray)
                _, threshold = cv2.threshold(diff, 28, 255, cv2.THRESH_BINARY)
                score = cv2.countNonZero(threshold) / threshold.size
                if score > 0.055 and now >= ignore_until and (now - last_motion).total_seconds() > 1:
                    stamp = now.strftime("%Y%m%d_%H%M%S")
                    image_path = evidences / f"pdv001_movimento_{stamp}.jpg"
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                    cv2.imwrite(str(image_path), frame)
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
                    print("MOVIMENTO", now.strftime("%H:%M:%S"), "score=", round(score, 4), image_path)
                    last_motion = now
            previous = gray
        except Exception as exc:
            print("CAMERA_ERRO", type(exc).__name__, exc)

        if open_cluster and (now - open_cluster["last"]).total_seconds() > args.cluster_gap:
            pending.append(open_cluster)
            open_cluster = None

        while pending and (now - pending[0]["last"]).total_seconds() >= args.window:
            cluster = pending.popleft()
            motion_ts = cluster["start"]
            score = cluster["score"]
            image_path = cluster["image"]
            paid = payment_near_cluster(items, cluster["start"], cluster["last"], args.window)
            near = item_near_cluster(items, cluster["start"], cluster["last"], args.window)
            if near:
                status = "casou"
                reason = near[-1][1].replace('"', "'")
                ignore_until = now + timedelta(seconds=args.post_item_ignore)
                print("CASOU", motion_ts.strftime("%H:%M:%S"), "item=", reason)
            elif paid:
                status = "ignorado"
                reason = "movimento durante pagamento/finalizacao"
                ignore_until = now + timedelta(seconds=args.post_payment_ignore)
                print("IGNORADO", motion_ts.strftime("%H:%M:%S"), reason)
            elif motion_ts < ignore_until:
                status = "ignorado"
                reason = "movimento apos item casado"
                print("IGNORADO", motion_ts.strftime("%H:%M:%S"), reason)
            else:
                status = "suspeita"
                reason = "movimento sem item registrado"
                print("SUSPEITA", motion_ts.strftime("%H:%M:%S"), reason, image_path)
            line = (
                '{"hora":"%s","fim":"%s","pdv":"001","tipo":"%s","score":%.4f,'
                '"movimentos":%d,"motivo":"%s","imagem":"%s"}\n'
            ) % (
                motion_ts.strftime("%Y-%m-%d %H:%M:%S"),
                cluster["last"].strftime("%Y-%m-%d %H:%M:%S"),
                status,
                score,
                cluster["count"],
                reason,
                str(image_path).replace("\\", "\\\\"),
            )
            with events_file.open("a", encoding="utf-8") as fh:
                fh.write(line)
            if should_send_telegram(args, status):
                caption = (
                    "PDV 001 - {tipo}\n"
                    "Hora: {hora}\n"
                    "Fim: {fim}\n"
                    "Motivo: {motivo}\n"
                    "Score: {score:.4f}\n"
                    "Movimentos: {movimentos}"
                ).format(
                    tipo=status.upper(),
                    hora=motion_ts.strftime("%Y-%m-%d %H:%M:%S"),
                    fim=cluster["last"].strftime("%Y-%m-%d %H:%M:%S"),
                    motivo=reason,
                    score=score,
                    movimentos=cluster["count"],
                )
                try:
                    send_telegram_photo(args, image_path, caption)
                    print("TELEGRAM_ENVIADO", status, image_path)
                except Exception as exc:
                    print("TELEGRAM_ERRO", type(exc).__name__, exc)

        cutoff = now - timedelta(minutes=5)
        items = [(ts, text) for ts, text in items if ts >= cutoff]
        time.sleep(0.35)

    print("AUDITOR_FIM", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    main()
