#!/usr/bin/env python3
"""
PDV YOLO Trainer — faz fine-tune do YOLOv8s no dataset gerado pelo
pdv_dataset_builder.py e salva o modelo treinado.

Modos:
  1. --once      : treina uma vez e sai
  2. --watch     : monitora o dataset e re-treina quando acumular N novas amostras
  3. sem flag    : treina uma vez (padrão)

Uso:
  python3.8 pdv_yolo_trainer.py \
      --dataset /var/log/pdv-antitheft/dataset \
      --base-model /home/rpdv/yolov8s-world.pt \
      --outdir /var/log/pdv-antitheft/models \
      --epochs 40 \
      --device cpu

Requer: pip3.8 install ultralytics
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default=os.environ.get("DATASET_OUTDIR", "/var/log/pdv-antitheft/dataset"))
    p.add_argument("--base-model", default=os.environ.get("YOLO_BASE_MODEL", "/home/rpdv/yolov8s-world.pt"))
    p.add_argument("--outdir", default=os.environ.get("TRAINER_OUTDIR", "/var/log/pdv-antitheft/models"))
    p.add_argument("--epochs", type=int, default=int(os.environ.get("TRAINER_EPOCHS", "40")))
    p.add_argument("--batch", type=int, default=int(os.environ.get("TRAINER_BATCH", "8")))
    p.add_argument("--imgsz", type=int, default=int(os.environ.get("TRAINER_IMGSZ", "320")))
    p.add_argument("--device", default=os.environ.get("YOLO_DEVICE", "cpu"))
    p.add_argument("--patience", type=int, default=int(os.environ.get("TRAINER_PATIENCE", "10")))
    p.add_argument("--watch", action="store_true")
    p.add_argument("--watch-interval", type=int, default=int(os.environ.get("TRAINER_WATCH_INTERVAL", "3600")))
    p.add_argument("--min-new-samples", type=int, default=int(os.environ.get("TRAINER_MIN_NEW_SAMPLES", "200")))
    return p.parse_args()


def log(msg):
    print("[{}] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def count_images(dataset_dir):
    root = Path(dataset_dir)
    total = 0
    for split in ("train", "val"):
        img_dir = root / split / "images"
        if img_dir.exists():
            total += len(list(img_dir.glob("*.jpg")))
    return total


def train(args):
    from ultralytics import YOLO

    data_yaml = Path(args.dataset) / "data.yaml"
    if not data_yaml.exists():
        raise FileNotFoundError("data.yaml nao encontrado em {}. Rode pdv_dataset_builder.py primeiro.".format(args.dataset))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = "pdv_antitheft_{}".format(stamp)
    runs_dir = outdir / "runs"

    log("carregando modelo base: {}".format(args.base_model))
    model = YOLO(args.base_model)

    n_images = count_images(args.dataset)
    log("iniciando treino: {} imagens, {} epocas, device={}".format(n_images, args.epochs, args.device))

    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        device=args.device,
        patience=args.patience,
        project=str(runs_dir),
        name=run_name,
        exist_ok=True,
        verbose=True,
        workers=0,  # evita multiprocessing no Ubuntu 18.04
    )

    # Copia o melhor modelo para destino fixo
    best_src = runs_dir / run_name / "weights" / "best.pt"
    best_dst = outdir / "best.pt"
    last_dst = outdir / "last.pt"

    if best_src.exists():
        import shutil
        shutil.copy2(best_src, best_dst)
        shutil.copy2(runs_dir / run_name / "weights" / "last.pt", last_dst)
        log("modelo salvo: {}".format(best_dst))
    else:
        log("AVISO: best.pt nao encontrado em {}".format(best_src))

    # Salva métricas
    metrics = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_name": run_name,
        "base_model": args.base_model,
        "epochs": args.epochs,
        "n_images": n_images,
        "device": args.device,
        "best_model": str(best_dst) if best_src.exists() else None,
        "results_dir": str(runs_dir / run_name),
    }
    if hasattr(results, "results_dict"):
        metrics["results"] = {
            k: float(v) for k, v in results.results_dict.items()
            if isinstance(v, (int, float))
        }

    metrics_path = outdir / "train_metrics_{}.json".format(stamp)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    log("metricas salvas: {}".format(metrics_path))
    return metrics


def watch_loop(args):
    log("modo watch: checando a cada {}s, re-treina com >={} novas amostras".format(
        args.watch_interval, args.min_new_samples))
    last_count = count_images(args.dataset)
    log("baseline: {} imagens".format(last_count))

    while True:
        time.sleep(args.watch_interval)
        current = count_images(args.dataset)
        new = current - last_count
        log("dataset: {} imagens ({:+d} novas)".format(current, new))
        if new >= args.min_new_samples:
            log("iniciando re-treino ({} novas amostras)...".format(new))
            try:
                train(args)
                last_count = current
            except Exception as exc:
                log("ERRO no treino: {}".format(exc))
        else:
            log("aguardando mais amostras (minimo: {})...".format(args.min_new_samples))


def main():
    args = parse_args()
    try:
        if args.watch:
            watch_loop(args)
        else:
            train(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        log("ERRO: {}".format(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
