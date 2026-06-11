#!/usr/bin/env python3
"""
PDV VIT Verifier — verifica cada produto registrado no caixa contra a câmera.

Gatilho: cada novo VIT (registro de produto) no arquivo Espião do PDV.
Lógica: quando um produto é registrado, tira foto após snap_delay segundos e pergunta:
  "O PDV registrou PRODUTO. O que você vê no scanner? Corresponde?"
Alerta SOMENTE quando o produto visível não corresponde ao registrado.

Variáveis de ambiente (em /etc/pdv-antitheft-agent.env):
  CAMERA_HOST, CAMERA_USER, CAMERA_PASS
  PDV_STATION, PDV_BASE_DIR
  GROQ_API_KEY          — chave Groq (console.groq.com)
  GROQ_MODEL            — padrão: meta-llama/llama-4-scout-17b-16e-instruct
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  ANTITHEFT_INTERVAL    — segundos entre polls de VIT (padrão 2)
  ANTITHEFT_SNAP_DELAY  — segundos após VIT antes de tirar foto (padrão 2)
  ANTITHEFT_OUTDIR      — diretório de logs

Requer: pip3.8 install requests
"""

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.auth import HTTPDigestAuth

SPY_RE    = re.compile(r"^(?P<time>\d{2}:\d{2}:\d{2}):(?P<text>.+)$")
VIT_RE    = re.compile(r"Descricao:\s*([^|]+)")
PRICE_RE  = re.compile(r"VlTotal:\s*([^\|]+)")
QUANT_RE  = re.compile(r"Quant:\s*([^\|]+)")
UNIT_RE   = re.compile(r"Und:\s*([^\|]+)")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--camera-host",      default=os.environ.get("CAMERA_HOST",        "10.10.10.20"))
    p.add_argument("--camera-user",      default=os.environ.get("CAMERA_USER",        ""))
    p.add_argument("--camera-pass",      default=os.environ.get("CAMERA_PASS",        ""))
    p.add_argument("--pdv-station",      default=os.environ.get("PDV_STATION",        "001"))
    p.add_argument("--pdv-base-dir",     default=os.environ.get("PDV_BASE_DIR",       "/home/rpdv/frente"))
    p.add_argument("--spy-tail", type=int, default=int(os.environ.get("ANTITHEFT_SPY_TAIL",    "500")))
    p.add_argument("--groq-api-key",     default=os.environ.get("GROQ_API_KEY",       ""))
    p.add_argument("--groq-model",       default=os.environ.get("GROQ_MODEL",         "meta-llama/llama-4-scout-17b-16e-instruct"))
    p.add_argument("--skip-vision", action="store_true", default=os.environ.get("ANTITHEFT_SKIP_VISION", "") == "1")
    p.add_argument("--telegram-token",   default=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    p.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID",   ""))
    p.add_argument("--interval",   type=float, default=float(os.environ.get("ANTITHEFT_INTERVAL",   "2.0")))
    p.add_argument("--snap-delay", type=float, default=float(os.environ.get("ANTITHEFT_SNAP_DELAY", "2.0")))
    p.add_argument("--outdir",           default=os.environ.get("ANTITHEFT_OUTDIR",   "/var/log/pdv-antitheft/alerts"))
    p.add_argument("--duration",   type=int,   default=int(os.environ.get("ANTITHEFT_DURATION",     "0")))
    p.add_argument("--imhdx-host",    default=os.environ.get("IMHDX_HOST",    ""))
    p.add_argument("--imhdx-user",    default=os.environ.get("IMHDX_USER",    ""))
    p.add_argument("--imhdx-pass",    default=os.environ.get("IMHDX_PASS",    ""))
    p.add_argument("--imhdx-channel", type=int, default=int(os.environ.get("IMHDX_CHANNEL", "1")))
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


def get_latest_vit(args):
    """Retorna o VIT mais recente do Espião hoje, ou None."""
    path = today_spy_path(args)
    if not path.exists():
        return None
    lines = path.read_text(errors="replace").splitlines()[-args.spy_tail:]
    now   = datetime.now()
    today = now.date()
    for line in reversed(lines):
        m = SPY_RE.match(line.strip())
        if not m:
            continue
        text = m.group("text").strip()
        if not text.startswith("VIT |"):
            continue
        event_time = datetime.combine(today, datetime.strptime(m.group("time"), "%H:%M:%S").time())
        nome_m  = VIT_RE.search(text)
        price_m = PRICE_RE.search(text)
        quant_m = QUANT_RE.search(text)
        unit_m  = UNIT_RE.search(text)
        nome    = nome_m.group(1).strip()  if nome_m  else "?"
        valor   = price_m.group(1).strip() if price_m else ""
        quant   = quant_m.group(1).strip() if quant_m else "1"
        und     = unit_m.group(1).strip()  if unit_m  else ""
        # Label completo para o Groq: "Polpa De Fruta Goiaba 100g - R$1,29 (2 Un)"
        preco_str = "R${}".format(valor) if valor else ""
        qtd_str   = "{} {}".format(quant, und).strip() if quant != "1" else und
        extras    = " - ".join(filter(None, [preco_str, qtd_str]))
        label     = "{} {}".format(nome, extras).strip(" -") if extras else nome
        return {
            "time":  event_time,
            "nome":  nome,
            "valor": valor,
            "label": label,
        }
    return None


def get_recent_vit(args, window_seconds):
    """Retorna lista de VITs na janela — usado pelo bot sob demanda."""
    path = today_spy_path(args)
    if not path.exists():
        return []
    lines  = path.read_text(errors="replace").splitlines()[-args.spy_tail:]
    now    = datetime.now()
    cutoff = now - timedelta(seconds=window_seconds)
    today  = now.date()
    vits   = []
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
            vits.append({
                "time": event_time.strftime("%H:%M:%S"),
                "nome": nome_m.group(1).strip() if nome_m else "?",
            })
    return vits


# ─────────────────────────────── Vision (Groq API) ───────────────────────────

_PROMPT_VERIFY = (
    "O caixa acabou de registrar no PDV: \"{produto}\".\n\n"
    "Olhe para a área do scanner e para as mãos/braços da operadora.\n"
    "Descreva o produto que você vê sendo passado ou segurado agora.\n"
    "O produto visível corresponde a \"{produto}\"?\n\n"
    "Regras:\n"
    "- Se a cena estiver vazia ou sem produto visível: corresponde = true\n"
    "- Responda corresponde = false SOMENTE se você vir claramente um produto diferente\n"
    "- Nunca julgue por postura, aparência ou movimento ambíguo\n"
    "- Responda SOMENTE com JSON, sem texto antes ou depois\n\n"
    "Formato: {{\"produto_visivel\": \"o que você vê\", \"corresponde\": true/false, "
    "\"motivo\": \"até 15 palavras\"}}"
)

_PROMPT_DESCRIBE = (
    "Descreva em uma frase o que está acontecendo nesta imagem de câmera de caixa de supermercado. "
    "Foque no que está na área do scanner. Seja objetivo e neutro."
)


def vision_verify_vit(jpeg, args, label):
    """Verifica se o produto visível corresponde ao VIT registrado. Retorna dict ou None."""
    if not args.groq_api_key:
        return None

    b64    = base64.b64encode(jpeg).decode()
    prompt = _PROMPT_VERIFY.format(produto=label)

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer {}".format(args.groq_api_key),
                     "Content-Type": "application/json"},
            json={
                "model":      args.groq_model,
                "max_tokens": 100,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": "data:image/jpeg;base64,{}".format(b64)}},
                    {"type": "text", "text": prompt},
                ]}],
            },
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log("VISION_ERRO: {}".format(exc))
        return None

    raw = re.sub(r"```json\s*|\s*```", "", raw).strip()
    try:
        data            = json.loads(raw)
        corresponde     = bool(data.get("corresponde", True))
        produto_visivel = str(data.get("produto_visivel", ""))
        motivo          = str(data.get("motivo", ""))
        log("VIT_VERIFY: \"{}\" visivel=\"{}\" corresponde={} | {}".format(
            label[:30], produto_visivel[:30], corresponde, motivo[:40]))
        return {"corresponde": corresponde, "produto_visivel": produto_visivel, "motivo": motivo}
    except Exception as exc:
        log("VISION_JSON_ERRO: {} raw={}".format(exc, raw[:100]))
        return None


