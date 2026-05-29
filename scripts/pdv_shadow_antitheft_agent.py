#!/usr/bin/env python3
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lessons-file",
        default=os.environ.get(
            "LEARNING_LESSONS_FILE",
            "/var/log/pdv-learning-agent/knowledge/lessons.jsonl",
        ),
    )
    parser.add_argument(
        "--outdir",
        default=os.environ.get("SHADOW_OUTDIR", "/var/log/pdv-shadow-antitheft"),
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get(
            "SHADOW_STATE_FILE",
            "/var/lib/pdv-shadow-antitheft/offset.state",
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("SHADOW_INTERVAL", "5.0")),
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=int(os.environ.get("SHADOW_DURATION", "0")),
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=float(os.environ.get("SHADOW_MIN_SCORE", "40.0")),
    )
    parser.add_argument(
        "--start-at-end",
        default=os.environ.get("SHADOW_START_AT_END", "1"),
    )
    return parser.parse_args()


def load_offset(state_path):
    try:
        return int(Path(state_path).read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0


def save_offset(state_path, offset):
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(offset), encoding="utf-8")


def is_enabled(value):
    return str(value).lower() in ("1", "s", "sim", "true", "yes", "y")


def read_new_lessons(lessons_file, state_file, start_at_end=False):
    path = Path(lessons_file)
    if not path.exists():
        return []

    size = path.stat().st_size
    state_path = Path(state_file)
    if start_at_end and not state_path.exists():
        save_offset(state_file, size)
        return []

    offset = load_offset(state_file)
    if offset > size:
        offset = 0

    lessons = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                lessons.append(json.loads(line))
            except Exception as exc:
                lessons.append(
                    {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "context_label": "linha_invalida",
                        "raw_error": str(exc),
                    }
                )
        save_offset(state_file, fh.tell())
    return lessons


def score_lesson(lesson):
    score = 0.0
    reasons = []
    label = lesson.get("context_label", "")
    change = float(lesson.get("change_score") or 0)
    recent_events = lesson.get("recent_events") or []

    if label == "movimento_sem_evento_pdv":
        score += 30
        reasons.append("movimento visual sem evento do PDV")
    elif label == "consulta_preco":
        score += 35
        reasons.append("consulta de preco precisa comparar com venda posterior")
    elif label == "cupom_aberto":
        score += 20
        reasons.append("cupom aberto em observacao")
    elif label == "venda_confirmada":
        score += 5
        reasons.append("exemplo normal com item vendido")
    elif label == "pagamento":
        score += 3
        reasons.append("exemplo normal de pagamento")
    elif label == "ambiente":
        reasons.append("amostra de fundo")
    else:
        score += 10
        reasons.append("contexto ainda sem classificacao forte")

    if change >= 18:
        score += 15
        reasons.append("mudanca visual alta")
    elif change >= 10:
        score += 5
        reasons.append("mudanca visual moderada")

    if label == "movimento_sem_evento_pdv" and not recent_events:
        score += 5
        reasons.append("sem evento recente do Espiao")

    return round(score, 2), reasons


def priority_for(score, min_score):
    if score >= min_score + 25:
        return "alta"
    if score >= min_score:
        return "revisar"
    return "normal"


def build_decision(lesson, min_score):
    score, reasons = score_lesson(lesson)
    priority = priority_for(score, min_score)
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_time": lesson.get("time", ""),
        "pdv": lesson.get("pdv", ""),
        "shadow_status": "review_candidate" if priority != "normal" else "normal_reference",
        "priority": priority,
        "score": score,
        "reason": "; ".join(reasons),
        "human_review_required": priority != "normal",
        "no_accusation": True,
        "context_label": lesson.get("context_label", ""),
        "image": lesson.get("image", ""),
        "change_score": lesson.get("change_score", 0),
        "future_agent_hint": lesson.get("future_agent_hint", ""),
        "visual_fingerprint": lesson.get("visual_fingerprint", {}),
        "recent_events": lesson.get("recent_events", []),
    }


def append_jsonl(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_summary(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "version": 1,
        "purpose": "agente antifurto sombra para teste sem alertas",
        "total_observations": 0,
        "review_candidates": 0,
        "by_context": {},
        "by_priority": {},
        "last_updated": "",
    }


def update_summary(outdir, decision):
    path = Path(outdir) / "summary.json"
    summary = load_summary(path)
    summary["total_observations"] = int(summary.get("total_observations", 0)) + 1
    if decision.get("human_review_required"):
        summary["review_candidates"] = int(summary.get("review_candidates", 0)) + 1

    context = decision.get("context_label") or "indefinido"
    priority = decision.get("priority") or "normal"
    by_context = summary.setdefault("by_context", {})
    by_priority = summary.setdefault("by_priority", {})
    by_context[context] = int(by_context.get(context, 0)) + 1
    by_priority[priority] = int(by_priority.get(priority, 0)) + 1
    summary["last_updated"] = decision.get("time", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def handle_lessons(args, lessons):
    root = Path(args.outdir)
    day = datetime.now().strftime("%Y%m%d")
    observations_path = root / day / "observations.jsonl"
    review_path = root / day / "review_queue.jsonl"

    for lesson in lessons:
        decision = build_decision(lesson, args.min_score)
        append_jsonl(observations_path, decision)
        if decision["human_review_required"]:
            append_jsonl(review_path, decision)
        update_summary(root, decision)


def main():
    args = parse_args()
    start = time.time()
    while True:
        lessons = read_new_lessons(args.lessons_file, args.state_file, is_enabled(args.start_at_end))
        if lessons:
            handle_lessons(args, lessons)
        if args.duration and time.time() - start >= args.duration:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
