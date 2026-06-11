import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import pdv_dashboard_sync as sync


def test_deve_ignorar_confere_por_regra_de_valor():
    registro = {"resultado": {"resultado": "CONFERE_POR_REGRA_DE_VALOR", "economizou_api": True}}
    assert sync.deve_ignorar(registro) is True


def test_deve_ignorar_economizou_api():
    registro = {"resultado": {"resultado": "CONFERE", "economizou_api": True}}
    assert sync.deve_ignorar(registro) is True


def test_nao_deve_ignorar_resultado_normal():
    registro = {"resultado": {"resultado": "NAO_CONFERE", "confianca": 90}}
    assert sync.deve_ignorar(registro) is False


def test_montar_evento():
    registro = {
        "timestamp": "2026-06-10T14:32:08",
        "imagem": "/tmp/foo.jpg",
        "cupom": "221548",
        "produto": "Cafe Marata 250g",
        "valor_unitario": 14.99,
        "quantidade": 1,
        "modo": "produto",
        "resultado": {"resultado": "NAO_CONFERE", "confianca": 90},
    }
    evento = sync.montar_evento(registro, "001")
    assert evento["pdv"] == "001"
    assert evento["cupom"] == "221548"
    assert evento["produto"] == "Cafe Marata 250g"
    assert evento["resultado"] == {"resultado": "NAO_CONFERE", "confianca": 90}


def test_ler_novos_eventos_offset(tmp_path):
    results_file = tmp_path / "results.jsonl"
    offset_file = tmp_path / "offset"

    linha1 = json.dumps({"timestamp": "1", "resultado": {"resultado": "NAO_CONFERE"}})
    linha2 = json.dumps(
        {"timestamp": "2", "resultado": {"resultado": "CONFERE_POR_REGRA_DE_VALOR", "economizou_api": True}}
    )
    linha3 = json.dumps({"timestamp": "3", "resultado": {"resultado": "CONFERE", "confianca": 96}})

    results_file.write_text(linha1 + "\n")
    eventos, offset = sync.ler_novos_eventos(str(results_file), str(offset_file))
    assert [e["timestamp"] for e in eventos] == ["1"]
    sync.write_offset(str(offset_file), offset)

    with results_file.open("a", encoding="utf-8") as handle:
        handle.write(linha2 + "\n")
        handle.write(linha3 + "\n")

    eventos, offset = sync.ler_novos_eventos(str(results_file), str(offset_file))
    assert [e["timestamp"] for e in eventos] == ["3"]
    sync.write_offset(str(offset_file), offset)

    eventos, offset = sync.ler_novos_eventos(str(results_file), str(offset_file))
    assert eventos == []


def test_garantir_offset_inicial_pula_historico(tmp_path):
    results_file = tmp_path / "results.jsonl"
    offset_file = tmp_path / "offset"

    linhas = [
        json.dumps({"timestamp": str(i), "resultado": {"resultado": "NAO_CONFERE"}})
        for i in range(5)
    ]
    results_file.write_text("\n".join(linhas) + "\n")

    sync.garantir_offset_inicial(str(results_file), str(offset_file))
    eventos, offset = sync.ler_novos_eventos(str(results_file), str(offset_file))
    assert eventos == []

    with results_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": "novo", "resultado": {"resultado": "NAO_CONFERE"}}) + "\n")

    eventos, _ = sync.ler_novos_eventos(str(results_file), str(offset_file))
    assert [e["timestamp"] for e in eventos] == ["novo"]


def test_garantir_offset_inicial_nao_sobrescreve_offset_existente(tmp_path):
    results_file = tmp_path / "results.jsonl"
    offset_file = tmp_path / "offset"
    results_file.write_text(json.dumps({"timestamp": "1", "resultado": {"resultado": "NAO_CONFERE"}}) + "\n")
    sync.write_offset(str(offset_file), 0)

    sync.garantir_offset_inicial(str(results_file), str(offset_file))
    eventos, _ = sync.ler_novos_eventos(str(results_file), str(offset_file))
    assert [e["timestamp"] for e in eventos] == ["1"]