def vision_describe(jpeg, args):
    """Descreve a cena — usado apenas pelo bot sob demanda."""
    if not args.groq_api_key:
        return "Visão não configurada."
    b64 = base64.b64encode(jpeg).decode()
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer {}".format(args.groq_api_key),
                     "Content-Type": "application/json"},
            json={
                "model":      args.groq_model,
                "max_tokens": 80,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": "data:image/jpeg;base64,{}".format(b64)}},
                    {"type": "text", "text": _PROMPT_DESCRIBE},
                ]}],
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log("VISION_ERRO: {}".format(exc))
        return "Erro na análise: {}.".format(str(exc)[:60])


# ─────────────────────────────── Telegram ────────────────────────────────────

def telegram_send(token, chat_id, text, jpeg=None, reply_markup=None):
    if not token or not chat_id:
        return
    try:
        data = {"chat_id": chat_id}
        if reply_markup:
            data["reply_markup"] = reply_markup
        if jpeg:
            data["caption"] = text[:1024]
            r = requests.post(
                "https://api.telegram.org/bot{}/sendPhoto".format(token),
                data=data,
                files={"photo": ("frame.jpg", jpeg, "image/jpeg")},
                timeout=20,
            )
        else:
            data["text"] = text[:4096]
            r = requests.post(
                "https://api.telegram.org/bot{}/sendMessage".format(token),
                data=data,
                timeout=10,
            )
        if not r.ok:
            log("TELEGRAM_ERRO: {} {}".format(r.status_code, r.text[:80]))
    except Exception as exc:
        log("TELEGRAM_FALHOU: {}".format(exc))


