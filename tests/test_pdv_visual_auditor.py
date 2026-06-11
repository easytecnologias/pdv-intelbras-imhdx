import argparse
import json
import time

import pytest

import pdv_visual_auditor as auditor


# ---------------------------------------------------------------------------
# Regra de valor (sem chamada a API)
# ---------------------------------------------------------------------------

def test_resultado_regra_valor_libera_item_barato():
    resultado = auditor.resultado_regra_valor("Bala Halls", 1.50, 2)
    assert resultado["resultado"] == "CONFERE_POR_REGRA_DE_VALOR"
    assert resultado["acao_recomendada"] == "liberar"
    assert resultado["economizou_api"] is True
    assert resultado["valor_total"] == 3.0


def test_produto_tem_risco():
    assert auditor.produto_tem_risco("Cerveja Antarctica Lata 350ml")
    assert auditor.produto_tem_risco("Carne Bovina Picanha Kg")
    assert not auditor.produto_tem_risco("Bala Halls")


def test_executar_auditoria_cli_aplica_regra_de_valor_sem_api(monkeypatch):
    monkeypatch.setattr(auditor, "GROQ_API_KEY", "")
    resultado = auditor.executar_auditoria_cli(
        imagem="/nao/existe.jpg",
        produto="Bala Halls",
        valor=1.50,
        quantidade=2,
    )
    assert resultado["resultado"] == "CONFERE_POR_REGRA_DE_VALOR"


def test_executar_auditoria_cli_produto_de_risco_nao_usa_regra_de_valor(monkeypatch):
    monkeypatch.setattr(auditor, "GROQ_API_KEY", "")
    resultado = auditor.executar_auditoria_cli(
        imagem="/nao/existe.jpg",
        produto="Cerveja Antarctica Lata 350ml",
        valor=3.50,
        quantidade=1,
    )
    assert resultado["resultado"] == "NAO_ANALISADO"
    assert "Imagem nao encontrada" in resultado["erro_tecnico"]


# ---------------------------------------------------------------------------
# Normalizacao de texto / categorias / tokens
# ---------------------------------------------------------------------------

def test_normalizar_texto_remove_acentos_e_caixa():
    assert auditor.normalizar_texto("Açúcar Cristal") == "acucar cristal"


def test_categorias_do_texto_identifica_categoria():
    assert auditor.categorias_do_texto("Pacote de biscoito Trakinas") == {"biscoito"}
    assert auditor.categorias_do_texto("Arroz Tipo 1 5kg") == {"arroz"}
    assert auditor.categorias_do_texto("Item generico qualquer") == set()


def test_tokens_relevantes_ignora_palavras_curtas_e_unidades():
    tokens = auditor.tokens_relevantes("Arroz Tipo 1 5Kg Trad")
    assert "arroz" in tokens
    assert "kg" not in tokens
    assert "trad" not in tokens
    assert "1" not in tokens


# ---------------------------------------------------------------------------
# comparar_identificacao_visual (modo "produto")
# ---------------------------------------------------------------------------

def test_comparar_identificacao_visual_categoria_compativel_libera():
    raw = {
        "identificacao_conclusiva": True,
        "confianca_visual": 90,
        "categoria_visual": "arroz",
        "marca_visual": "Tio Joao",
        "produto_visual": "Pacote de arroz Tio Joao",
        "evidencia_temporal": "ANTES e BIP",
    }
    resultado = auditor.comparar_identificacao_visual(raw, "Arroz Tipo 1 5Kg", 1)
    assert resultado["resultado"] == "CONFERE"
    assert resultado["acao_recomendada"] == "liberar"


def test_comparar_identificacao_visual_categoria_divergente_gera_nao_confere():
    raw = {
        "identificacao_conclusiva": True,
        "confianca_visual": 90,
        "categoria_visual": "biscoito",
        "marca_visual": "Trakinas",
        "produto_visual": "Pacote de biscoito Trakinas",
        "evidencia_temporal": "ANTES e BIP",
    }
    resultado = auditor.comparar_identificacao_visual(
        raw, "Leite Em Po Ninho Int Inst Lv750pg700", 1
    )
    assert resultado["resultado"] == "NAO_CONFERE"
    assert resultado["acao_recomendada"] == "revisar cupom"


def test_comparar_identificacao_visual_nao_conclusiva_e_inconclusivo():
    raw = {
        "identificacao_conclusiva": False,
        "confianca_visual": 30,
        "categoria_visual": "desconhecido",
        "marca_visual": "",
        "produto_visual": "",
        "evidencia_temporal": "produtos diferentes em cada painel",
    }
    resultado = auditor.comparar_identificacao_visual(raw, "Arroz Tipo 1 5Kg", 1)
    assert resultado["resultado"] == "INCONCLUSIVO"
    assert resultado["acao_recomendada"] == "revisar gravacao"


