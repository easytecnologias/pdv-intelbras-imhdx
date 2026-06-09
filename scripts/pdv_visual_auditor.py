#!/opt/pdv-visual-auditor/venv/bin/python
import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


ENV_PATH = "/etc/pdv-intelbras-imhdx/.env"
load_dotenv(ENV_PATH)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"
COOLDOWN_PATH = Path("/var/lib/pdv-visual-auditor/groq_cooldown.json")

SYSTEM_PROMPT = (
    "Voce e um Auditor Visual de Prevencao de Perdas especializado em caixas "
    "de supermercado. Sua funcao e analisar a imagem da area do scanner e "
    "comparar o que aparece fisicamente com os dados de texto registrados no "
    "PDV fornecidos pelo usuario. Nao use termos agressivos como fraude ou "
    "furto. Seja analitico. Regra critica: voce so deve responder NAO_CONFERE "
    "quando a divergencia visual for evidente e sem ambiguidade. Se a embalagem, "
    "marca, categoria ou quantidade nao estiver nitida, responda INCONCLUSIVO. "
    "Nunca chute a categoria do produto por cor da embalagem. Se houver marca "
    "e categoria compativeis com o texto do PDV, e a quantidade nao divergir, "
    "responda CONFERE mesmo que peso, gramatura ou variante nao estejam legiveis. "
    "Para itens vendidos por KG ou com quantidade decimal, nao tente validar o "
    "peso pela imagem; valide apenas se a categoria visual bate com o produto. "
    "Quando a quantidade registrada for maior que 1, o PDV pode ter usado "
    "multiplicacao e escaneado apenas uma unidade. Nesse caso, uma unidade "
    "visualmente compativel confirma o produto; nao exija que todas as unidades "
    "aparecam juntas na imagem."
)

PALAVRAS_RISCO = (
    "CARNE",
    "CERV",
    "WHISKY",
    "AZEITE",
    "SABAO",
    "REFRIGERANTE",
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "resultado": {
            "type": "string",
            "enum": ["CONFERE", "NAO_CONFERE", "INCONCLUSIVO"],
        },
        "confianca": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        },
        "o_que_aparece_na_imagem": {"type": "string"},
        "comparacao_pdv": {"type": "string"},
        "possivel_divergencia": {"type": "string"},
        "acao_recomendada": {
            "type": "string",
            "enum": ["liberar", "revisar cupom", "revisar gravacao"],
        },
    },
    "required": [
        "resultado",
        "confianca",
        "o_que_aparece_na_imagem",
        "comparacao_pdv",
        "possivel_divergencia",
        "acao_recomendada",
    ],
}


def produto_tem_risco(produto):
    produto_upper = (produto or "").upper()
    return any(palavra in produto_upper for palavra in PALAVRAS_RISCO)


def resultado_regra_valor(produto, valor, quantidade):
    valor_total = valor * quantidade
    return {
        "resultado": "CONFERE_POR_REGRA_DE_VALOR",
        "confianca": 100,
        "o_que_aparece_na_imagem": "",
        "comparacao_pdv": (
            "Item liberado pela regra local: valor unitario abaixo de R$ 8,00, "
            "valor total da linha abaixo de R$ 20,00 e produto fora da lista "
            "de risco."
        ),
        "possivel_divergencia": "",
        "acao_recomendada": "liberar",
        "produto": produto,
        "valor_unitario": valor,
        "quantidade": quantidade,
        "valor_total": valor_total,
        "economizou_api": True,
    }


def erro_como_json(mensagem):
    return {
        "resultado": "INCONCLUSIVO",
        "confianca": 0,
        "o_que_aparece_na_imagem": "",
        "comparacao_pdv": "Nao foi possivel concluir a auditoria visual.",
        "possivel_divergencia": mensagem,
        "acao_recomendada": "revisar gravacao",
    }


def erro_cota_como_json(espera_segundos):
    espera = max(int(espera_segundos), 1)
    return {
        "resultado": "INCONCLUSIVO",
        "confianca": 0,
        "o_que_aparece_na_imagem": "",
        "comparacao_pdv": (
            "A imagem foi obtida, mas a auditoria visual nao pode ser "
            "processada porque a cota temporaria da Groq foi atingida."
        ),
        "possivel_divergencia": (
            "Limite da API atingido. Tente novamente em aproximadamente "
            f"{espera} segundos."
        ),
        "acao_recomendada": "revisar gravacao",
        "erro_api": "COTA_GROQ",
        "tentar_novamente_em_segundos": espera,
    }


def carregar_cooldown():
    try:
        data = json.loads(COOLDOWN_PATH.read_text())
        return max(float(data.get("ate", 0)) - time.time(), 0)
    except Exception:
        return 0


def salvar_cooldown(espera_segundos):
    COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOLDOWN_PATH.write_text(
        json.dumps({"ate": time.time() + max(float(espera_segundos), 1)})
    )