# ─────────────────────────────── Log ─────────────────────────────────────────

def save_entry(outdir, record):
    root = Path(outdir)
    path = root / datetime.now().strftime("%Y%m%d") / "activity.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─────────────────────────────── Bot Telegram ────────────────────────────────

_BOT_KEYBOARD = json.dumps({"inline_keyboard": [[
    {"text": "📷 Ver caixa agora", "callback_data": "ver"},
]]})

_alert_registry = {}


def save_feedback(outdir, kind, record):
    root = Path(outdir).parent / "feedback"
    root.mkdir(parents=True, exist_ok=True)
    fname = "confirmed.jsonl" if kind == "ok" else "dismissed.jsonl"
    with (root / fname).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _snapshot_e_descreve(args):
    """Tira foto e descreve — usado pelo bot sob demanda."""
    try:
        jpeg = snapshot(args)
    except Exception as exc:
        return None, "Erro na câmera: {}".format(exc)

    hora   = datetime.now().strftime("%H:%M:%S")
    recent = get_recent_vit(args, 30)

    if recent:
        vit    = recent[-1]
        result = vision_verify_vit(jpeg, args, vit["nome"])
        if result:
            icon = "✅" if result["corresponde"] else "⚠️"
            msg  = "{} Caixa {} | {}\nVIT: {}\nVisível: {}\n{}".format(
                icon, args.pdv_station, hora,
                vit["nome"], result["produto_visivel"],
                result["motivo"].capitalize())
        else:
            desc = vision_describe(jpeg, args)
            msg  = "Caixa {} | {}\n{}\nVIT recente: {}".format(
                args.pdv_station, hora, desc, vit["nome"])
    else:
        desc = vision_describe(jpeg, args)
        msg  = "Caixa {} | {}\n{}\nSem VIT nos últimos 30s.".format(
            args.pdv_station, hora, desc)

    return jpeg, msg


def _responder_bot(args, chat_id, callback_query_id=None):
    token = args.telegram_token
    if callback_query_id:
        try:
            requests.post(
                "https://api.telegram.org/bot{}/answerCallbackQuery".format(token),
                data={"callback_query_id": callback_query_id,
                      "text": "Consultando câmera..."},
                timeout=5,
            )
        except Exception:
            pass
    jpeg, msg = _snapshot_e_descreve(args)
    telegram_send(token, chat_id, msg, jpeg, reply_markup=_BOT_KEYBOARD)


