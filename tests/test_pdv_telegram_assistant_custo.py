import json
from datetime import datetime

import pdv_telegram_assistant as bot


def _escrever_resultados(path, registros):
    with path.open("w", encoding="utf-8") as handle:
        for registro in registros:
            handle.write(json.dumps(registro) + "\n")


def test_custo_periodo_soma_apenas_chamadas_reais(tmp_path, monkeypatch):
    results_path = tmp_path / "results.jsonl"
    hoje = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    _escrever_resultados(results_path, [
        {
            "timestamp": hoje.isoformat(timespec="seconds"),
            "resultado": {"resultado": "CONFERE", "tokens_entrada": 1000, "tokens_saida": 200},
        },
        {
            "timestamp": hoje.isoformat(timespec="seconds"),
            "resultado": {"resultado": "CONFERE_POR_REGRA_DE_VALOR", "economizou_api": True},
        },
        {
            "timestamp": hoje.isoformat(timespec="seconds"),
            "resultado": {"resultado": "NAO_ANALISADO", "erro_api": "COTA_GROQ"},
        },
    ])
    monkeypatch.setattr(bot, "VISUAL_RESULTS_PATH", results_path)

    registros = bot._ler_resultados_visuais()
    chamadas, tok_in, tok_out, custo = bot._custo_periodo(registros, hoje.replace(hour=0))

    assert chamadas == 1
    assert tok_in == 1000
    assert tok_out == 200
    assert custo == 1000 / 1_000_000 * bot.GROQ_PRECO_INPUT_USD_POR_MILHAO + 200 / 1_000_000 * bot.GROQ_PRECO_OUTPUT_USD_POR_MILHAO


def test_custo_summary_sem_arquivo(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "VISUAL_RESULTS_PATH", tmp_path / "nao_existe.jsonl")

    texto = bot.custo_summary(argparse_namespace())

    assert "Custo da auditoria visual" in texto
    assert "Chamadas reais a API: 0" in texto


def argparse_namespace():
    import argparse
    return argparse.Namespace()