def test_comparar_identificacao_visual_marca_fraca_fica_inconclusivo():
    raw = {
        "identificacao_conclusiva": True,
        "confianca_visual": 60,
        "categoria_visual": "desconhecido",
        "marca_visual": "ilegivel",
        "produto_visual": "Embalagem nao identificada",
        "evidencia_temporal": "ANTES e BIP",
    }
    resultado = auditor.comparar_identificacao_visual(raw, "Arroz Tipo 1 5Kg", 1)
    assert resultado["resultado"] == "INCONCLUSIVO"


# ---------------------------------------------------------------------------
# comparar_presenca_visual (modo "presenca")
# ---------------------------------------------------------------------------

def test_comparar_presenca_visual_produto_presente_libera():
    raw = {
        "produto_presente": True,
        "confianca_visual": 75,
        "evidencia_visual": "produto cruza o scanner",
        "objeto_principal": "pacote",
    }
    resultado = auditor.comparar_presenca_visual(raw)
    assert resultado["resultado"] == "CONFERE"
    assert resultado["acao_recomendada"] == "liberar"


def test_comparar_presenca_visual_sem_produto_alerta():
    raw = {
        "produto_presente": False,
        "confianca_visual": 90,
        "evidencia_visual": "scanner vazio durante toda a sequencia",
        "objeto_principal": "",
    }
    resultado = auditor.comparar_presenca_visual(raw)
    assert resultado["resultado"] == "NAO_CONFERE"
    assert resultado["tipo_alerta"] == "REGISTRO_SEM_PASSAGEM_VISUAL"


def test_comparar_presenca_visual_baixa_confianca_e_inconclusivo():
    raw = {
        "produto_presente": False,
        "confianca_visual": 50,
        "evidencia_visual": "imagem parcialmente encoberta",
        "objeto_principal": "",
    }
    resultado = auditor.comparar_presenca_visual(raw)
    assert resultado["resultado"] == "INCONCLUSIVO"


# ---------------------------------------------------------------------------
# normalizar_inconclusivo
# ---------------------------------------------------------------------------

def test_normalizar_inconclusivo_evidencia_compativel_vira_confere():
    resultado = {
        "resultado": "INCONCLUSIVO",
        "confianca": 50,
        "o_que_aparece_na_imagem": "Pacote de arroz na bancada",
        "comparacao_pdv": "",
        "possivel_divergencia": "",
        "acao_recomendada": "revisar gravacao",
    }
    ajustado = auditor.normalizar_inconclusivo(resultado, "Arroz Tipo 1 5Kg", 1)
    assert ajustado["resultado"] == "CONFERE"
    assert ajustado["acao_recomendada"] == "liberar"


def test_normalizar_inconclusivo_sem_evidencia_mantem_baixa_confianca():
    resultado = {
        "resultado": "INCONCLUSIVO",
        "confianca": 50,
        "o_que_aparece_na_imagem": "scanner vazio",
        "comparacao_pdv": "",
        "possivel_divergencia": "",
        "acao_recomendada": "revisar gravacao",
    }
    ajustado = auditor.normalizar_inconclusivo(resultado, "Arroz Tipo 1 5Kg", 1)
    assert ajustado["resultado"] == "INCONCLUSIVO"
    assert ajustado["confianca"] <= 35


# ---------------------------------------------------------------------------
# aplicar_trava_conservadora
# ---------------------------------------------------------------------------

def test_aplicar_trava_quantidade_maior_que_um_com_token_compativel_libera():
    resultado = {
        "resultado": "INCONCLUSIVO",
        "confianca": 40,
        "o_que_aparece_na_imagem": "Pacote de feijao carioca",
        "comparacao_pdv": "",
        "possivel_divergencia": "",
        "acao_recomendada": "revisar gravacao",
    }
    ajustado = auditor.aplicar_trava_conservadora(
        resultado, "Feijao Carioca Mui Nobre 1kg", 10
    )
    assert ajustado["resultado"] == "CONFERE"
    assert ajustado["acao_recomendada"] == "liberar"


def test_aplicar_trava_nao_confere_com_token_compativel_vira_inconclusivo():
    resultado = {
        "resultado": "NAO_CONFERE",
        "confianca": 80,
        "o_que_aparece_na_imagem": "Pacote de arroz tipo 1",
        "comparacao_pdv": "",
        "possivel_divergencia": "",
        "acao_recomendada": "revisar cupom",
    }
    ajustado = auditor.aplicar_trava_conservadora(resultado, "Arroz Tipo 1 5Kg", 1)
    assert ajustado["resultado"] == "INCONCLUSIVO"
    assert ajustado["confianca"] <= 60


# ---------------------------------------------------------------------------
# Mensagens de erro / cota
# ---------------------------------------------------------------------------

def test_erro_como_json_formato():
    resultado = auditor.erro_como_json("falha qualquer")
    assert resultado["resultado"] == "NAO_ANALISADO"
    assert resultado["confianca"] is None
    assert resultado["erro_tecnico"] == "falha qualquer"


