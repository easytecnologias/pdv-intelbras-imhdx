#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, "/opt/pdv-telegram-assistant")

import pdv_telegram_assistant as bot


STATE_PATH = Path("/var/lib/pdv-visual-auditor/alert_worker_state.json")
POLL_SECONDS = float(os.environ.get("VISUAL_ALERT_POLL_SECONDS", "1"))
MIN_VALUE = float(os.environ.get("VISUAL_ALERT_MIN_VALUE", "20"))
SEND_CONFERE = os.environ.get("VISUAL_ALERT_SEND_CONFERE", "0") == "1"
START_FRESH = os.environ.get("VISUAL_ALERT_START_FRESH", "1") == "1"
DELAY_SECONDS = float(os.environ.get("VISUAL_ALERT_DELAY_SECONDS", "6"))
IMAGE_RETRY_SECONDS = float(
    os.environ.get("VISUAL_ALERT_IMAGE_RETRY_SECONDS", "8")
)
MAX_IMAGE_ATTEMPTS = int(
    os.environ.get("VISUAL_ALERT_MAX_IMAGE_ATTEMPTS", "6")
)
PHANTOM_ENABLED = os.environ.get("VISUAL_PHANTOM_ENABLED", "1") == "1"
PHANTOM_MOTION_MEAN_MIN = float(
    os.environ.get("VISUAL_PHANTOM_MOTION_MEAN_MIN", "4.5")
)
PHANTOM_CHANGED_PIXELS_MIN = float(
    os.environ.get("VISUAL_PHANTOM_CHANGED_PIXELS_MIN", "7.0")
)
LIVE_BUFFER_ENABLED = (
    os.environ.get("VISUAL_IMHDX_LIVE_BUFFER_ENABLED", "1") == "1"
)
LIVE_BUFFER_FPS = float(
    os.environ.get("VISUAL_IMHDX_LIVE_BUFFER_FPS", "2")
)
LIVE_BUFFER_DIR = Path("/var/lib/pdv-visual-auditor/live_frames")
RISK_WORDS = (
    "CARNE",
    "BOV",
    "SUIN",
    "FRANGO",
    "PEIXE",
    "CERV",
    "WHISKY",
    "AZEITE",
    "SABAO",
    "REFRIGERANTE",
    "REFRI",
    "CIGARRO",
)


def log(message):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message), flush=True)


class ImhdxLiveBuffer:
    def __init__(self, args):
        self.args = args
        self.process = None
        LIVE_BUFFER_DIR.mkdir(parents=True, exist_ok=True)

    def start(self):
        if not LIVE_BUFFER_ENABLED:
            return False
        if not (
            self.args.imhdx_host
            and self.args.imhdx_user
            and self.args.imhdx_pass
        ):
            log("buffer ao vivo desativado: credenciais iMHDX ausentes")
            return False
        if self.process and self.process.poll() is None:
            return True

        self.stop()

        for path in LIVE_BUFFER_DIR.glob("frame_*.jpg"):
            path.unlink(missing_ok=True)

        user = quote(self.args.imhdx_user, safe="")
        password = quote(self.args.imhdx_pass, safe="")
        url = (
            "rtsp://%s:%s@%s:554/cam/realmonitor?channel=%s&subtype=0"
            % (
                user,
                password,
                self.args.imhdx_host,
                self.args.imhdx_channel,
            )
        )
        pattern = str(LIVE_BUFFER_DIR / "frame_%09d.jpg")
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-use_wallclock_as_timestamps",
                "1",
                "-i",
                url,
                "-vf",
                "fps=%.3f" % LIVE_BUFFER_FPS,
                "-q:v",
                "4",
                pattern,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log("buffer ao vivo iMHDX iniciado fps=%.2f" % LIVE_BUFFER_FPS)
        return True

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        else:
            self.process.wait()
        self.process = None

    def ensure_running(self):
        if not LIVE_BUFFER_ENABLED:
            return False
        if self.process is None or self.process.poll() is not None:
            return self.start()
        return True

    def cleanup(self, keep_seconds=30):
        cutoff = time.time() - keep_seconds
        for path in LIVE_BUFFER_DIR.glob("frame_*.jpg"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def frames_near(self, detected_at):
        if not self.ensure_running():
            return []
        self.cleanup()
        all_mtimes = []
        candidates = []
        for path in LIVE_BUFFER_DIR.glob("frame_*.jpg"):
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            all_mtimes.append(modified)
            if detected_at - 3.5 <= modified <= detected_at + 2.0:
                candidates.append((modified, path))
        candidates.sort()
        paths = [path for _, path in candidates]
        if all_mtimes:
            offsets = sorted(round(m - detected_at, 2) for m in all_mtimes)
            log(
                "buffer ao vivo: %s frames disponiveis, offsets=%s, selecionados=%s"
                % (len(all_mtimes), offsets, [round(m - detected_at, 2) for m, _ in candidates])
            )
        if len(paths) <= 3:
            return paths
        return [paths[0], paths[len(paths) // 2], paths[-1]]


def clean_bot_args():
    sys.argv = [sys.argv[0]]


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"seen": []}


def save_seen(seen):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"seen": sorted(seen)}, sort_keys=True))
    tmp_path.replace(STATE_PATH)