def _prefetch_video(args, alert_id, alerta_dt):
    """Inicia download do vídeo em background logo após o alerta."""
    import subprocess
    from urllib.parse import quote

    if not args.imhdx_host:
        return

    vid_dir  = Path(args.outdir) / alerta_dt.strftime("%Y%m%d")
    vid_dir.mkdir(parents=True, exist_ok=True)
    dav_path = str(vid_dir / "clip_{}.dav".format(alert_id))
    mp4_path = str(vid_dir / "clip_{}.mp4".format(alert_id))

    inicio    = alerta_dt - timedelta(seconds=10)
    fim       = alerta_dt + timedelta(seconds=15)
    start_str = quote(inicio.strftime("%Y-%m-%d %H:%M:%S"))
    end_str   = quote(fim.strftime("%Y-%m-%d %H:%M:%S"))

    def _dl(subtype, max_mb):
        url = ("http://{}/cgi-bin/loadfile.cgi"
               "?action=startLoad&channel={}&subtype={}&startTime={}&endTime={}").format(
            args.imhdx_host, args.imhdx_channel, subtype, start_str, end_str)
        try:
            r = requests.get(url, auth=HTTPDigestAuth(args.imhdx_user, args.imhdx_pass),
                             timeout=30, stream=True)
            if r.status_code != 200:
                r.close()
                return 0
            recv = 0
            with open(dav_path, "wb") as fh:
                for chunk in r.iter_content(65536):
                    if chunk:
                        fh.write(chunk)
                        recv += len(chunk)
                        if recv >= max_mb * 1024 * 1024:
                            break
            r.close()
            return recv
        except Exception:
            return 0

    for espera in [30, 35]:
        log("VIDEO_PREFETCH: aguardando {}s para {}...".format(espera, alert_id))
        time.sleep(espera)
        recv = _dl(1, 6)
        if recv < 2048:
            recv = _dl(0, 20)
        if recv >= 2048:
            break

    if recv < 2048:
        if alert_id in _alert_registry:
            _alert_registry[alert_id]["video_failed"] = True
        log("VIDEO_PREFETCH: gravacao nao disponivel para {}".format(alert_id))
        return

    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", dav_path,
             "-vf", "scale=640:360", "-c:v", "libx264",
             "-preset", "ultrafast", "-crf", "28", "-an", mp4_path],
            capture_output=True, timeout=90,
        )
        if proc.returncode == 0 and os.path.exists(mp4_path):
            if alert_id in _alert_registry:
                _alert_registry[alert_id]["video_ready"] = mp4_path
            log("VIDEO_PREFETCH: pronto {} ({:.1f}MB)".format(
                alert_id, os.path.getsize(mp4_path) / 1024 / 1024))
        else:
            log("VIDEO_PREFETCH: ffmpeg falhou para {}".format(alert_id))
    except Exception as exc:
        log("VIDEO_PREFETCH_ERRO: {}".format(exc))
    finally:
        try:
            if os.path.exists(dav_path):
                os.unlink(dav_path)
        except Exception:
            pass


def _send_alert_video_clip(args, chat_id, alert_id):
    token = args.telegram_token

    def responder(text):
        telegram_send(token, chat_id, text)

    if not args.imhdx_host or not args.imhdx_user or not args.imhdx_pass:
        responder("❌ Não foi possível obter o vídeo: iMHDX não configurado neste PDV.")
        return

    for _ in range(18):
        meta = _alert_registry.get(alert_id, {})
        if meta.get("video_ready") or meta.get("video_failed"):
            break
        time.sleep(5)

    meta     = _alert_registry.get(alert_id, {})
    mp4_path = meta.get("video_ready")

    if not mp4_path or not os.path.exists(mp4_path):
        responder("❌ Não foi possível obter o vídeo: gravação não encontrada no iMHDX para este horário.")
        return

    try:
        alerta_dt = datetime.strptime(meta.get("time", ""), "%Y-%m-%d %H:%M:%S")
        hora      = alerta_dt.strftime("%H:%M:%S")
    except Exception:
        hora = "?"

    caption = "📹 Clipe do alerta — Caixa {} às {}".format(args.pdv_station, hora)

    try:
        size_mb = os.path.getsize(mp4_path) / (1024 * 1024)
        if size_mb > 50:
            caption += "\n⚠️ Arquivo grande ({:.1f} MB).".format(size_mb)
            with open(mp4_path, "rb") as vf:
                requests.post(
                    "https://api.telegram.org/bot{}/sendDocument".format(token),
                    data={"chat_id": chat_id, "caption": caption[:1024]},
                    files={"document": ("clip.mp4", vf, "video/mp4")},
                    timeout=120,
                )
        else:
            with open(mp4_path, "rb") as vf:
                requests.post(
                    "https://api.telegram.org/bot{}/sendVideo".format(token),
                    data={"chat_id": chat_id, "caption": caption[:1024]},
                    files={"video": ("clip.mp4", vf, "video/mp4")},
                    timeout=120,
                )
        log("VIDEO_ENVIADO: alert_id={} tamanho={:.1f}MB".format(alert_id, size_mb))
    except Exception as exc:
        log("VIDEO_ERRO: {}".format(exc))
        responder("❌ Não foi possível obter o vídeo: {}.".format(str(exc)[:80]))


