import argparse
from pathlib import Path
from unittest.mock import patch

import pdv_telegram_assistant as bot
import pdv_visual_alert_worker as worker


def _args(tmp_path):
    return argparse.Namespace(
        state_dir=str(tmp_path),
        imhdx_channel="1",
    )


def test_video_clip_for_item_sucesso(tmp_path):
    args = _args(tmp_path)
    item = {"time": "14:32:08"}

    with patch.object(bot, "baixar_clipe_imhdx") as mock_baixar:
        resultado = worker.video_clip_for_item(args, item, "evento-1", tmp_path)

    assert resultado == str(tmp_path / "evento-1.mp4")
    mock_baixar.assert_called_once()
    chamada_args = mock_baixar.call_args[0]
    assert chamada_args[0] is args
    assert chamada_args[1].strftime("%H:%M:%S") == "14:32:08"
    assert chamada_args[2] == "1"
    assert chamada_args[3] == tmp_path / "evento-1.mp4"


def test_video_clip_for_item_sem_gravacao_retorna_none(tmp_path):
    args = _args(tmp_path)
    item = {"time": "14:32:08"}

    with patch.object(bot, "baixar_clipe_imhdx", side_effect=RuntimeError("gravacao indisponivel")):
        resultado = worker.video_clip_for_item(args, item, "evento-1", tmp_path)

    assert resultado is None


def test_video_clip_for_item_horario_invalido_retorna_none(tmp_path):
    args = _args(tmp_path)
    item = {"time": "horario-invalido"}

    with patch.object(bot, "baixar_clipe_imhdx") as mock_baixar:
        resultado = worker.video_clip_for_item(args, item, "evento-1", tmp_path)

    assert resultado is None
    mock_baixar.assert_not_called()
