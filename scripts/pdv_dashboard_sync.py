#!/usr/bin/env python3
"""Sincroniza eventos da auditoria visual e a saude dos servicos do PDV
com a API do dashboard Easy Auditoria."""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, "/opt/pdv-telegram-assistant")

import pdv_telegram_assistant as bot  # noqa: E402

SERVICOS_HEALTH = (
    "pdv-intelbras-bridge",
    "pdv-telegram-assistant",
    "pdv-visual-alert-worker",
)

VIDEO_RETRY_MIN_AGE_SECONDS = 90
VIDEO_RETRY_MAX_TENTATIVAS = 5

FECHACUPOM_RE = re.compile(r":FECHACUPOM \| Cod: \S+ \| Descricao:.*?\| VlTotal: ([\d.]+)\|")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sincroniza resultados da auditoria visual com o dashboard Easy Auditoria."
    )
    parser.add_argument("--api-url", default=os.environ.get("DASHBOARD_API_URL", ""))
    parser.add_argument("--api-token", default=os.environ.get("DASHBOARD_API_TOKEN", ""))
    parser.add_argument("--pdv-station", default=os.environ.get("PDV_STATION", "001"))
    parser.add_argument(
        "--results-file",
        default=os.environ.get("VISUAL_AUDITOR_RESULTS_FILE", "/var/lib/pdv-visual-auditor/results.jsonl"),
    )
    parser.add_argument(
        "--offset-file",
        default=os.environ.get(
            "DASHBOARD_SYNC_OFFSET_FILE", "/var/lib/pdv-visual-auditor/dashboard_sync_offset"
        ),
    )
    parser.add_argument(
        "--timeout", type=float, default=float(os.environ.get("DASHBOARD_SYNC_TIMEOUT", "10"))
    )
    parser.add_argument(
        "--pending-videos-file",
        default=os.environ.get(
            "DASHBOARD_PENDING_VIDEOS_FILE", "/var/lib/pdv-visual-auditor/dashboard_pending_videos.jsonl"
        ),
    )
    parser.add_argument(
        "--video-retry-dir",
        default=os.environ.get("DASHBOARD_VIDEO_RETRY_DIR", "/var/lib/pdv-visual-auditor/videos_retry"),
    )
    parser.add_argument("--imhdx-host", default=os.environ.get("IMHDX_HOST", ""))
    parser.add_argument("--imhdx-user", default=os.environ.get("IMHDX_USER", ""))
    parser.add_argument("--imhdx-pass", default=os.environ.get("IMHDX_PASS", ""))
    parser.add_argument("--imhdx-channel", type=int, default=int(os.environ.get("IMHDX_CHANNEL", "1")))
    parser.add_argument("--pdv-base-dir", default=os.environ.get("PDV_BASE_DIR", "/home/rpdv/frente"))
    parser.add_argument("--state-dir", default=os.environ.get("BOT_STATE_DIR", "/var/lib/pdv-telegram-assistant"))
    parser.add_argument(
        "--sales-backfill-days",
        type=int,
        default=int(os.environ.get("DASHBOARD_SALES_BACKFILL_DAYS", "30")),
    )
    parser.add_argument(
        "--sales-synced-file",
        default=os.environ.get(
            "DASHBOARD_SALES_SYNCED_FILE", "/var/lib/pdv-visual-auditor/dashboard_sales_synced"
        ),
    )
    return parser.parse_args()


def read_offset(path):
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return 0


def write_offset(path, offset):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(str(offset))


def deve_ignorar(registro):
    resultado = registro.get("resultado") or {}
    if resultado.get("economizou_api"):
        return True
    if resultado.get("resultado") == "CONFERE_POR_REGRA_DE_VALOR":
        return True
    return False


def ler_novos_eventos(results_file, offset_file):
    path = Path(results_file)
    offset = read_offset(offset_file)
    if not path.is_file():
        return [], offset

    eventos = []
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        for linha in handle:
            linha = linha.strip()
            if not linha:
                continue
            try:
                registro = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if deve_ignorar(registro):
                continue
            eventos.append(registro)
        offset = handle.tell()
    return eventos, offset