def test_erro_cota_como_json_formato():
    resultado = auditor.erro_cota_como_json(45)
    assert resultado["resultado"] == "NAO_ANALISADO"
    assert resultado["erro_api"] == "COTA_GROQ"
    assert resultado["tentar_novamente_em_segundos"] == 45


def test_erro_limite_local_formato():
    resultado = auditor.erro_limite_local(30, "LIMITE_LOCAL_MINUTO")
    assert resultado["erro_api"] == "LIMITE_LOCAL_MINUTO"
    assert "protecao local" in resultado["erro_tecnico"]


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("Error 429: rate limit exceeded", True),
        ("ResourceExhausted: quota exceeded", True),
        ("Connection refused", False),
    ],
)
def test_eh_erro_limite(texto, esperado):
    assert auditor.eh_erro_limite(Exception(texto)) is esperado


def test_extrair_retry_after_le_tempo_da_mensagem():
    assert auditor._extrair_retry_after("please retry in 12.5s") == 14


def test_extrair_retry_after_usa_default_sem_match():
    assert auditor._extrair_retry_after("erro generico") == 30


# ---------------------------------------------------------------------------
# reservar_chamada_api (rate limit local)
# ---------------------------------------------------------------------------

def test_reservar_chamada_api_respeita_limite_por_minuto(tmp_path, monkeypatch):
    requests_path = tmp_path / "request_times.json"
    monkeypatch.setattr(auditor, "REQUESTS_PATH", requests_path)
    monkeypatch.setattr(auditor, "MAX_CALLS_PER_MINUTE", 2)
    monkeypatch.setattr(auditor, "MAX_CALLS_PER_HOUR", 150)

    assert auditor.reservar_chamada_api()[0] is True
    assert auditor.reservar_chamada_api()[0] is True
    permitido, espera, motivo = auditor.reservar_chamada_api()
    assert permitido is False
    assert motivo == "LIMITE_LOCAL_MINUTO"
    assert espera > 0


def test_reservar_chamada_api_respeita_limite_por_hora(tmp_path, monkeypatch):
    requests_path = tmp_path / "request_times.json"
    now = time.time()
    requests_path.write_text(
        json.dumps({"timestamps": [now - i * 20 for i in range(150)]})
    )
    monkeypatch.setattr(auditor, "REQUESTS_PATH", requests_path)
    monkeypatch.setattr(auditor, "MAX_CALLS_PER_MINUTE", 5)
    monkeypatch.setattr(auditor, "MAX_CALLS_PER_HOUR", 150)

    permitido, espera, motivo = auditor.reservar_chamada_api()
    assert permitido is False
    assert motivo == "LIMITE_LOCAL_HORA"


# ---------------------------------------------------------------------------
# gerar_recorte_scanner
# ---------------------------------------------------------------------------

def test_gerar_recorte_scanner_pula_imagens_de_sequencia():
    assert auditor.gerar_recorte_scanner("/tmp/foo_sequence.jpg") is None


# ---------------------------------------------------------------------------
# parse_args / registrar_resultado (campo "video")
# ---------------------------------------------------------------------------

def test_parse_args_aceita_video(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "pdv_visual_auditor.py",
            "--imagem", "/tmp/foto.jpg",
            "--produto", "Bala Halls",
            "--valor", "1.50",
            "--quantidade", "2",
            "--video", "/tmp/evento.mp4",
        ],
    )
    args = auditor.parse_args()
    assert args.video == "/tmp/evento.mp4"


def test_parse_args_video_padrao_vazio(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "pdv_visual_auditor.py",
            "--imagem", "/tmp/foto.jpg",
            "--produto", "Bala Halls",
            "--valor", "1.50",
            "--quantidade", "2",
        ],
    )
    args = auditor.parse_args()
    assert args.video == ""


def test_registrar_resultado_inclui_video(tmp_path, monkeypatch):
    results_path = tmp_path / "results.jsonl"
    monkeypatch.setattr(auditor, "RESULTS_PATH", results_path)

    args = argparse.Namespace(
        imagem="/tmp/foto.jpg",
        video="/tmp/evento.mp4",
        cupom="221548",
        produto="Bala Halls",
        valor=1.50,
        quantidade=2,
        modo="produto",
    )
    auditor.registrar_resultado(args, {"resultado": "CONFERE"})

    linha = json.loads(results_path.read_text(encoding="utf-8").strip())
    assert linha["video"] == "/tmp/evento.mp4"


def test_registrar_resultado_video_vazio_vira_none(tmp_path, monkeypatch):
    results_path = tmp_path / "results.jsonl"
    monkeypatch.setattr(auditor, "RESULTS_PATH", results_path)

    args = argparse.Namespace(
        imagem="/tmp/foto.jpg",
        video="",
        cupom="221548",
        produto="Bala Halls",
        valor=1.50,
        quantidade=2,
        modo="produto",
    )
    auditor.registrar_resultado(args, {"resultado": "CONFERE"})

    linha = json.loads(results_path.read_text(encoding="utf-8").strip())
    assert linha["video"] is None
