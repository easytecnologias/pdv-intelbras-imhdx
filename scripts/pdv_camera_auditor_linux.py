#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.auth import HTTPDigestAuth


SPY_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2}):(?P<text>.+)$")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-host", default=os.environ.get("CAMERA_HOST", "10.10.10.20"))
    parser.add_argument("--camera-user", default=os.environ.get("CAMERA_USER", ""))
    parser.add_argument("--camera-pass", default=os.environ.get("CAMERA_PASS", ""))
    parser.add_argument("--pdv-station", default=os.environ.get("PDV_STATION", "001"))
    parser.add_argument("--pdv-base-dir", default=os.environ.get("PDV_BASE_DIR", "/home/rpdv/frente"))
    parser.add_argument("--outdir", default=os.environ.get("AUDITOR_OUTDIR", "/var/log/pdv-camera-auditor"))
    parser.add_argument("--duration", type=int, default=int(os.environ.get("AUDITOR_DURATION", "0")))
    parser.add_argument("--spy-tail", type=int, default=int(os.environ.get("AUDITOR_SPY_TAIL", "350")))
    parser.add_argument("--snapshot-interval", type=float, default=float(os.environ.get("AUDITOR_SNAPSHOT_INTERVAL", "10.0")))
    args = parser.parse_args()
    if not args.camera_user or not args.camera_pass:
        raise SystemExit("camera user/pass ausentes")
    return args


def today_spy_path(args):
    name = "Espiao%s.%s" % (datetime.now().strftime("%d%m%y"), args.pdv_station)
    return Path(args.pdv_base_dir) / "Cm" / name


def spy_event_kind(text):
    if text.startswith("ABRECUPOM |"):
        return "start"
    if text.startswith("CSP |"):
        return "consultation"
    if text.startswith("VIT |"):
        return "item"
    if text.startswith("FIN |"):
        return "payment"
    if text.startswith("FECHACUPOM |"):
        return "payment"
    return ""


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
        text = match.group("text").strip()
        kind = spy_event_kind(text)
        if not kind:
            continue
        event_time = datetime.strptime(match.group("time"), "%H:%M:%S").time()
        events.append((datetime.combine(today, event_time), kind, text))
    return events


def snapshot(args):
    url = "http://%s/cgi-bin/snapshot.cgi?channel=1&type=0" % args.camera_host
    response = requests.get(url, auth=HTTPDigestAuth(args.camera_user, args.camera_pass), timeout=5)
    if response.status_code != 200 or response.content[:2] != b"\xff\xd8":
        raise RuntimeError("snapshot falhou: HTTP %s" % response.status_code)
    return response.content


def write_event(events_file, payload):
    with events_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    events_file = outdir / "events.jsonl"

    seen = set()
    start = datetime.now()
    last_spy = start - timedelta(seconds=20)
    last_snapshot = datetime.min

    print("MONITOR_CAMERA_INICIO", start.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    print("EVENTOS", events_file, flush=True)

    while args.duration <= 0 or (datetime.now() - start).total_seconds() < args.duration:
        now = datetime.now()

        if (now - last_spy).total_seconds() >= 2:
            try:
                for ts, kind, text in get_spy_events(args):
                    if ts < last_spy - timedelta(seconds=2):
                        continue
                    key = (ts, text)
                    if key in seen:
                        continue
                    seen.add(key)
                    payload = {
                        "hora": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "pdv": args.pdv_station,
                        "tipo": "pdv_evento",
                        "evento": kind,
                        "texto": text,
                    }
                    write_event(events_file, payload)
                    print(kind.upper(), ts.strftime("%H:%M:%S"), text, flush=True)
                last_spy = now
            except Exception as exc:
                print("ESPIAO_ERRO", type(exc).__name__, exc, flush=True)

        if (now - last_snapshot).total_seconds() >= args.snapshot_interval:
            try:
                snapshot(args)
                print("CAMERA_OK", now.strftime("%H:%M:%S"), flush=True)
            except Exception as exc:
                print("CAMERA_ERRO", type(exc).__name__, exc, flush=True)
            last_snapshot = now

        time.sleep(0.5)

    print("MONITOR_CAMERA_FIM", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()
