#!/usr/bin/env python3
"""
PDV Dataset Builder — monta dataset YOLO a partir das imagens coletadas pelo
learning agent, usando YOLO-World para gerar bounding boxes automáticos.

Fluxo:
  1. Varre todos os dias em LEARNING_OUTDIR
  2. venda_confirmada + VIT recente  → positive (produto no scanner)
  3. movimento_sem_evento_pdv        → negative (fundo/suspeito, sem bbox)
  4. Gera split train/val (80/20) em DATASET_OUTDIR/images/ + labels/
  5. Grava data.yaml pronto para YOLOv8 train

Uso:
  python3.8 pdv_dataset_builder.py \
      --learning-dir /var/log/pdv-learning-agent \
      --outdir /var/log/pdv-antitheft/dataset \
      --model /home/rpdv/yolov8s-world.pt \
      --days 7 \
      --max-per-class 2000

Requer: pip3.8 install ultralytics pillow
"""

import argparse
import json
import os
import random
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

VIT_RE = re.compile(r"Descricao:\s*([^|]+)")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--learning-dir", default=os.environ.get("LEARNING_OUTDIR", "/var/log/pdv-learning-agent"))
    p.add_argument("--outdir", default=os.environ.get("DATASET_OUTDIR", "/var/log/pdv-antitheft/dataset"))
    p.add_argument("--model", default=os.environ.get("YOLO_WORLD_MODEL", "/home/rpdv/yolov8s-world.pt"))
    p.add_argument("--device", default=os.environ.get("YOLO_DEVICE", "cpu"))
    p.add_argument("--conf", type=float, default=float(os.environ.get("YOLO_CONF", "0.15")))
    p.add_argument("--days", type=int, default=int(os.environ.get("DATASET_DAYS", "7")))
    p.add_argument("--max-per-class", type=int, default=int(os.environ.get("DATASET_MAX_PER_CLASS", "2000")))
    p.add_argument("--val-split", type=float, default=float(os.environ.get("DATASET_VAL_SPLIT", "0.2")))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def log(msg):
    print("[{}] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def collect_samples(learning_dir, days):
    """Retorna listas de (image_path, label) onde label='produto' ou 'negativo'."""
    root = Path(learning_dir)
    cutoff = datetime.now() - timedelta(days=days)
    positives = []
    negatives = []

    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            day_dt = datetime.strptime(day_dir.name, "%Y%m%d")
        except ValueError:
            continue
        if day_dt < cutoff:
            continue

        meta_path = day_dir / "metadata.jsonl"
        if not meta_path.exists():
            continue

        with meta_path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue

                image = d.get("image", "")
                if not image or not Path(image).exists():
                    continue

                label = d.get("context_label", "")
                events = d.get("recent_events") or []
                vit_events = [e for e in events if e.get("kind") == "item"]

                if label == "venda_confirmada" and vit_events:
                    produto = _extract_product_name(vit_events[0].get("text", ""))
                    positives.append((image, produto))
                elif label == "movimento_sem_evento_pdv":
                    negatives.append((image, ""))

    log("coletados: {} positivos, {} negativos".format(len(positives), len(negatives)))
    return positives, negatives


def _extract_product_name(vit_text):
    m = VIT_RE.search(vit_text)
    if m:
        return m.group(1).strip()
    return "produto"


def load_yolo_world(model_path, device):
    from ultralytics import YOLO, YOLOWorld
    log("carregando modelo YOLO-World: {}".format(model_path))
    try:
        model = YOLOWorld(model_path)
    except Exception:
        model = YOLO(model_path)
    model.set_classes(["produto", "embalagem", "caixa", "garrafa", "pacote", "saco"])
    return model


def auto_label(model, image_path, conf, device):
    """Retorna lista de bboxes YOLO normalizados (cx, cy, w, h) para classe 0."""
    from PIL import Image as PILImage
    result_list = model.predict(
        source=str(image_path),
        conf=conf,
        device=device,
        verbose=False,
        imgsz=640,
    )
    if not result_list:
        return []

    result = result_list[0]
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    img = PILImage.open(image_path)
    w_img, h_img = img.size
    labels = []
    for box in boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        cx = (x1 + x2) / 2.0 / w_img
        cy = (y1 + y2) / 2.0 / h_img
        bw = (x2 - x1) / w_img
        bh = (y2 - y1) / h_img
        labels.append((0, cx, cy, bw, bh))
    return labels


def write_sample(image_src, yolo_labels, split_dir, stem):
    img_dst = split_dir / "images" / (stem + ".jpg")
    lbl_dst = split_dir / "labels" / (stem + ".txt")
    img_dst.parent.mkdir(parents=True, exist_ok=True)
    lbl_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_src, img_dst)
    with lbl_dst.open("w") as fh:
        for row in yolo_labels:
            fh.write("{} {:.6f} {:.6f} {:.6f} {:.6f}\n".format(*row))


