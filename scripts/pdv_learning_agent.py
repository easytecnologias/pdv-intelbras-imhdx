#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image
from requests.auth import HTTPDigestAuth


SPY_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2}):(?P<text>.+)$")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-host", default=os.environ.get("CAMERA_HOST", "10.10.10.20"))
    parser.add_argument("--camera-user", default=os.environ.get("CAMERA_USER", ""))
    parser.add_argument("--camera-pass", default=os.environ.get("CAMERA_PASS", ""))
    parser.add_argument("--pdv-station", default=os.environ.get("PDV_STATION", "001"))
    parser.add_argument("--pdv-base-dir", default=os.environ.get("PDV_BASE_DIR", "/home/rpdv/frente"))
    parser.add_argument("--outdir", default=os.environ.get("LEARNING_OUTDIR", "/var/log/pdv-learning-agent"))
    parser.add_argument("--duration", type=int, default=int(os.environ.get("LEARNING_DURATION", "0")))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("LEARNING_INTERVAL", "3.0")))
    parser.add_argument("--event-window", type=float, default=float(os.environ.get("LEARNING_EVENT_WINDOW", "12.0")))
    parser.add_argument("--change-threshold", type=float, default=float(os.environ.get("LEARNING_CHANGE_THRESHOLD", "7.0")))
    parser.add_argument("--min-save-interval", type=float, default=float(os.environ.get("LEARNING_MIN_SAVE_INTERVAL", "2.0")))
    parser.add_argument("--max-per-day", type=int, default=int(os.environ.get("LEARNING_MAX_PER_DAY", "3500")))
    parser.add_argument("--retention-days", type=int, default=int(os.environ.get("LEARNING_RETENTION_DAYS", "10")))
    parser.add_argument("--spy-tail", type=int, default=int(os.environ.get("LEARNING_SPY_TAIL", "350")))
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
    if text.startswith("FIN |") or text.startswith("FECHACUPOM |"):
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
        events.append({"time": datetime.combine(today, event_time), "kind": kind, "text": text})
    return events


def snapshot(args):
    url = "http://%s/cgi-bin/snapshot.cgi?channel=1&type=0" % args.camera_host
    response = requests.get(url, auth=HTTPDigestAuth(args.camera_user, args.camera_pass), timeout=5)
    if response.status_code != 200 or response.content[:2] != b"\xff\xd8":
        raise RuntimeError("snapshot falhou: HTTP %s" % response.status_code)
    return response.content


def frame_signature(jpeg):
    image = Image.open(BytesIO(jpeg)).convert("L").resize((64, 36))
    return list(image.getdata())


def change_score(previous, current):
    if previous is None:
        return 0.0
    total = sum(abs(a - b) for a, b in zip(previous, current))
    return total / float(len(current))


def recent_events(events, now, seconds):
    start = now - timedelta(seconds=seconds)
    return [event for event in events if start <= event["time"] <= now]


def compact_event(event):
    return {
        "time": event["time"].strftime("%Y-%m-%d %H:%M:%S"),
        "kind": event["kind"],
        "text": event["text"][:500],
    }


def visual_fingerprint(jpeg):
    image = Image.open(BytesIO(jpeg)).convert("RGB")
    small = image.resize((16, 16)).convert("L")
    pixels = list(small.getdata())
    avg = sum(pixels) / float(len(pixels))
    ahash = "".join("1" if value >= avg else "0" for value in pixels)

    color = image.resize((1, 1)).getpixel((0, 0))
    edge_image = image.convert("L").resize((64, 36))
    edge_pixels = list(edge_image.getdata())
    width = 64
    edge_total = 0
    edge_count = 0
    for index, value in enumerate(edge_pixels):
        if index % width != width - 1:
            edge_total += abs(value - edge_pixels[index + 1])
            edge_count += 1
        if index + width < len(edge_pixels):
            edge_total += abs(value - edge_pixels[index + width])
            edge_count += 1

    return {
        "ahash": ahash,
        "brightness": round(avg, 2),
        "avg_rgb": [int(color[0]), int(color[1]), int(color[2])],
        "edge_score": round(edge_total / float(edge_count or 1), 2),
    }


def infer_context_label(reason, context):
    kinds = {event["kind"] for event in context}
    if "item" in kinds:
        return "venda_confirmada"
    if "consultation" in kinds:
        return "consulta_preco"
    if "payment" in kinds:
        return "pagamento"
    if "start" in kinds:
        return "cupom_aberto"
    if reason == "scene_change":
        return "movimento_sem_evento_pdv"
    return "ambiente"


def future_agent_hint(context_label):
    hints = {
        "venda_confirmada": "usar como exemplo normal de item vendido",
        "consulta_preco": "revisar diferenca entre consulta e venda",
        "pagamento": "usar como exemplo normal de finalizacao",
        "cupom_aberto": "usar como contexto inicial de atendimento",
        "movimento_sem_evento_pdv": "prioridade para revisao humana",
        "ambiente": "usar como fundo/negativo",
    }
    return hints.get(context_label, "revisar manualmente")