def item_key(cupom, item):
    return "|".join(
        [
            str(cupom),
            str(item.get("time", "")),
            str(item.get("code", "")),
            str(item.get("desc", "")),
            str(item.get("qty", "")),
            "%.2f" % float(item.get("value") or 0),
        ]
    )


def iter_items(args):
    cups, _, _ = bot.read_sales(args)
    for cup in cups:
        cupom = cup.get("number", "")
        for item in cup.get("items", []):
            yield cup, item, item_key(cupom, item)


def item_needs_product_audit(item):
    description = bot.normalize_text(item.get("desc", ""))
    unit_value = bot.item_unit_value(item)
    line_total = bot.money(item.get("value"))
    return (
        bool(item.get("consultations"))
        or line_total >= MIN_VALUE
        or unit_value >= MIN_VALUE
        or any(word in description for word in RISK_WORDS)
    )


def send_alert(args, response, elapsed, cup, item):
    reason = response.pop("alert_reason", None)
    if not reason:
        reason = (
            "VENDA APOS CONSULTA"
            if item.get("consultations")
            else "DIVERGENCIA VISUAL"
        )
    prefix = (
        "ALERTA VISUAL EM TEMPO REAL\n"
        "Motivo: %s\n"
        "Tempo: %.1fs\n\n"
    ) % (reason, elapsed)
    response = dict(response)
    request_id = bot.save_visual_video_request(
        args,
        cup.get("number", ""),
        item,
    )
    response["reply_markup"] = bot.visual_video_keyboard(request_id)
    if response.get("caption"):
        response["caption"] = prefix + response["caption"]
    elif response.get("text"):
        response["text"] = prefix + response["text"]
    bot.send_response(args, response)


def live_event_from_frames(args, cupom, item, frame_paths):
    if len(frame_paths) < 2:
        return None

    out_dir = Path(args.state_dir) / "imhdx"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = "%s_%s_live" % (
        time.strftime("%Y%m%d_%H%M%S"),
        item.get("code") or "item",
    )
    source = "Transmissao ao vivo PDV%s / iMHDX" % int(args.pdv_station)
    original_path = out_dir / ("%s.jpg" % stamp)
    sequence_path = out_dir / ("%s_sequence.jpg" % stamp)

    panels = []
    labels = ("ANTES", "BIP", "DEPOIS")
    for index, frame_path in enumerate(frame_paths[:3]):
        image = bot.Image.open(str(frame_path)).convert("RGB")
        if index == min(1, len(frame_paths) - 1):
            image.save(str(original_path), quality=88)
        width, height = image.size
        panel = image.crop(
            (
                int(width * 0.35),
                int(height * 0.22),
                min(width, int(width * 0.97)),
                min(height, int(height * 0.97)),
            )
        )
        resampling = getattr(bot.Image, "Resampling", bot.Image)
        panel.thumbnail((640, 500), resampling.LANCZOS)
        canvas = bot.Image.new("RGB", (640, 520), "black")
        canvas.paste(
            panel,
            ((640 - panel.width) // 2, 20 + (500 - panel.height) // 2),
        )
        draw = bot.ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 640, 28), fill=(0, 0, 0))
        draw.text(
            (12, 5),
            labels[min(index, len(labels) - 1)],
            fill=(255, 220, 0),
        )
        panels.append(canvas)

    sequence = bot.Image.new("RGB", (640 * len(panels), 520), "black")
    for index, panel in enumerate(panels):
        sequence.paste(panel, (640 * index, 0))
    sequence.save(str(sequence_path), quality=88)
    stamped_path = bot.overlay_pdv_caption(
        args,
        original_path,
        cupom,
        item,
        source,
    )
    return {
        "imagem": stamped_path,
        "imagem_original": str(original_path),
        "auditoria_imagem": str(sequence_path),
        "frames_analisados": len(panels),
        "movimento_scanner": bot.sequence_motion_score(
            sequence_path,
            panel_count=len(panels),
        ),
        "fonte": source,
    }