def write_data_yaml(outdir, class_names):
    content = (
        "path: {outdir}\n"
        "train: train/images\n"
        "val: val/images\n"
        "nc: {nc}\n"
        "names: {names}\n"
    ).format(outdir=outdir, nc=len(class_names), names=class_names)
    (Path(outdir) / "data.yaml").write_text(content, encoding="utf-8")
    log("data.yaml gravado em {}".format(outdir))


def build(args):
    outdir = Path(args.outdir)
    train_dir = outdir / "train"
    val_dir = outdir / "val"

    positives, negatives = collect_samples(args.learning_dir, args.days)

    random.shuffle(positives)
    random.shuffle(negatives)
    positives = positives[: args.max_per_class]
    negatives = negatives[: args.max_per_class]

    if args.dry_run:
        log("DRY RUN — {} positivos, {} negativos selecionados".format(len(positives), len(negatives)))
        return

    model = load_yolo_world(args.model, args.device)

    stats = {"positivos_com_bbox": 0, "positivos_sem_bbox": 0, "negativos": 0, "erros": 0}
    all_samples = []  # (image_path, yolo_labels)

    log("auto-labelando {} positivos...".format(len(positives)))
    for idx, (img_path, produto) in enumerate(positives):
        if idx % 100 == 0:
            log("  {}/{}".format(idx, len(positives)))
        try:
            labels = auto_label(model, img_path, args.conf, args.device)
            if labels:
                stats["positivos_com_bbox"] += 1
            else:
                # Sem detecção: adiciona bbox full-frame como fallback
                labels = [(0, 0.5, 0.5, 0.9, 0.9)]
                stats["positivos_sem_bbox"] += 1
            all_samples.append((img_path, labels, "pos"))
        except Exception as exc:
            stats["erros"] += 1
            log("  ERRO {}: {}".format(img_path, exc))

    log("adicionando {} negativos...".format(len(negatives)))
    for img_path, _ in negatives:
        all_samples.append((img_path, [], "neg"))
        stats["negativos"] += 1

    # Split train/val
    random.shuffle(all_samples)
    n_val = max(1, int(len(all_samples) * args.val_split))
    val_samples = all_samples[:n_val]
    train_samples = all_samples[n_val:]

    log("gravando {} train, {} val...".format(len(train_samples), len(val_samples)))
    for split, samples in [("train", train_samples), ("val", val_samples)]:
        split_dir = outdir / split
        for i, (img_path, labels, _) in enumerate(samples):
            stem = "pdv_{:06d}".format(i)
            try:
                write_sample(img_path, labels, split_dir, stem)
            except Exception as exc:
                log("  ERRO gravando {}: {}".format(img_path, exc))

    write_data_yaml(str(outdir), ["produto"])

    summary = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "train": len(train_samples),
        "val": len(val_samples),
        "stats": stats,
        "data_yaml": str(outdir / "data.yaml"),
    }
    summary_path = outdir / "build_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log("concluido: {}".format(summary))


def main():
    args = parse_args()
    try:
        build(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