def test_ler_novos_eventos_arquivo_inexistente(tmp_path):
    eventos, offset = sync.ler_novos_eventos(str(tmp_path / "nao-existe.jsonl"), str(tmp_path / "offset"))
    assert eventos == []
    assert offset == 0


def test_ler_novos_eventos_ignora_linha_invalida(tmp_path):
    results_file = tmp_path / "results.jsonl"
    offset_file = tmp_path / "offset"
    results_file.write_text("isso nao e json\n" + json.dumps({"timestamp": "1", "resultado": {"resultado": "NAO_CONFERE"}}) + "\n")

    eventos, _ = sync.ler_novos_eventos(str(results_file), str(offset_file))
    assert [e["timestamp"] for e in eventos] == ["1"]


def test_enviar_eventos_chama_api():
    eventos = [{"timestamp": "1", "resultado": {"resultado": "NAO_CONFERE"}}]
    with patch.object(sync.requests, "post") as mock_post:
        sync.enviar_eventos("https://api.example", "token123", eventos, "001", 10)

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.example/api/v1/events"
    assert kwargs["headers"] == {"Authorization": "Bearer token123"}
    assert kwargs["json"]["pdv"] == "001"


def test_enviar_eventos_sem_config_nao_chama_api():
    eventos = [{"timestamp": "1", "resultado": {"resultado": "NAO_CONFERE"}}]
    with patch.object(sync.requests, "post") as mock_post:
        sync.enviar_eventos("", "", eventos, "001", 10)
    mock_post.assert_not_called()


def test_enviar_eventos_erro_de_rede_nao_propaga():
    eventos = [{"timestamp": "1", "resultado": {"resultado": "NAO_CONFERE"}}]
    with patch.object(sync.requests, "post", side_effect=Exception("boom")):
        sync.enviar_eventos("https://api.example", "token123", eventos, "001", 10)


def test_enviar_eventos_envia_imagem_quando_disponivel(tmp_path):
    imagem = tmp_path / "foto.jpg"
    imagem.write_bytes(b"fake-jpg")
    eventos = [{"timestamp": "1", "imagem": str(imagem), "resultado": {"resultado": "NAO_CONFERE"}}]

    mock_resposta = type("Resp", (), {"json": lambda self: {"id": 42}})()
    with patch.object(sync.requests, "post", return_value=mock_resposta) as mock_post:
        sync.enviar_eventos("https://api.example", "token123", eventos, "001", 10)

    assert mock_post.call_count == 2
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.example/api/v1/events/42/image"
    assert kwargs["headers"] == {"Authorization": "Bearer token123"}
    assert "file" in kwargs["files"]


def test_enviar_imagem_evento_arquivo_inexistente_nao_chama_api(tmp_path):
    with patch.object(sync.requests, "post") as mock_post:
        sync.enviar_imagem_evento("https://api.example", "token123", 42, str(tmp_path / "nao-existe.jpg"), 10)
    mock_post.assert_not_called()


def test_enviar_imagem_evento_erro_de_rede_nao_propaga(tmp_path):
    imagem = tmp_path / "foto.jpg"
    imagem.write_bytes(b"fake-jpg")
    with patch.object(sync.requests, "post", side_effect=Exception("boom")):
        sync.enviar_imagem_evento("https://api.example", "token123", 42, str(imagem), 10)


def test_enviar_eventos_envia_video_quando_disponivel(tmp_path):
    video = tmp_path / "evento.mp4"
    video.write_bytes(b"fake-mp4")
    eventos = [{"timestamp": "1", "video": str(video), "resultado": {"resultado": "NAO_CONFERE"}}]

    mock_resposta = type("Resp", (), {"json": lambda self: {"id": 42}})()
    with patch.object(sync.requests, "post", return_value=mock_resposta) as mock_post:
        sync.enviar_eventos("https://api.example", "token123", eventos, "001", 10)

    assert mock_post.call_count == 2
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.example/api/v1/events/42/video"
    assert kwargs["headers"] == {"Authorization": "Bearer token123"}
    assert "file" in kwargs["files"]


