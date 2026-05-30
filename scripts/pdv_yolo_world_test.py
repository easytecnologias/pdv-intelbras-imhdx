#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


DEFAULT_CLASSES = [
    "product",
    "package",
    "box",
    "bottle",
    "bag",
    "basket",
    "hand",
    "person",
    "scanner",
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=os.environ.get("YOLO_TEST_INPUT_DIR", ""))
    parser.add_argument("--outdir", default=os.environ.get("YOLO_TEST_OUTDIR", "/var/log/pdv-yolo-test"))
    parser.add_argument("--model", default=os.environ.get("YOLO_WORLD_MODEL", "yolov8s-world.pt"))
    parser.add_argument("--device", default=os.environ.get("YOLO_DEVICE", "cpu"))
    parser.add_argument("--conf", type=float, default=float(os.environ.get("YOLO_CONF", "0.18")))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("YOLO_TEST_LIMIT", "50")))
    parser.add_argument("--classes", default=os.environ.get("YOLO_TEST_CLASSES", ",".join(DEFAULT_CLASSES)))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.input_dir:
        raise SystemExit("--input-dir obrigatorio")
    return args


def image_files(input_dir, limit):
    path = Path(input_dir)
    if not path.exists():
        raise SystemExit("diretorio nao encontrado: %s" % path)
    files = []
    for pattern in ("*.jpg", "*.jpeg", "*.png"):
        files.extend(path.rglob(pattern))
    files = sorted(files, key=lambda item: item.stat().st_mtime)
    if limit > 0:
        return files[-limit:]
    return files


def load_model(args, classes):
    try:
        from ultralytics import YOLO, YOLOWorld
    except Exception as exc:
        raise SystemExit(
            "dependencia ausente: instale ultralytics/torch para rodar YOLO-World (%s)" % exc
        )

    try:
        model = YOLOWorld(args.model)
    except Exception:
        model = YOLO(args.model)

    if hasattr(model, "set_classes"):
        model.set_classes(classes)
    return model


def result_boxes(result):
    names = getattr(result, "names", {}) or {}
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []

    output = []
    for box in boxes:
        xyxy = box.xyxy[0].tolist()
        cls_id = int(box.cls[0].item()) if box.cls is not None else -1
        conf = float(box.conf[0].item()) if box.conf is not None else 0.0
        output.append(
            {
                "label": str(names.get(cls_id, cls_id)),
                "confidence": round(conf, 4),
                "box": [round(float(value), 2) for value in xyxy],
            }
        )
    return output


def color_for(label):
    palette = {
        "product": (255, 214, 10),
        "package": (255, 214, 10),
        "box": (255, 214, 10),
        "bottle": (255, 214, 10),
        "bag": (80, 180, 255),
        "basket": (80, 180, 255),
        "hand": (70, 220, 120),
        "person": (255, 100, 100),
        "scanner": (180, 90, 255),
    }
    return palette.get(label, (255, 255, 255))


def draw_boxes(image_path, detections, output_path):
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    for item in detections:
        x1, y1, x2, y2 = item["box"]
        label = "%s %.2f" % (item["label"], item["confidence"])
        color = color_for(item["label"])
        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
        bbox = draw.textbbox((x1, max(0, y1 - 12)), label, font=font)
        draw.rectangle(bbox, fill=color)
        draw.text((x1, max(0, y1 - 12)), label, fill=(0, 0, 0), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=90)


def write_jsonl(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def summarize(results):
    labels = {}
    total = 0
    for item in results:
        for detection in item.get("detections", []):
            total += 1
            label = detection["label"]
            labels[label] = labels.get(label, 0) + 1
    return {"images": len(results), "detections": total, "labels": labels}


def run(args):
    classes = [item.strip() for item in args.classes.split(",") if item.strip()]
    files = image_files(args.input_dir, args.limit)
    root = Path(args.outdir)
    day = datetime.now().strftime("%Y%m%d")
    results_path = root / day / "results.jsonl"
    summary_path = root / day / "summary.json"
    annotated_dir = root / day / "annotated"

    if args.dry_run:
        payload = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "dry_run",
            "input_dir": str(args.input_dir),
            "images_found": len(files),
            "classes": classes,
        }
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    model = load_model(args, classes)
    outputs = []
    for image_path in files:
        prediction = model.predict(
            source=str(image_path),
            conf=args.conf,
            device=args.device,
            verbose=False,
        )
        detections = result_boxes(prediction[0]) if prediction else []
        annotated_path = annotated_dir / image_path.name
        draw_boxes(image_path, detections, annotated_path)

        payload = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "image": str(image_path),
            "annotated_image": str(annotated_path),
            "model": args.model,
            "device": args.device,
            "confidence": args.conf,
            "classes": classes,
            "detections": detections,
        }
        write_jsonl(results_path, payload)
        outputs.append(payload)

    summary = summarize(outputs)
    summary["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary["model"] = args.model
    summary["device"] = args.device
    summary["results"] = str(results_path)
    summary["annotated_dir"] = str(annotated_dir)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main():
    try:
        return run(parse_args())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
