#!/usr/bin/env python3
"""Sincroniza eventos da auditoria visual e a saude dos servicos do PDV
com a API do dashboard Easy Auditoria."""
import argparse
import json
import os
import subprocess
from pathlib import Path

import requests

SERVICOS_HEALTH = (
    "pdv-intelbras-bridge",
    "pdv-telegram-assistant",
    "pdv-visual-alert-worker",
)


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


def enviar_eventos(api_url, api_token, eventos, pdv_station, timeout):
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
    enviar_eventos(args.api_url, args.api_token, eventos, args.pdv_station, args.timeout)
    write_offset(args.offset_file, offset)
    enviar_health(args.api_url, args.api_token, args.pdv_station, args.timeout)


if __name__ == "__main__":
    main()