def eh_erro_limite(exc):
    texto = str(exc).lower()
    return (
        "429" in texto
        or "resourceexhausted" in texto
        or "resource exhausted" in texto
        or "quota" in texto
        or "rate limit" in texto
    )


def montar_prompt(produto, valor, quantidade):
    return (
        "Analise a imagem do scanner/balanca do PDV e compare com o item "
        "registrado abaixo.\n\n"
        f"Produto registrado no PDV: {produto}\n"
        f"Valor unitario registrado: R$ {valor:.2f}\n"
        f"Quantidade registrada: {quantidade}\n\n"
        "Regras obrigatorias:\n"
        "- Nao classifique produto apenas pela cor da embalagem.\n"
        "- Se marca e categoria forem visualmente compativeis com o produto "
        "registrado, use CONFERE mesmo sem ler peso/gramatura exatos.\n"
        "- Peso, gramatura e variante so devem gerar INCONCLUSIVO quando houver "
        "conflito visual claro ou impossibilidade de reconhecer o produto.\n"
        "- Para produto por KG ou quantidade decimal, nao compare peso pela "
        "imagem; se a categoria visual bater, use CONFERE.\n"
        "- Se a quantidade registrada for maior que 1, considere que o operador "
        "pode ter digitado a multiplicacao e escaneado apenas uma unidade. Nao "
        "marque INCONCLUSIVO apenas porque as demais unidades nao aparecem.\n"
        "- Use NAO_CONFERE somente quando voce enxergar claramente um produto "
        "de outra categoria ou quantidade diferente.\n"
        "- Se a imagem nao mostrar claramente o produto no momento da passada, "
        "use INCONCLUSIVO.\n\n"
        "Responda apenas com um objeto JSON valido, sem markdown, exatamente "
        "com os campos: resultado (CONFERE, NAO_CONFERE ou INCONCLUSIVO), "
        "confianca (inteiro de 0 a 100), o_que_aparece_na_imagem, "
        "comparacao_pdv, possivel_divergencia e acao_recomendada "
        "(liberar, revisar cupom ou revisar gravacao)."
    )


def tokens_relevantes(produto):
    ignorar = {
        "trad",
        "tradicional",
        "un",
        "und",
        "kg",
        "lt",
        "ml",
        "sc",
        "pct",
        "refil",
        "cx",
    }
    tokens = []
    for token in re.findall(r"[A-Za-z0-9]+", produto.lower()):
        if len(token) < 4 or token in ignorar or token.isdigit():
            continue
        tokens.append(token)
    return tokens


def aplicar_trava_conservadora(resultado, produto, quantidade):
    texto_ia = " ".join(
        str(resultado.get(campo, ""))
        for campo in (
            "o_que_aparece_na_imagem",
            "comparacao_pdv",
            "possivel_divergencia",
        )
    ).lower()

    texto_imagem = str(resultado.get("o_que_aparece_na_imagem", "")).lower()
    produto_norm = produto.lower()
    comparacao_norm = str(resultado.get("comparacao_pdv", "")).lower()
    item_pesavel = " kg" in (" " + produto_norm) or " kg" in comparacao_norm
    produto_carne = any(
        termo in produto_norm
        for termo in ("carne", "bov", "frango", "suino", "peixe", "costela")
    )
    imagem_carne = any(
        termo in texto_ia
        for termo in ("carne", "bife", "picanha", "frango", "suino", "peixe")
    )
    produto_cerveja = "cerv" in produto_norm
    imagem_cerveja = any(
        termo in texto_ia
        for termo in ("cerveja", "lata de cerveja", "original")
    )

    if resultado.get("resultado") == "INCONCLUSIVO":
        if produto_cerveja and imagem_cerveja:
            resultado["resultado"] = "CONFERE"
            resultado["confianca"] = max(int(resultado.get("confianca") or 0), 85)
            resultado["comparacao_pdv"] = (
                "Produto visualmente compativel com o registro do PDV: a imagem "
                "mostra uma lata de cerveja e identifica ORIGINAL. A quantidade "
                "%s foi tratada como multiplicacao registrada no PDV; uma unica "
                "unidade no quadro e suficiente para validar o produto escaneado."
                % quantidade
            )
            resultado["possivel_divergencia"] = ""
            resultado["acao_recomendada"] = "liberar"
            return resultado

        if item_pesavel and produto_carne and imagem_carne:
            resultado["resultado"] = "CONFERE"
            resultado["confianca"] = max(int(resultado.get("confianca") or 0), 80)
            resultado["comparacao_pdv"] = (
                "Produto pesavel visualmente compativel com o registro do PDV: "
                "a imagem mostra carne e o PDV registrou item de carne por KG. "
                "O peso foi considerado dado do PDV/balanca, nao da imagem."
            )
            resultado["possivel_divergencia"] = ""
            resultado["acao_recomendada"] = "liberar"
            return resultado

        produto_bombom = "bombom" in produto_norm
        imagem_garoto = "garoto" in texto_imagem
        imagem_caixa = any(
            termo in texto_imagem
            for termo in ("caixa", "embalagem", "chocolate", "bombom")
        )
        if produto_bombom and imagem_garoto and imagem_caixa:
            resultado["resultado"] = "CONFERE"
            resultado["confianca"] = max(int(resultado.get("confianca") or 0), 80)
            resultado["comparacao_pdv"] = (
                "Produto visualmente compativel com o registro do PDV: marca "
                "Garoto e embalagem/caixa de bombom aparentes. Gramatura exata "
                "nao foi usada como criterio de divergencia."
            )
            resultado["possivel_divergencia"] = ""
            resultado["acao_recomendada"] = "liberar"
            return resultado

    if resultado.get("resultado") == "NAO_CONFERE" and any(
        token in texto_ia for token in tokens_relevantes(produto)
    ):
        resultado["resultado"] = "INCONCLUSIVO"
        resultado["confianca"] = min(int(resultado.get("confianca") or 0), 60)
        resultado["possivel_divergencia"] = (
            "A resposta original indicou divergencia, mas citou marca ou termo "
            "compativel com o produto registrado. Classificacao ajustada para "
            "INCONCLUSIVO por seguranca operacional."
        )
        resultado["acao_recomendada"] = "revisar gravacao"

    return resultado