def audit_exact_item(args, cup, item, live_frames=None):
    cupom = cup.get("number", "")
    event = live_event_from_frames(
        args,
        cupom,
        item,
        live_frames or [],
    )
    if not event:
        event = bot.imhdx_sequence_for_item(args, cupom, item)
    if not event:
        return {
            "retry_later": True,
            "text": (
                "Auditoria visual em tempo real\n\n"
                "Achei o item, mas a gravacao do iMHDX ainda nao estava "
                "disponivel perto do horario.\n"
                "Cupom %s %s - %s x %s - %s"
            )
            % (
                cupom,
                item.get("time", ""),
                item.get("qty", ""),
                item.get("desc", "").title(),
                bot.money_br(item.get("value", 0.0)),
            )
        }

    image_path = event.get("auditoria_imagem") or event["imagem"]
    source = event.get("fonte", "Gravacao iMHDX")
    motion = event.get("movimento_scanner") or {}
    product_audit = item_needs_product_audit(item)
    passage_detected = (
        float(motion.get("media") or 0) >= PHANTOM_MOTION_MEAN_MIN
        or float(motion.get("pixels_alterados") or 0)
        >= PHANTOM_CHANGED_PIXELS_MIN
    )
    log(
        "triagem cupom=%s produto=%s auditoria_produto=%s "
        "movimento_media=%.2f pixels_alterados=%.2f passagem=%s"
        % (
            cupom,
            item.get("desc", ""),
            product_audit,
            float(motion.get("media") or 0),
            float(motion.get("pixels_alterados") or 0),
            passage_detected,
        )
    )

    if product_audit:
        audit = bot.run_visual_auditor(
            image_path,
            item,
            mode="produto",
            force_api=bool(item.get("consultations")),
            cupom=cupom,
        )
    elif not PHANTOM_ENABLED or passage_detected:
        audit = {
            "resultado": "CONFERE",
            "confianca": 100,
            "o_que_aparece_na_imagem": "",
            "comparacao_pdv": (
                "O filtro local detectou passagem visual na area do scanner."
            ),
            "possivel_divergencia": "",
            "acao_recomendada": "liberar",
            "economizou_api": True,
        }
    else:
        audit = bot.run_visual_auditor(
            image_path,
            item,
            mode="presenca",
            force_api=True,
        )
        if audit.get("tipo_alerta") == "REGISTRO_SEM_PASSAGEM_VISUAL":
            audit["alert_reason"] = "REGISTRO SEM PASSAGEM VISUAL"

    audit["movimento_scanner"] = motion
    if event.get("frames_analisados"):
        audit["frames_analisados"] = event["frames_analisados"]
    caption = bot.format_visual_audit_caption(
        args, cupom, item, audit, source
    )
    response = {"photo": event["imagem"], "caption": caption}
    if audit.get("alert_reason"):
        response["alert_reason"] = audit["alert_reason"]
    return response


def should_send_response(response):
    text = response.get("caption") or response.get("text") or ""
    if "Resultado:" not in text:
        return False
    if "Resultado: NAO_ANALISADO" in text:
        return False
    if SEND_CONFERE:
        return (
            "Resultado: CONFERE" in text
            or "Resultado: NAO_CONFERE" in text
        )
    return "Resultado: NAO_CONFERE" in text


def _handle_shutdown_signal(signum, frame):
    raise SystemExit(0)


