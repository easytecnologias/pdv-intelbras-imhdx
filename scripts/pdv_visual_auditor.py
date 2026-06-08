#!/usr/bin/env python3
"""
Auditoria visual de scanner para PDV Intelbras iMHDX.

Este modulo compara um item registrado pelo PDV/Espiao com uma imagem .jpg
capturada no horario do bip, usando a API oficial do Gemini.

Uso esperado:
    from pdv_visual_auditor import executar_auditoria_scanner

    resultado = executar_auditoria_scanner("/tmp/item.jpg", {
        "pdv": "001",
        "cupom": "221038",
        "horario": "14:32:10",
        "produto": "Coca-Cola 2L",
        "quantidade": 2,
        "valor_unitario": 9.99,
    })
"""

import json
import os
import time
import unicodedata
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


ENV_PATH = "/etc/pdv-intelbras-imhdx/.env"
GEMINI_MODEL = "gemini-1.5-flash"

PALAVRAS_RISCO = (
    "CARNE",
    "CERVEJA",
    "WHISKY",
    "AZEITE",
    "SABAO",
    "REFRIGERANTE",
)

PROMPT_SISTEMA = (
    "Voce e um Auditor Visual de Prevencao de Perdas especializado em caixas "
    "de supermercado. Sua funcao e analisar a imagem da area do scanner e "
    "comparar o que aparece fisicamente com os dados de texto registrados no "
    "PDV fornecidos pelo usuario. Nao use termos agressivos como fraude, furto "
    "ou roubo. Seja estritamente analitico e objetivo."
)

RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=[
        "resultado",
        "confianca",
        "o_que_aparece_na_imagem",
        "comparacao_pdv",
        "possivel_divergencia",
        "acao_recomendada",
    ],
    properties={
        "resultado": types.Schema(
            type=types.Type.STRING,
            enum=["CONFERE", "NAO_CONFERE", "INCONCLUSIVO"],
        ),
        "confianca": types.Schema(
            type=types.Type.INTEGER,
            minimum=0,
            maximum=100,
        ),
        "o_que_aparece_na_imagem": types.Schema(type=types.Type.STRING),
        "comparacao_pdv": types.Schema(type=types.Type.STRING),
        "possivel_divergencia": types.Schema(type=types.Type.STRING),
        "acao_recomendada": types.Schema(
            type=types.Type.STRING,
            enum=["liberar", "revisar cupom", "revisar gravacao"],
        ),
    },
)