def bot_poll_loop(args):
    if not args.telegram_token or not args.telegram_chat_id:
        return

    token  = args.telegram_token
    offset = 0

    try:
        telegram_send(token, args.telegram_chat_id,
                      "Monitor PDV {} iniciado.".format(args.pdv_station),
                      reply_markup=_BOT_KEYBOARD)
    except Exception:
        pass

    while True:
        try:
            r = requests.get(
                "https://api.telegram.org/bot{}/getUpdates".format(token),
                params={"offset": offset, "timeout": 30,
                        "allowed_updates": ["callback_query", "message"]},
                timeout=35,
            )
            if not r.ok:
                time.sleep(5)
                continue

            for update in r.json().get("result", []):
                offset = update["update_id"] + 1

                cq = update.get("callback_query")
                if cq:
                    data    = cq.get("data", "")
                    chat_id = cq["message"]["chat"]["id"]
                    cq_id   = cq["id"]

                    if data == "ver":
                        threading.Thread(
                            target=_responder_bot,
                            args=(args, chat_id, cq_id),
                            daemon=True,
                        ).start()
                        continue

                    if data.startswith("atf_video:"):
                        alert_id = data.split(":", 1)[1]
                        try:
                            requests.post(
                                "https://api.telegram.org/bot{}/answerCallbackQuery".format(token),
                                data={"callback_query_id": cq_id,
                                      "text": "⏳ Buscando clipe, aguarde..."},
                                timeout=5,
                            )
                        except Exception:
                            pass
                        telegram_send(token, chat_id, "⏳ Buscando clipe... aguarde.")
                        threading.Thread(
                            target=_send_alert_video_clip,
                            args=(args, chat_id, alert_id),
                            daemon=True,
                        ).start()
                        continue

                    if data.startswith("atf_ok:") or data.startswith("atf_no:"):
                        kind     = "ok" if data.startswith("atf_ok:") else "no"
                        alert_id = data.split(":", 1)[1]
                        meta     = _alert_registry.get(alert_id, {})
                        record   = {
                            "alert_id": alert_id,
                            "image":    meta.get("image"),
                            "time":     meta.get("time"),
                            "pdv":      meta.get("pdv", args.pdv_station),
                            "produto":  meta.get("produto"),
                            "visivel":  meta.get("visivel"),
                        }
                        save_feedback(args.outdir, kind, record)
                        label = "Fraude real" if kind == "ok" else "Falso positivo"
                        log("FEEDBACK {}: alert_id={}".format(label.upper(), alert_id))
                        try:
                            requests.post(
                                "https://api.telegram.org/bot{}/answerCallbackQuery".format(token),
                                data={"callback_query_id": cq_id,
                                      "text": "✅ Registrado. Obrigado!"},
                                timeout=5,
                            )
                        except Exception:
                            pass
                        continue

                msg  = update.get("message", {})
                text = msg.get("text", "")
                if text.startswith(("/ver", "/foto", "/caixa")):
                    chat_id = msg["chat"]["id"]
                    threading.Thread(
                        target=_responder_bot,
                        args=(args, chat_id),
                        daemon=True,
                    ).start()

        except Exception as exc:
            log("BOT_POLL_ERRO: {}".format(exc))
            time.sleep(10)


# ─────────────────────────────── Loop principal ──────────────────────────────

