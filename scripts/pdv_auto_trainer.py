#!/usr/bin/env python3
"""
PDV Auto Trainer — treina o modelo de detecção automaticamente toda madrugada.

Fluxo:
  1. Conta novas amostras desde o último treino
  2. Se < MIN_NEW_SAMPLES → loga e sai (sem treinar)
  3. Reconstrói dataset dos últimos DATASET_DAYS dias
  4. Treina YOLOv8 (40 épocas por padrão)
  5. Compara mAP novo vs anterior
  6. Se melhorou (ou é o primeiro treino) → copia best.pt para produção
  7. Reinicia pdv-antitheft-agent.service para carregar novo modelo
  8. Salva histórico de treinos em train_history.jsonl

Uso (chamado pelo systemd timer toda madrugada):
  python3.8 /opt/pdv-antitheft/pdv_auto_trainer.py

Variáveis de ambiente (em /etc/pdv-antitheft-agent.env):
  LEARNING_OUTDIR, DATASET_OUTDIR, TRAINER_OUTDIR, YOLO_WORLD_MODEL
  AUTO_TRAINER_MIN_SAMPLES  — mínimo de novas amostras para treinar (padrão: 300)
  AUTO_TRAINER_DATASET_DAYS — dias de histórico (padrão: 7)
  AUTO_TRAINER_EPOCHS       — épocas (padrão: 40)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Garante prioridade mínima mesmo se chamado fora do systemd
os.nice(19)


# ── Configuração ──────────────────────────────────────────────────────────────

LEARNING_DIR    = os.environ.get("LEARNING_OUTDIR",          "/var/log/pdv-learning-agent")
DATASET_DIR     = os.environ.get("DATASET_OUTDIR",           "/var/log/pdv-antitheft/dataset")
TRAINER_DIR     = os.environ.get("TRAINER_OUTDIR",           "/var/log/pdv-antitheft/models")
YOLO_MODEL      = os.environ.get("YOLO_WORLD_MODEL",         "/home/rpdv/yolov8s-world.pt")
PYTHON          = os.environ.get("AUTO_TRAINER_PYTHON",      "/usr/bin/python3.8")
SCRIPT_DIR      = os.environ.get("AUTO_TRAINER_SCRIPT_DIR",  "/opt/pdv-antitheft")
MIN_SAMPLES     = int(os.environ.get("AUTO_TRAINER_MIN_SAMPLES",   "300"))
DATASET_DAYS    = int(os.environ.get("AUTO_TRAINER_DATASET_DAYS",  "7"))
EPOCHS          = int(os.environ.get("AUTO_TRAINER_EPOCHS",        "40"))
DEVICE          = os.environ.get("YOLO_DEVICE", "cpu")
ANTITHEFT_SVC   = os.environ.get("AUTO_TRAINER_SERVICE", "pdv-antitheft-agent.service")

HISTORY_FILE    = Path(TRAINER_DIR) / "train_history.jsonl"
STATE_FILE      = Path(TRAINER_DIR) / "auto_trainer.state"
PROD_MODEL      = Path(TRAINER_DIR) / "best.pt"
PREV_MODEL      = Path(TRAINER_DIR) / "best_prev.pt"


def log(msg):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] AUTO_TRAINER {}".format(stamp, msg)
    print(line, flush=True)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_train_time": None, "last_sample_count": 0, "best_map": 0.0, "train_count": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def count_positive_samples(learning_dir, days):
    """Conta imagens venda_confirmada com VIT nos últimos N dias."""
    root = Path(learning_dir)
    cutoff = datetime.now() - timedelta(days=days)
    count = 0
    for day_dir in root.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            day_dt = datetime.strptime(day_dir.name, "%Y%m%d")
        except ValueError:
            continue
        if day_dt < cutoff:
            continue
        meta = day_dir / "metadata.jsonl"
        if not meta.exists():
            continue
        with meta.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                    if (d.get("context_label") == "venda_confirmada"
                            and any(e.get("kind") == "item" for e in (d.get("recent_events") or []))):
                        count += 1
                except Exception:
                    pass
    return count


def run_step(label, cmd, timeout=7200):
    """Executa subprocesso e retorna (sucesso, output)."""
    log("iniciando: {}".format(label))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            log("ERRO em {}: {}".format(label, output[-500:]))
            return False, output
        log("concluido: {} — {}".format(label, output[-200:]))
        return True, output
    except subprocess.TimeoutExpired:
        log("TIMEOUT em {} ({}s)".format(label, timeout))
        return False, "timeout"
    except Exception as exc:
        log("EXCECAO em {}: {}".format(label, exc))
        return False, str(exc)


def extract_map_from_metrics(trainer_dir):
    """Lê o mAP50 do último treino a partir dos arquivos de métricas."""
    root = Path(trainer_dir)
    metrics_files = sorted(root.glob("train_metrics_*.json"), key=lambda p: p.stat().st_mtime)
    if not metrics_files:
        return 0.0
    try:
        data = json.loads(metrics_files[-1].read_text(encoding="utf-8"))
        results = data.get("results", {})
        # Tenta diferentes chaves que ultralytics pode usar
        for key in ("metrics/mAP50(B)", "metrics/mAP50-95(B)", "mAP50", "mAP"):
            if key in results:
                return float(results[key])
    except Exception:
        pass
    return 0.0


def restart_antitheft():
    """Reinicia o agente antifurto para carregar novo modelo."""
    try:
        subprocess.run(
            ["systemctl", "restart", ANTITHEFT_SVC],
            timeout=15, capture_output=True
        )
        log("servico {} reiniciado com novo modelo".format(ANTITHEFT_SVC))
    except Exception as exc:
        log("aviso: nao conseguiu reiniciar servico: {}".format(exc))


def append_history(record):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def train_cycle():
    state = load_state()
    now = datetime.now()

    log("=== CICLO DE TREINO AUTOMATICO ===")
    log("ultimo treino: {}".format(state.get("last_train_time") or "nunca"))
    log("treinos realizados: {}".format(state.get("train_count", 0)))

    # ── 1) Contar novas amostras ──────────────────────────────────────────────
    total_samples = count_positive_samples(LEARNING_DIR, DATASET_DAYS)
    last_count = state.get("last_sample_count", 0)
    new_samples = max(0, total_samples - last_count)

    log("amostras positivas (ultimos {}d): {} total, {} novas".format(
        DATASET_DAYS, total_samples, new_samples))

    if new_samples < MIN_SAMPLES and state.get("train_count", 0) > 0:
        log("novas amostras ({}) abaixo do minimo ({}). Pulando treino.".format(
            new_samples, MIN_SAMPLES))
        append_history({
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "action": "skipped",
            "reason": "new_samples={} < min={}".format(new_samples, MIN_SAMPLES),
            "total_samples": total_samples,
        })
        return

    log("iniciando pipeline de treino ({} novas amostras)...".format(
        new_samples if state.get("train_count", 0) > 0 else total_samples))

    # ── 2) Construir dataset ──────────────────────────────────────────────────
    ok, out = run_step("dataset_builder", [
        PYTHON,
        str(Path(SCRIPT_DIR) / "pdv_dataset_builder.py"),
        "--learning-dir", LEARNING_DIR,
        "--outdir", DATASET_DIR,
        "--model", YOLO_MODEL,
        "--days", str(DATASET_DAYS),
        "--max-per-class", "2000",
        "--device", DEVICE,
    ], timeout=3600)

    if not ok:
        append_history({"time": now.strftime("%Y-%m-%d %H:%M:%S"), "action": "failed", "step": "dataset"})
        return

    # ── 3) Treinar ───────────────────────────────────────────────────────────
    ok, out = run_step("yolo_trainer", [
        PYTHON,
        str(Path(SCRIPT_DIR) / "pdv_yolo_trainer.py"),
        "--dataset", DATASET_DIR,
        "--base-model", YOLO_MODEL,
        "--outdir", TRAINER_DIR,
        "--epochs", str(EPOCHS),
        "--device", DEVICE,
        "--batch", "8",
    ], timeout=7200)

    if not ok:
        append_history({"time": now.strftime("%Y-%m-%d %H:%M:%S"), "action": "failed", "step": "train"})
        return

    # ── 4) Avaliar e decidir deploy ───────────────────────────────────────────
    new_map = extract_map_from_metrics(TRAINER_DIR)
    prev_map = state.get("best_map", 0.0)
    is_first = state.get("train_count", 0) == 0

    log("mAP50 novo={:.4f} anterior={:.4f}".format(new_map, prev_map))

    if is_first or new_map >= prev_map - 0.01:  # aceita até 1% de regressão
        # Guarda backup do modelo anterior
        if PROD_MODEL.exists():
            shutil.copy2(PROD_MODEL, PREV_MODEL)

        log("DEPLOY: modelo novo aceito (mAP {:.4f} >= {:.4f})".format(new_map, prev_map))
        restart_antitheft()
        deployed = True
        state["best_map"] = max(new_map, prev_map)
    else:
        log("DEPLOY REJEITADO: mAP regrediu demais ({:.4f} < {:.4f})".format(new_map, prev_map))
        deployed = False

    # ── 5) Atualizar state e histórico ───────────────────────────────────────
    state["last_train_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
    state["last_sample_count"] = total_samples
    state["train_count"] = state.get("train_count", 0) + 1
    save_state(state)

    append_history({
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "trained",
        "deployed": deployed,
        "new_map": new_map,
        "prev_map": prev_map,
        "total_samples": total_samples,
        "new_samples": new_samples,
        "epochs": EPOCHS,
    })

    log("=== CICLO CONCLUIDO: treino #{}, deploy={} ===".format(
        state["train_count"], deployed))


def main():
    Path(TRAINER_DIR).mkdir(parents=True, exist_ok=True)
    try:
        train_cycle()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        log("ERRO FATAL: {}".format(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