def montar_evento(registro, pdv_station):
    return {
        "timestamp": registro.get("timestamp"),
        "pdv": pdv_station,
        "imagem": registro.get("imagem"),
        "cupom": registro.get("cupom"),
        "produto": registro.get("produto"),
        "valor_unitario": registro.get("valor_unitario"),
        "quantidade": registro.get("quantidade"),
        "modo": registro.get("modo"),
        "resultado": registro.get("resultado"),
    }


def enviar_imagem_evento(api_url, api_token, evento_id, imagem_path, timeout):
    caminho = Path(imagem_path)
    if not caminho.is_file():
        return
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        with caminho.open("rb") as arquivo:
            requests.post(
                f"{api_url}/api/v1/events/{evento_id}/image",
                files={"file": (caminho.name, arquivo, "image/jpeg")},
                headers=headers,
                timeout=timeout,
            )
    except Exception:
        pass


def enviar_video_evento(api_url, api_token, evento_id, video_path, timeout):
    caminho = Path(video_path)
    if not caminho.is_file():
        return
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        with caminho.open("rb") as arquivo:
            requests.post(
                f"{api_url}/api/v1/events/{evento_id}/video",
                files={"file": (caminho.name, arquivo, "video/mp4")},
                headers=headers,
                timeout=timeout,
            )
    except Exception:
        pass


def enviar_eventos(api_url, api_token, eventos, pdv_station, timeout, pending_videos_file=None):
    if not eventos or not api_url or not api_token:
        return
    headers = {"Authorization": f"Bearer {api_token}"}
    for registro in eventos:
        try:
            resposta = requests.post(
                f"{api_url}/api/v1/events",
                json=montar_evento(registro, pdv_station),
                headers=headers,
                timeout=timeout,
            )
            evento_id = resposta.json().get("id")
        except Exception:
            continue
        imagem = registro.get("imagem")
        if evento_id and imagem:
            enviar_imagem_evento(api_url, api_token, evento_id, imagem, timeout)
        video = registro.get("video")
        if evento_id and video:
            enviar_video_evento(api_url, api_token, evento_id, video, timeout)
        elif evento_id and pending_videos_file:
            adicionar_video_pendente(pending_videos_file, evento_id, registro.get("timestamp"))


def ler_videos_pendentes(path):
    arquivo = Path(path)
    if not arquivo.is_file():
        return []
    itens = []
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            itens.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return itens


def gravar_videos_pendentes(path, itens):
    arquivo = Path(path)
    if not itens:
        if arquivo.is_file():
            arquivo.unlink()
        return
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with arquivo.open("w", encoding="utf-8") as handle:
        for item in itens:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def adicionar_video_pendente(path, evento_id, timestamp):
    if not timestamp:
        return
    itens = ler_videos_pendentes(path)
    itens.append({"evento_id": evento_id, "timestamp": timestamp, "tentativas": 0})
    gravar_videos_pendentes(path, itens)


def processar_videos_pendentes(args):
    if not args.api_url or not args.api_token:
        return
    itens = ler_videos_pendentes(args.pending_videos_file)
    if not itens:
        return

    agora = datetime.now()
    restantes = []
    for item in itens:
        try:
            event_dt = datetime.fromisoformat(item["timestamp"])
        except Exception:
            continue

        if (agora - event_dt).total_seconds() < VIDEO_RETRY_MIN_AGE_SECONDS:
            restantes.append(item)
            continue

        evento_id = item["evento_id"]
        video_path = Path(args.video_retry_dir) / f"{evento_id}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            bot.baixar_clipe_imhdx(args, event_dt, args.imhdx_channel, video_path)
        except Exception:
            item["tentativas"] = item.get("tentativas", 0) + 1
            if item["tentativas"] < VIDEO_RETRY_MAX_TENTATIVAS:
                restantes.append(item)
            continue

        enviar_video_evento(args.api_url, args.api_token, evento_id, video_path, args.timeout)

    gravar_videos_pendentes(args.pending_videos_file, restantes)