def run(args):
    if not args.groq_api_key and not args.skip_vision:
        log("AVISO: GROQ_API_KEY nao configurado — vision desabilitada")
        args.skip_vision = True

    log("MONITOR_INICIO PDV={} camera={} vision={} poll={}s snap_delay={}s".format(
        args.pdv_station, args.camera_host,
        "groq:{}".format(args.groq_model.split("/")[-1]) if not args.skip_vision else "desabilitada",
        args.interval, args.snap_delay))

    threading.Thread(target=bot_poll_loop, args=(args,), daemon=True).start()

    start              = datetime.now()
    last_verified_time = None   # datetime do último VIT verificado
    last_alert         = datetime.min

    while args.duration <= 0 or (datetime.now() - start).total_seconds() < args.duration:

        try:
            vit = get_latest_vit(args)
        except Exception as exc:
            log("SPY_ERRO: {}".format(exc))
            time.sleep(args.interval)
            continue

        # Nenhum VIT hoje ainda
        if vit is None:
            time.sleep(args.interval)
            continue

        # Já verificamos este VIT
        if last_verified_time and vit["time"] <= last_verified_time:
            time.sleep(args.interval)
            continue

        # ── Novo VIT detectado ────────────────────────────────────────────────
        label = vit["label"]
        log("NOVO_VIT: PDV{} {} | {}".format(
            args.pdv_station, vit["time"].strftime("%H:%M:%S"), label))

        # Aguarda snap_delay para o produto ainda estar visível no frame
        time.sleep(args.snap_delay)

        if args.skip_vision:
            last_verified_time = vit["time"]
            continue

        try:
            jpeg = snapshot(args)
        except Exception as exc:
            log("CAMERA_ERRO: {}".format(exc))
            last_verified_time = vit["time"]
            continue

        result = vision_verify_vit(jpeg, args, label)
        last_verified_time = vit["time"]

        if result is None:
            continue

        if result["corresponde"]:
            log("OK PDV{} {} | {} → \"{}\"".format(
                args.pdv_station,
                vit["time"].strftime("%H:%M:%S"),
                label[:30],
                result["produto_visivel"][:40]))
            continue

        # ── Divergência detectada ─────────────────────────────────────────────
        now  = datetime.now()
        hora = now.strftime("%H:%M:%S")

        save_entry(args.outdir, {
            "time":            now.strftime("%Y-%m-%d %H:%M:%S"),
            "vit_time":        vit["time"].strftime("%H:%M:%S"),
            "produto_pdv":     label,
            "produto_visivel": result["produto_visivel"],
            "motivo":          result["motivo"],
        })

        if (now - last_alert).total_seconds() < 60:
            log("ALERTA_COOLDOWN: {} | {}".format(hora, label))
            continue

        last_alert = now
        alert_id   = "{}_{}".format(args.pdv_station, now.strftime("%Y%m%d_%H%M%S"))

        img_path = None
        try:
            img_dir  = Path(args.outdir) / now.strftime("%Y%m%d")
            img_dir.mkdir(parents=True, exist_ok=True)
            img_path = str(img_dir / "alert_{}.jpg".format(alert_id))
            Path(img_path).write_bytes(jpeg)
        except Exception as exc:
            log("ERRO salvando imagem: {}".format(exc))

        _alert_registry[alert_id] = {
            "image":   img_path,
            "time":    now.strftime("%Y-%m-%d %H:%M:%S"),
            "pdv":     args.pdv_station,
            "produto": label,
            "visivel": result["produto_visivel"],
        }

        feedback_keyboard = json.dumps({"inline_keyboard": [[
            {"text": "✅ Fraude real",    "callback_data": "atf_ok:{}".format(alert_id)},
            {"text": "❌ Falso positivo", "callback_data": "atf_no:{}".format(alert_id)},
            {"text": "📹 Ver vídeo",      "callback_data": "atf_video:{}".format(alert_id)},
        ]]})

        msg = (
            "⚠️ Caixa {} — {}\n"
            "PDV registrou: {}\n"
            "Câmera viu: {}\n"
            "{}"
        ).format(
            args.pdv_station, hora,
            label,
            result["produto_visivel"],
            result["motivo"].capitalize(),
        )
        telegram_send(args.telegram_token, args.telegram_chat_id,
                      msg, jpeg, reply_markup=feedback_keyboard)
        log("TELEGRAM_ALERTA: pdv={} | {} → \"{}\" | alert_id={}".format(
            label, result["produto_visivel"], alert_id, alert_id))

        if args.imhdx_host:
            threading.Thread(
                target=_prefetch_video,
                args=(args, alert_id, now),
                daemon=True,
            ).start()


def main():
    args = parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()