def test_enviar_video_evento_arquivo_inexistente_nao_chama_api(tmp_path):
    with patch.object(sync.requests, "post") as mock_post:
        sync.enviar_video_evento("https://api.example", "token123", 42, str(tmp_path / "nao-existe.mp4"), 10)
    mock_post.assert_not_called()


def test_enviar_video_evento_erro_de_rede_nao_propaga(tmp_path):
    video = tmp_path / "evento.mp4"
    video.write_bytes(b"fake-mp4")
    with patch.object(sync.requests, "post", side_effect=Exception("boom")):
        sync.enviar_video_evento("https://api.example", "token123", 42, str(video), 10)


# ---------------------------------------------------------------------------
# retry tardio de video (videos pendentes)
# ---------------------------------------------------------------------------

def test_enviar_eventos_sem_video_adiciona_pendente(tmp_path):
    pendentes_file = tmp_path / "pendentes.jsonl"
    eventos = [{"timestamp": "2026-06-11T10:00:00", "resultado": {"resultado": "NAO_CONFERE"}}]

    mock_resposta = type("Resp", (), {"json": lambda self: {"id": 42}})()
    with patch.object(sync.requests, "post", return_value=mock_resposta):
        sync.enviar_eventos("https://api.example", "token123", eventos, "001", 10, str(pendentes_file))

    itens = sync.ler_videos_pendentes(str(pendentes_file))
    assert itens == [{"evento_id": 42, "timestamp": "2026-06-11T10:00:00", "tentativas": 0}]


def test_enviar_eventos_com_video_nao_adiciona_pendente(tmp_path):
    pendentes_file = tmp_path / "pendentes.jsonl"
    video = tmp_path / "evento.mp4"
    video.write_bytes(b"fake-mp4")
    eventos = [{"timestamp": "2026-06-11T10:00:00", "video": str(video), "resultado": {"resultado": "NAO_CONFERE"}}]

    mock_resposta = type("Resp", (), {"json": lambda self: {"id": 42}})()
    with patch.object(sync.requests, "post", return_value=mock_resposta):
        sync.enviar_eventos("https://api.example", "token123", eventos, "001", 10, str(pendentes_file))

    assert sync.ler_videos_pendentes(str(pendentes_file)) == []


def test_processar_videos_pendentes_aguarda_idade_minima(tmp_path):
    pendentes_file = tmp_path / "pendentes.jsonl"
    timestamp_recente = datetime.now().isoformat(timespec="seconds")
    sync.gravar_videos_pendentes(str(pendentes_file), [{"evento_id": 42, "timestamp": timestamp_recente, "tentativas": 0}])

    args = argparse_namespace(tmp_path, pendentes_file)
    with patch.object(sync.bot, "baixar_clipe_imhdx") as mock_baixar:
        sync.processar_videos_pendentes(args)

    mock_baixar.assert_not_called()
    assert sync.ler_videos_pendentes(str(pendentes_file)) == [
        {"evento_id": 42, "timestamp": timestamp_recente, "tentativas": 0}
    ]


def test_processar_videos_pendentes_sucesso(tmp_path):
    pendentes_file = tmp_path / "pendentes.jsonl"
    timestamp_antigo = (datetime.now() - timedelta(seconds=200)).isoformat(timespec="seconds")
    sync.gravar_videos_pendentes(str(pendentes_file), [{"evento_id": 42, "timestamp": timestamp_antigo, "tentativas": 0}])

    args = argparse_namespace(tmp_path, pendentes_file)
    mock_resposta = type("Resp", (), {"json": lambda self: {"id": 42}})()

    def fake_baixar(args, event_dt, channel, output_path):
        Path(output_path).write_bytes(b"fake-mp4")

    with patch.object(sync.bot, "baixar_clipe_imhdx", side_effect=fake_baixar) as mock_baixar, \
            patch.object(sync.requests, "post", return_value=mock_resposta) as mock_post:
        sync.processar_videos_pendentes(args)

    mock_baixar.assert_called_once()
    mock_post.assert_called_once()
    assert sync.ler_videos_pendentes(str(pendentes_file)) == []