def write_learning_lesson(root, payload):
    knowledge_dir = root / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    lesson_path = knowledge_dir / "lessons.jsonl"
    with lesson_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    update_handoff(knowledge_dir / "future_antitheft_handoff.json", payload)


def update_handoff(path, lesson):
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    if not data:
        data = {
            "version": 1,
            "purpose": "base de aprendizado para futuro agente antifurto",
            "total_samples": 0,
            "labels": {},
            "last_updated": "",
            "rules": [
                "nao gerar alerta sem revisao humana",
                "nao usar movimento como fraude",
                "usar venda_confirmada como normal positivo",
                "usar movimento_sem_evento_pdv como fila de revisao",
            ],
        }

    label = lesson.get("context_label", "indefinido")
    labels = data.setdefault("labels", {})
    bucket = labels.setdefault(label, {"samples": 0, "examples": []})
    bucket["samples"] += 1
    data["total_samples"] = int(data.get("total_samples", 0)) + 1
    data["last_updated"] = lesson.get("time", "")

    examples = bucket.setdefault("examples", [])
    if len(examples) < 25:
        examples.append(
            {
                "time": lesson.get("time", ""),
                "image": lesson.get("image", ""),
                "reason": lesson.get("reason", ""),
                "change_score": lesson.get("change_score", 0),
                "fingerprint": lesson.get("visual_fingerprint", {}),
                "hint": lesson.get("future_agent_hint", ""),
            }
        )

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_sample(args, root, jpeg, reason, score, context, now):
    day = now.strftime("%Y%m%d")
    day_dir = root / day
    images_dir = day_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    stamp = now.strftime("%H%M%S_%f")[:-3]
    name = "pdv%s_%s_%s.jpg" % (args.pdv_station, reason, stamp)
    image_path = images_dir / name
    meta_path = day_dir / "metadata.jsonl"

    image_path.write_bytes(jpeg)
    context_label = infer_context_label(reason, context)
    payload = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "pdv": args.pdv_station,
        "image": str(image_path),
        "reason": reason,
        "change_score": round(score, 3),
        "label_status": "pending_human_review",
        "context_label": context_label,
        "future_agent_hint": future_agent_hint(context_label),
        "visual_fingerprint": visual_fingerprint(jpeg),
        "recent_events": [compact_event(event) for event in context],
    }
    with meta_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    write_learning_lesson(root, payload)
    return image_path


def cleanup_old_days(root, retention_days):
    if retention_days <= 0 or not root.exists():
        return
    cutoff = datetime.now() - timedelta(days=retention_days)
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            day = datetime.strptime(child.name, "%Y%m%d")
        except ValueError:
            continue
        if day < cutoff:
            for path in sorted(child.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            child.rmdir()


def main():
    args = parse_args()
    root = Path(args.outdir)
    root.mkdir(parents=True, exist_ok=True)

    seen = set()
    events = []
    previous_signature = None
    last_spy = datetime.min
    last_saved = datetime.min
    saved_day = ""
    saved_count = 0
    start = datetime.now()

    print("LEARNING_AGENT_INICIO", start.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    print("LEARNING_OUTDIR", root, flush=True)

    while args.duration <= 0 or (datetime.now() - start).total_seconds() < args.duration:
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        if day != saved_day:
            saved_day = day
            saved_count = 0
            cleanup_old_days(root, args.retention_days)

        if (now - last_spy).total_seconds() >= 2:
            try:
                for event in get_spy_events(args):
                    key = (event["time"], event["text"])
                    if last_spy != datetime.min and event["time"] < last_spy - timedelta(seconds=2):
                        continue
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(event)
                    print("LEARNING_EVENT", event["kind"].upper(), event["time"].strftime("%H:%M:%S"), flush=True)
                last_spy = now
            except Exception as exc:
                print("LEARNING_ESPIAO_ERRO", type(exc).__name__, exc, flush=True)

        try:
            jpeg = snapshot(args)
            signature = frame_signature(jpeg)
            score = change_score(previous_signature, signature)
            context = recent_events(events, now, args.event_window)
            reason = ""
            if context:
                reason = "pdv_event"
            elif score >= args.change_threshold:
                reason = "scene_change"

            if (
                reason
                and saved_count < args.max_per_day
                and (now - last_saved).total_seconds() >= args.min_save_interval
            ):
                image_path = save_sample(args, root, jpeg, reason, score, context, now)
                saved_count += 1
                last_saved = now
                print("LEARNING_SAMPLE", reason, round(score, 2), image_path, flush=True)
            previous_signature = signature
        except Exception as exc:
            print("LEARNING_CAMERA_ERRO", type(exc).__name__, exc, flush=True)

        cutoff = now - timedelta(minutes=10)
        events = [event for event in events if event["time"] >= cutoff]
        time.sleep(args.interval)

    print("LEARNING_AGENT_FIM", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()