def _normalizar_texto(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return texto.upper()


def _tem_palavra_risco(produto):
    produto_normalizado = _normalizar_texto(produto)
    return any(palavra in produto_normalizado for palavra in PALAVRAS_RISCO)


def _retorno_regra_valor(dados_item):
    return {
        "resultado": "CONFERE_POR_REGRA_DE_VALOR",
        "confianca": 100,
        "o_que_aparece_na_imagem": "",
        "comparacao_pdv": (
            "Item nao enviado para auditoria visual porque esta abaixo do "
            "valor minimo e nao contem palavra-chave de risco."
        ),
        "possivel_divergencia": "",
        "acao_recomendada": "liberar",
        "pdv": str(dados_item.get("pdv", "")),
        "cupom": str(dados_item.get("cupom", "")),
        "horario": str(dados_item.get("horario", "")),
        "produto": str(dados_item.get("produto", "")),
        "economizou_api": True,
    }


def _deve_pular_por_regra_de_custo(dados_item):
    produto = dados_item.get("produto", "")
    valor_unitario = float(dados_item.get("valor_unitario") or 0)
    return valor_unitario < 8.00 and not _tem_palavra_risco(produto)


def _mime_type(caminho_imagem):
    ext = Path(caminho_imagem).suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    return "image/jpeg"


def _montar_prompt_usuario(dados_item):
    return (
        "Analise somente a area do scanner/balanca e os produtos visiveis "
        "proximos ao scanner.\n\n"
        "Dados registrados no PDV:\n"
        "- PDV: {pdv}\n"
        "- Cupom: {cupom}\n"
        "- Horario do registro: {horario}\n"
        "- Produto registrado: {produto}\n"
        "- Quantidade registrada: {quantidade}\n"
        "- Valor unitario: R$ {valor_unitario:.2f}\n\n"
        "Responda se o que aparece fisicamente na imagem confere com o item "
        "registrado no PDV. Se a imagem estiver ruim, cortada, encoberta ou "
        "fora do momento correto, responda INCONCLUSIVO."
    ).format(
        pdv=str(dados_item.get("pdv", "")),
        cupom=str(dados_item.get("cupom", "")),
        horario=str(dados_item.get("horario", "")),
        produto=str(dados_item.get("produto", "")),
        quantidade=int(dados_item.get("quantidade") or 0),
        valor_unitario=float(dados_item.get("valor_unitario") or 0),
    )


def _erro_limite_taxa(exc):
    codigo = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if codigo == 429:
        return True

    texto = "{} {}".format(type(exc).__name__, str(exc))
    texto = texto.upper()
    return (
        "429" in texto
        or "RESOURCE_EXHAUSTED" in texto
        or "RESOURCEEXHAUSTED" in texto
        or "RATE LIMIT" in texto
        or "QUOTA" in texto
    )


def _normalizar_resposta(texto_resposta):
    if not texto_resposta:
        raise ValueError("Gemini retornou resposta vazia")

    texto = texto_resposta.strip()
    if texto.startswith("```"):
        texto = texto.replace("```json", "").replace("```", "").strip()

    data = json.loads(texto)
    return {
        "resultado": str(data.get("resultado", "INCONCLUSIVO")),
        "confianca": int(data.get("confianca", 0)),
        "o_que_aparece_na_imagem": str(data.get("o_que_aparece_na_imagem", "")),
        "comparacao_pdv": str(data.get("comparacao_pdv", "")),
        "possivel_divergencia": str(data.get("possivel_divergencia", "")),
        "acao_recomendada": str(data.get("acao_recomendada", "revisar gravacao")),
    }


def _chamar_gemini(client, caminho_imagem, dados_item):
    imagem = Path(caminho_imagem)
    if not imagem.exists():
        raise FileNotFoundError("Imagem nao encontrada: {}".format(caminho_imagem))

    prompt_usuario = _montar_prompt_usuario(dados_item)
    parte_imagem = types.Part.from_bytes(
        data=imagem.read_bytes(),
        mime_type=_mime_type(caminho_imagem),
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            parte_imagem,
            prompt_usuario,
        ],
        config=types.GenerateContentConfig(
            system_instruction=PROMPT_SISTEMA,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0.1,
        ),
    )

    return _normalizar_resposta(response.text)


def executar_auditoria_scanner(caminho_imagem, dados_item):
    """
    Executa auditoria visual do item registrado no scanner.

    Retorna sempre um dicionario. Em itens baratos e sem palavra de risco, nao
    chama a API e retorna CONFERE_POR_REGRA_DE_VALOR para preservar a cota.
    """
    load_dotenv(ENV_PATH)

    if _deve_pular_por_regra_de_custo(dados_item):
        return _retorno_regra_valor(dados_item)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY nao encontrada em {}".format(ENV_PATH))

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    try:
        resultado = _chamar_gemini(client, caminho_imagem, dados_item)
    except Exception as exc:
        if not _erro_limite_taxa(exc):
            raise

        time.sleep(5)
        resultado = _chamar_gemini(client, caminho_imagem, dados_item)

    resultado.update({
        "pdv": str(dados_item.get("pdv", "")),
        "cupom": str(dados_item.get("cupom", "")),
        "horario": str(dados_item.get("horario", "")),
        "produto": str(dados_item.get("produto", "")),
        "quantidade": int(dados_item.get("quantidade") or 0),
        "valor_unitario": float(dados_item.get("valor_unitario") or 0),
        "economizou_api": False,
    })
    return resultado