def coletar_vendas(args, dt=None):
    caminho = bot.spy_path(args, dt)
    if not caminho.is_file():
        return 0.0, 0

    total = 0.0
    cupons = 0
    with caminho.open("r", encoding="latin-1", errors="replace") as handle:
        for linha in handle:
            match = FECHACUPOM_RE.search(linha)
            if not match:
                continue
            total += float(match.group(1))
            cupons += 1
    return total, cupons


def ler_datas_sincronizadas(path):
    arquivo = Path(path)
    if not arquivo.is_file():
        return set()
    return set(arquivo.read_text().split())


def marcar_data_sincronizada(path, data_str):
    arquivo = Path(path)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with arquivo.open("a") as handle:
        handle.write(data_str + "\n")


def _enviar_venda_dia(args, headers, dt):
    total, cupons = coletar_vendas(args, dt)
    requests.post(
        f"{args.api_url}/api/v1/sales",
        json={
            "pdv": args.pdv_station,
            "total": total,
            "cupons": cupons,
            "data": dt.strftime("%Y-%m-%d"),
        },
        headers=headers,
        timeout=args.timeout,
    )


def enviar_vendas(args):
    if not args.api_url or not args.api_token:
        return
    headers = {"Authorization": f"Bearer {args.api_token}"}
    hoje = bot.query_date(args)

    try:
        _enviar_venda_dia(args, headers, hoje)
    except Exception:
        pass

    sincronizadas = ler_datas_sincronizadas(args.sales_synced_file)
    for dias_atras in range(1, args.sales_backfill_days + 1):
        dt = hoje - timedelta(days=dias_atras)
        data_str = dt.strftime("%Y-%m-%d")
        if data_str in sincronizadas:
            continue
        if not bot.spy_path(args, dt).is_file():
            continue
        try:
            _enviar_venda_dia(args, headers, dt)
        except Exception:
            continue
        marcar_data_sincronizada(args.sales_synced_file, data_str)


def estado_servico(nome_servico):
    try:
        saida = subprocess.run(
            ["systemctl", "is-active", nome_servico],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ativo = saida.stdout.strip()
    except Exception:
        return "warning"
    if ativo == "active":
        return "online"
    if ativo in ("inactive", "failed"):
        return "offline"
    return "warning"


def coletar_health():
    return {servico: estado_servico(servico) for servico in SERVICOS_HEALTH}


def montar_health(pdv_station, status):
    return [
        {
            "pdv": pdv_station,
            "bridge": status.get("pdv-intelbras-bridge", "warning"),
            "imhdx": status.get("pdv-telegram-assistant", "warning"),
            "audit": status.get("pdv-visual-alert-worker", "warning"),
        }
    ]


def enviar_health(api_url, api_token, pdv_station, timeout):
    if not api_url or not api_token:
        return
    payload = montar_health(pdv_station, coletar_health())
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        requests.post(
            f"{api_url}/api/v1/health",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except Exception:
        pass


def garantir_offset_inicial(results_file, offset_file):
    """Na primeira execucao, pula o historico acumulado e sincroniza so daqui pra frente."""
    if Path(offset_file).exists():
        return
    path = Path(results_file)
    tamanho = path.stat().st_size if path.is_file() else 0
    write_offset(offset_file, tamanho)


def main():
    args = parse_args()
    garantir_offset_inicial(args.results_file, args.offset_file)
    eventos, offset = ler_novos_eventos(args.results_file, args.offset_file)
    enviar_eventos(
        args.api_url, args.api_token, eventos, args.pdv_station, args.timeout, args.pending_videos_file
    )
    write_offset(args.offset_file, offset)
    processar_videos_pendentes(args)
    enviar_health(args.api_url, args.api_token, args.pdv_station, args.timeout)
    enviar_vendas(args)


if __name__ == "__main__":
    main()