def test_processar_videos_pendentes_falha_incrementa_tentativas(tmp_path):
    pendentes_file = tmp_path / "pendentes.jsonl"
    timestamp_antigo = (datetime.now() - timedelta(seconds=200)).isoformat(timespec="seconds")
    sync.gravar_videos_pendentes(str(pendentes_file), [{"evento_id": 42, "timestamp": timestamp_antigo, "tentativas": 0}])

    args = argparse_namespace(tmp_path, pendentes_file)
    with patch.object(sync.bot, "baixar_clipe_imhdx", side_effect=RuntimeError("indisponivel")):
        sync.processar_videos_pendentes(args)

    itens = sync.ler_videos_pendentes(str(pendentes_file))
    assert itens == [{"evento_id": 42, "timestamp": timestamp_antigo, "tentativas": 1}]


def test_processar_videos_pendentes_desiste_apos_max_tentativas(tmp_path):
    pendentes_file = tmp_path / "pendentes.jsonl"
    timestamp_antigo = (datetime.now() - timedelta(seconds=200)).isoformat(timespec="seconds")
    sync.gravar_videos_pendentes(
        str(pendentes_file),
        [{"evento_id": 42, "timestamp": timestamp_antigo, "tentativas": sync.VIDEO_RETRY_MAX_TENTATIVAS - 1}],
    )

    args = argparse_namespace(tmp_path, pendentes_file)
    with patch.object(sync.bot, "baixar_clipe_imhdx", side_effect=RuntimeError("indisponivel")):
        sync.processar_videos_pendentes(args)

    assert sync.ler_videos_pendentes(str(pendentes_file)) == []


def argparse_namespace(tmp_path, pendentes_file):
    return type("Args", (), {
        "api_url": "https://api.example",
        "api_token": "token123",
        "timeout": 10,
        "pending_videos_file": str(pendentes_file),
        "video_retry_dir": str(tmp_path / "videos_retry"),
        "imhdx_channel": 1,
    })()


@pytest.mark.parametrize(
    "is_active_output,esperado",
    [
        ("active\n", "online"),
        ("inactive\n", "offline"),
        ("failed\n", "offline"),
        ("activating\n", "warning"),
    ],
)
def test_estado_servico(is_active_output, esperado):
    with patch.object(sync.subprocess, "run") as mock_run:
        mock_run.return_value.stdout = is_active_output
        assert sync.estado_servico("algum-servico") == esperado


def test_estado_servico_excecao_retorna_warning():
    with patch.object(sync.subprocess, "run", side_effect=Exception("boom")):
        assert sync.estado_servico("algum-servico") == "warning"


def test_montar_health():
    status = {
        "pdv-intelbras-bridge": "online",
        "pdv-telegram-assistant": "warning",
        "pdv-visual-alert-worker": "offline",
    }
    health = sync.montar_health("001", status)
    assert health == [{"pdv": "001", "bridge": "online", "imhdx": "warning", "audit": "offline"}]


def test_enviar_health_chama_api():
    with patch.object(sync, "coletar_health", return_value={
        "pdv-intelbras-bridge": "online",
        "pdv-telegram-assistant": "online",
        "pdv-visual-alert-worker": "online",
    }):
        with patch.object(sync.requests, "post") as mock_post:
            sync.enviar_health("https://api.example", "token123", "001", 10)

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.example/api/v1/health"
    assert kwargs["json"] == [{"pdv": "001", "bridge": "online", "imhdx": "online", "audit": "online"}]