def main():
    clean_bot_args()
    args = bot.parse_args()
    live_buffer = ImhdxLiveBuffer(args)
    live_buffer.start()
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    state = load_state()
    seen = set(state.get("seen", []))

    if START_FRESH:
        seen = {key for _, _, key in iter_items(args)}
        save_seen(seen)
        log("baseline criado com %s itens; aguardando novos itens" % len(seen))

    log(
        "worker iMHDX iniciado poll=%ss min_value=%.2f delay=%.1fs "
        "imagem_retry=%.1fs tentativas=%s phantom=%s "
        "movimento_min=%.2f pixels_min=%.2f live_buffer=%s"
        % (
            POLL_SECONDS,
            MIN_VALUE,
            DELAY_SECONDS,
            IMAGE_RETRY_SECONDS,
            MAX_IMAGE_ATTEMPTS,
            PHANTOM_ENABLED,
            PHANTOM_MOTION_MEAN_MIN,
            PHANTOM_CHANGED_PIXELS_MIN,
            LIVE_BUFFER_ENABLED,
        )
    )
    pending = {}
    last_live_cleanup = 0.0

    try:
        while True:
            try:
                if time.monotonic() - last_live_cleanup >= 10:
                    live_buffer.cleanup()
                    last_live_cleanup = time.monotonic()

                for cup, item, key in iter_items(args):
                    if key in seen:
                        continue

                    seen.add(key)
                    save_seen(seen)

                    pending[key] = {
                        "cup": cup,
                        "item": item,
                        "created_at": time.monotonic(),
                        "detected_at": time.time(),
                        "next_attempt_at": time.monotonic() + DELAY_SECONDS,
                        "attempts": 0,
                    }
                    log(
                        "agendado cupom=%s item=%s qtd=%s valor_unit=%.2f "
                        "total=%.2f produto_audit=%s delay=%.1fs"
                        % (
                            cup.get("number", ""),
                            item.get("desc", ""),
                            item.get("qty", ""),
                            bot.item_unit_value(item),
                            bot.money(item.get("value")),
                            item_needs_product_audit(item),
                            DELAY_SECONDS,
                        )
                    )

                ready_keys = [
                    key
                    for key, row in pending.items()
                    if time.monotonic() >= row["next_attempt_at"]
                ]
                for key in ready_keys:
                    row = pending[key]
                    cup = row["cup"]
                    item = row["item"]
                    row["attempts"] += 1
                    started = time.monotonic()
                    log(
                        "auditando tentativa=%s/%s cupom=%s item=%s qtd=%s "
                        "valor_unit=%.2f total=%.2f"
                        % (
                            row["attempts"],
                            MAX_IMAGE_ATTEMPTS,
                            cup.get("number", ""),
                            item.get("desc", ""),
                            item.get("qty", ""),
                            bot.item_unit_value(item),
                            bot.money(item.get("value")),
                        )
                    )
                    live_frames = live_buffer.frames_near(row["detected_at"])
                    response = audit_exact_item(
                        args,
                        cup,
                        item,
                        live_frames=live_frames,
                    )
                    elapsed = time.monotonic() - started
                    if response.get("retry_later"):
                        if row["attempts"] < MAX_IMAGE_ATTEMPTS:
                            row["next_attempt_at"] = (
                                time.monotonic() + IMAGE_RETRY_SECONDS
                            )
                            log(
                                "imagem ainda indisponivel; nova tentativa em %.1fs"
                                % IMAGE_RETRY_SECONDS
                            )
                        else:
                            pending.pop(key, None)
                            log(
                                "imagem indisponivel apos %s tentativas; "
                                "item encerrado sem alerta"
                                % MAX_IMAGE_ATTEMPTS
                            )
                        continue

                    pending.pop(key, None)
                    send = should_send_response(response)
                    log("auditado em %.2fs enviar=%s" % (elapsed, send))
                    if send:
                        send_alert(args, response, elapsed, cup, item)

                time.sleep(POLL_SECONDS)
            except Exception as exc:
                log("erro: %s: %s" % (type(exc).__name__, exc))
                time.sleep(5)
    finally:
        live_buffer.stop()
        log("buffer ao vivo parado")


if __name__ == "__main__":
    main()