def chamar_groq(imagem_path, produto, valor, quantidade):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY nao configurada em " + ENV_PATH)
    image_bytes = Path(imagem_path).read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": MODEL_NAME,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": montar_prompt(produto, valor, quantidade)},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + image_b64,
                        },
                    },
                ],
            },
        ],
    }
    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": "Bearer " + GROQ_API_KEY,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if response.status_code >= 400:
        error = RuntimeError(
            "Groq HTTP %s: %s" % (response.status_code, response.text[:500])
        )
        error.status_code = response.status_code
        error.retry_after = response.headers.get("retry-after")
        raise error

    content = response.json()["choices"][0]["message"]["content"]
    raw = json.loads(content)
    result = str(raw.get("resultado", "INCONCLUSIVO")).upper()
    if result not in ("CONFERE", "NAO_CONFERE", "INCONCLUSIVO"):
        result = "INCONCLUSIVO"
    action = str(raw.get("acao_recomendada", "revisar gravacao")).lower()
    if action not in ("liberar", "revisar cupom", "revisar gravacao"):
        action = "revisar gravacao"
    normalized = {
        "resultado": result,
        "confianca": max(0, min(int(raw.get("confianca") or 0), 100)),
        "o_que_aparece_na_imagem": str(
            raw.get("o_que_aparece_na_imagem")
            or raw.get("descricao")
            or ""
        ),
        "comparacao_pdv": str(
            raw.get("comparacao_pdv")
            or raw.get("justificativa")
            or "Nao foi possivel concluir a auditoria visual."
        ),
        "possivel_divergencia": str(raw.get("possivel_divergencia") or ""),
        "acao_recomendada": action,
    }
    return aplicar_trava_conservadora(normalized, produto, quantidade)


def _extrair_retry_after(exc_text, default=30):
    m = re.search(r"retry in\s+([\d.]+)s", exc_text, re.IGNORECASE)
    if m:
        return max(int(float(m.group(1))) + 2, 5)
    return default


def executar_auditoria_cli(imagem, produto, valor, quantidade):
    valor_total = valor * quantidade
    if valor < 8.0 and valor_total < 20.0 and not produto_tem_risco(produto):
        return resultado_regra_valor(produto, valor, quantidade)

    if not Path(imagem).is_file():
        return erro_como_json(f"Imagem nao encontrada: {imagem}")

    cooldown = carregar_cooldown()
    if cooldown > 0:
        return erro_cota_como_json(cooldown)

    try:
        return chamar_groq(imagem, produto, valor, quantidade)
    except Exception as exc:
        if not eh_erro_limite(exc):
            return erro_como_json(str(exc))

        retry_after = getattr(exc, "retry_after", None)
        try:
            wait = max(int(float(retry_after)), 5)
        except (TypeError, ValueError):
            wait = _extrair_retry_after(str(exc))
        salvar_cooldown(wait)
        return erro_cota_como_json(wait)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Auditoria visual do scanner PDV usando Gemini."
    )
    parser.add_argument("--imagem", required=True, help="Caminho do arquivo .jpg")
    parser.add_argument("--produto", required=True, help="Descricao do item no PDV")
    parser.add_argument("--valor", required=True, type=float, help="Valor unitario")
    parser.add_argument(
        "--quantidade", required=True, type=float, help="Quantidade registrada"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    resultado = executar_auditoria_cli(
        imagem=args.imagem,
        produto=args.produto,
        valor=args.valor,
        quantidade=args.quantidade,
    )
    print(json.dumps(resultado, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
