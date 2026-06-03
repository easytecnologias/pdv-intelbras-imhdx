#!/usr/bin/env python3
"""
PDV Anti-theft Agent — 2 estágios:
  Estágio 1 (YOLO, local, ~1s): detecta se há produto na câmera
  Estágio 2 (LLaVA via Ollama, ~8s): confirma se é fraude de verdade

Só envia alerta se AMBOS confirmarem — elimina falsos positivos.

Uso:
  python3.8 pdv_antitheft_agent.py

Variáveis de ambiente (em /etc/pdv-antitheft-agent.env):
  CAMERA_HOST, CAMERA_USER, CAMERA_PASS
  PDV_STATION, PDV_BASE_DIR
  ANTITHEFT_MODEL         — modelo YOLO treinado (ou usa fallback YOLO-World)
  ANTITHEFT_FALLBACK_MODEL
  OLLAMA_URL              — padrão http://localhost:11434
  OLLAMA_MODEL            — padrão llava:7b
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Requer: pip3.8 install ultralytics requests pillow
        Ollama rodando com: ollama pull llava:7b
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from requests.auth import HTTPDigestAuth

SPY_RE = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2}):(?P<text>.+)$")
VIT_RE = re.compile(r"Descricao:\s*([^|]+)")
PRICE_RE = re.compile(r"VlTotal:\s*([\d.,]+)")

# Perguntas para BLIP-VQA em inglês (modelo responde melhor) + tradução amigável
QUESTIONS_VQA = [
    ("is someone holding a product without scanning it",
     "produto segurado sem passar no scanner"),
    ("is a hand covering the barcode scanner",
     "mao cobrindo o leitor de codigo"),
    ("is there a product visible near the cashier",
     "produto visivel perto do caixa"),
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--camera-host",       default=os.environ.get("CAMERA_HOST", "10.10.10.20"))
    p.add_argument("--camera-user",       default=os.environ.get("CAMERA_USER", ""))
    p.add_argument("--camera-pass",       default=os.environ.get("CAMERA_PASS", ""))
    p.add_argument("--pdv-station",       default=os.environ.get("PDV_STATION", "001"))
    p.add_argument("--pdv-base-dir",      default=os.environ.get("PDV_BASE_DIR", "/home/rpdv/frente"))
    p.add_argument("--model",             default=os.environ.get("ANTITHEFT_MODEL", "/var/log/pdv-antitheft/models/best.pt"))
    p.add_argument("--fallback-model",    default=os.environ.get("ANTITHEFT_FALLBACK_MODEL", "/home/rpdv/yolov8s-world.pt"))
    p.add_argument("--conf",    type=float, default=float(os.environ.get("ANTITHEFT_CONF", "0.35")))
    p.add_argument("--device",            default=os.environ.get("YOLO_DEVICE", "cpu"))
    p.add_argument("--interval", type=float, default=float(os.environ.get("ANTITHEFT_INTERVAL", "2.0")))
    p.add_argument("--event-window", type=float, default=float(os.environ.get("ANTITHEFT_EVENT_WINDOW", "10.0")))
    p.add_argument("--alert-cooldown", type=float, default=float(os.environ.get("ANTITHEFT_COOLDOWN", "30.0")))
    p.add_argument("--spy-tail", type=int, default=int(os.environ.get("ANTITHEFT_SPY_TAIL", "500")))
    p.add_argument("--vision-model",      default=os.environ.get("VISION_MODEL", "Salesforce/blip-vqa-base"))
    p.add_argument("--vision-timeout", type=int, default=int(os.environ.get("VISION_TIMEOUT", "60")))
    p.add_argument("--telegram-token",    default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    p.add_argument("--telegram-chat-id",  default=os.environ.get("TELEGRAM_CHAT_ID", ""))
    p.add_argument("--outdir",            default=os.environ.get("ANTITHEFT_OUTDIR", "/var/log/pdv-antitheft/alerts"))
    p.add_argument("--duration", type=int, default=int(os.environ.get("ANTITHEFT_DURATION", "0")))
    p.add_argument("--skip-llava", action="store_true", default=os.environ.get("ANTITHEFT_SKIP_LLAVA", "") == "1")
    args = p.parse_args()
    if not args.camera_user or not args.camera_pass:
        raise SystemExit("CAMERA_USER e CAMERA_PASS sao obrigatorios")
    return args


def log(msg):
    print("[{}] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


# ─────────────────────────────── Câmera ──────────────────────────────────────

def snapshot(args):
    url = "http://{}/cgi-bin/snapshot.cgi?channel=1&type=0".format(args.camera_host)
    r = requests.get(url, auth=HTTPDigestAuth(args.camera_user, args.camera_pass), timeout=5)
    if r.status_code != 200 or r.content[:2] != b"\xff\xd8":
        raise RuntimeError("snapshot falhou: HTTP {}".format(r.status_code))
    return r.content


# ─────────────────────────────── Espião ──────────────────────────────────────

def today_spy_path(args):
    name = "Espiao{}.{}".format(datetime.now().strftime("%d%m%y"), args.pdv_station)
    return Path(args.pdv_base_dir) / "Cm" / name


def get_recent_vit(args, window_seconds):
    path = today_spy_path(args)
    if not path.exists():
        return []
    lines = path.read_text(errors="replace").splitlines()[-args.spy_tail:]
    now = datetime.now()
    cutoff = now - timedelta(seconds=window_seconds)
    today = now.date()
    vits = []
    for line in lines:
        m = SPY_RE.match(line.strip())
        if not m:
            continue
        text = m.group("text").strip()
        if not text.startswith("VIT |"):
            continue
        event_time = datetime.combine(today, datetime.strptime(m.group("time"), "%H:%M:%S").time())
        if event_time >= cutoff:
            nome_m = VIT_RE.search(text)
            preco_m = PRICE_RE.search(text)
            vits.append({
                "time": event_time.strftime("%H:%M:%S"),
                "nome": nome_m.group(1).strip() if nome_m else "?",
                "preco": preco_m.group(1).strip() if preco_m else "?",
            })
    return vits


# ─────────────────────────────── YOLO ────────────────────────────────────────

def load_yolo(model_path, fallback_path, device):
    from ultralytics import YOLO, YOLOWorld
    path = Path(model_path)
    if path.exists():
        log("YOLO: carregando modelo treinado {}".format(path))
        return YOLO(str(path)), False
    log("YOLO: modelo treinado nao encontrado, usando YOLO-World fallback")
    path = Path(fallback_path)
    try:
        model = YOLOWorld(str(path))
    except Exception:
        model = YOLO(str(path))
    model.set_classes(["produto", "embalagem", "caixa", "garrafa", "pacote", "saco"])
    return model, True


def yolo_detect(model, jpeg, conf, device):
    import numpy as np
    img = Image.open(BytesIO(jpeg)).convert("RGB")
    img_array = np.array(img)
    result_list = model.predict(
        source=img_array,
        conf=conf,
        device=device,
        verbose=False,
        imgsz=640,
    )
    if not result_list:
        return []
    result = result_list[0]
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    names = getattr(result, "names", {}) or {}
    out = []
    for box in boxes:
        cls_id = int(box.cls[0].item())
        conf_val = float(box.conf[0].item())
        xyxy = [round(float(v), 1) for v in box.xyxy[0].tolist()]
        out.append({"label": str(names.get(cls_id, cls_id)), "conf": round(conf_val, 3), "box": xyxy})
    return out


# ─────────────────────── BLIP-VQA (transformers, local) ──────────────────────

_blip_model = None
_blip_processor = None


def load_vision_model(model_id, _revision=None):
    global _blip_model, _blip_processor
    if _blip_model is not None:
        return
    log("VISION: carregando {} (~1GB, primeira vez pode demorar)...".format(model_id))
    from transformers import BlipProcessor, BlipForQuestionAnswering
    _blip_processor = BlipProcessor.from_pretrained(model_id)
    _blip_model = BlipForQuestionAnswering.from_pretrained(model_id)
    _blip_model.eval()
    log("VISION: BLIP-VQA carregado")


def vision_analyze(jpeg, args):
    """Analisa imagem com BLIP-VQA. Retorna (suspeito: bool, motivo: str)."""
    try:
        load_vision_model(args.vision_model)
    except Exception as exc:
        log("VISION_LOAD_ERRO: {}".format(exc))
        return True, "modelo vision indisponivel"

    try:
        import torch
        img = Image.open(BytesIO(jpeg)).convert("RGB")
        respostas = []
        for pergunta_en, descricao_pt in QUESTIONS_VQA:
            inputs = _blip_processor(img, pergunta_en, return_tensors="pt")
            with torch.no_grad():
                out = _blip_model.generate(**inputs, max_new_tokens=5)
            resposta = _blip_processor.decode(out[0], skip_special_tokens=True).strip().lower()
            respostas.append((descricao_pt, resposta))
            log("VISION_QA: '{}' -> '{}'".format(descricao_pt[:40], resposta))
    except Exception as exc:
        log("VISION_ERRO: {}".format(exc))
        return True, "erro na analise visual"

    # Suspeito se qualquer pergunta retornar "yes"
    motivos_pt = [desc for desc, r in respostas if "yes" in r]
    suspeito = bool(motivos_pt)
    motivo = " e ".join(motivos_pt) if motivos_pt else "sem evidencia clara"

    log("VISION: {} - {}".format("SUSPEITO" if suspeito else "NORMAL", motivo))
    return suspeito, motivo


# ─────────────────────────────── Alerta ──────────────────────────────────────

def draw_alert(jpeg, detections, motivo):
    img = Image.open(BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    for d in detections:
        x1, y1, x2, y2 = d["box"]
        draw.rectangle((x1, y1, x2, y2), outline=(255, 50, 50), width=3)
        lbl = "{} {:.0f}%".format(d["label"], d["conf"] * 100)
        bb = draw.textbbox((x1, max(0, y1 - 12)), lbl, font=font)
        draw.rectangle(bb, fill=(255, 50, 50))
        draw.text((x1, max(0, y1 - 12)), lbl, fill=(255, 255, 255), font=font)
    # Faixa vermelha com motivo
    header = "ALERTA: " + motivo[:80]
    bb = draw.textbbox((4, 4), header, font=font)
    draw.rectangle(bb, fill=(200, 0, 0))
    draw.text((4, 4), header, fill=(255, 255, 255), font=font)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def telegram_send_photo(token, chat_id, jpeg, caption, alert_id=None):
    if not token or not chat_id:
        return
    url = "https://api.telegram.org/bot{}/sendPhoto".format(token)
    reply_markup = None
    if alert_id:
        reply_markup = json.dumps({
            "inline_keyboard": [[
                {"text": "✅ Fraude real",       "callback_data": "atf_ok:{}".format(alert_id)},
                {"text": "❌ Falso positivo",    "callback_data": "atf_no:{}".format(alert_id)},
            ]]
        })
    data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        r = requests.post(url, data=data,
                          files={"photo": ("alerta.jpg", jpeg, "image/jpeg")}, timeout=20)
        if not r.ok:
            log("TELEGRAM_ERRO: {} {}".format(r.status_code, r.text[:100]))
    except Exception as exc:
        log("TELEGRAM_FALHOU: {}".format(exc))


def save_alert(outdir, record):
    root = Path(outdir)
    day = datetime.now().strftime("%Y%m%d")
    path = root / day / "alerts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─────────────────────────────── Loop ────────────────────────────────────────

def run(args):
    model, is_fallback = load_yolo(args.model, args.fallback_model, args.device)
    use_vision = not args.skip_llava

    if use_vision:
        try:
            load_vision_model(args.vision_model)
            log("VISION: BLIP-VQA carregado e pronto")
        except Exception as exc:
            log("VISION: nao disponivel ({}), rodando so com YOLO".format(exc))
            use_vision = False

    last_alert = datetime.min
    start = datetime.now()

    log("ANTITHEFT_INICIO PDV={} camera={} yolo_fallback={} vision={}".format(
        args.pdv_station, args.camera_host, is_fallback, use_vision))

    while args.duration <= 0 or (datetime.now() - start).total_seconds() < args.duration:
        now = datetime.now()
        loop_start = time.time()

        # ── Estágio 0: captura ────────────────────────────────────────────────
        try:
            jpeg = snapshot(args)
        except Exception as exc:
            log("CAMERA_ERRO {}".format(exc))
            time.sleep(args.interval)
            continue

        # ── Estágio 1: YOLO ───────────────────────────────────────────────────
        try:
            detections = yolo_detect(model, jpeg, args.conf, args.device)
        except Exception as exc:
            log("YOLO_ERRO {}".format(exc))
            time.sleep(args.interval)
            continue

        produto_detectado = bool(detections)

        if not produto_detectado:
            elapsed = time.time() - loop_start
            time.sleep(max(0, args.interval - elapsed))
            continue

        # ── Espião ────────────────────────────────────────────────────────────
        vits = get_recent_vit(args, args.event_window)
        if vits:
            log("OK produto detectado + VIT: {}".format(vits[-1]["nome"][:40]))
            elapsed = time.time() - loop_start
            time.sleep(max(0, args.interval - elapsed))
            continue

        # Produto detectado, sem VIT — suspeito no estágio 1
        det_str = ", ".join("{} {:.0f}%".format(d["label"], d["conf"] * 100) for d in detections)
        log("SUSPEITO_YOLO: {} — enviando para LLaVA...".format(det_str))

        # ── Estágio 2: Moondream2 ────────────────────────────────────────────
        if use_vision:
            suspeito, motivo = vision_analyze(jpeg, args)
            if not suspeito:
                elapsed = time.time() - loop_start
                time.sleep(max(0, args.interval - elapsed))
                continue
        else:
            motivo = "produto detectado sem scan (YOLO)"

        # ── Cooldown ──────────────────────────────────────────────────────────
        if (now - last_alert).total_seconds() < args.alert_cooldown:
            elapsed = time.time() - loop_start
            time.sleep(max(0, args.interval - elapsed))
            continue

        last_alert = now

        # ── Dispara alerta ────────────────────────────────────────────────────
        log("ALERTA CONFIRMADO: {}".format(motivo))

        motivo_curto = motivo[:60] if motivo else "suspeito"
        annotated = draw_alert(jpeg, detections, "Caixa {} - {}".format(args.pdv_station, motivo_curto))

        stamp = now.strftime("%Y%m%d_%H%M%S")
        alert_id = "{}_{}_{}".format(args.pdv_station, stamp, now.microsecond // 1000)

        caption = (
            "Caixa {pdv} - {hora}\n\n"
            "A camera identificou uma situacao suspeita:\n"
            "{motivo}\n\n"
            "Confianca da deteccao: {conf}\n\n"
            "Verifique o video no iMHDX e confirme abaixo:"
        ).format(
            pdv=args.pdv_station,
            hora=now.strftime("%H:%M:%S"),
            motivo=motivo.capitalize(),
            conf=det_str,
        )

        telegram_send_photo(args.telegram_token, args.telegram_chat_id, annotated, caption,
                            alert_id=alert_id)

        img_path = None
        try:
            img_path = Path(args.outdir) / now.strftime("%Y%m%d") / "alert_{}.jpg".format(stamp)
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.write_bytes(annotated)
        except Exception as exc:
            log("ERRO salvando imagem: {}".format(exc))

        save_alert(args.outdir, {
            "alert_id": alert_id,
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "pdv": args.pdv_station,
            "tipo": "produto_sem_scan",
            "motivo_llava": motivo,
            "deteccoes": detections,
            "image": str(img_path) if img_path else None,
            "feedback": None,
        })

        elapsed = time.time() - loop_start
        time.sleep(max(0, args.interval - elapsed))


def main():
    args = parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
