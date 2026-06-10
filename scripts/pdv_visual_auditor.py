#!/opt/pdv-visual-auditor/venv/bin/python
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv


ENV_PATHS = (
    "/etc/pdv-intelbras-imhdx/.env",
    "/etc/pdv-telegram-assistant.env",
    "/etc/pdv-visual-auditor.env",
)
for env_path in ENV_PATHS:
    load_dotenv(env_path, override=False)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "meta-llama/llama-4-scout-17b-16e-instruct"
COOLDOWN_PATH = Path("/var/lib/pdv-visual-auditor/groq_cooldown.json")
RESULTS_PATH = Path("/var/lib/pdv-visual-auditor/results.jsonl")
REQUESTS_PATH = Path("/var/lib/pdv-visual-auditor/request_times.json")
MAX_CALLS_PER_MINUTE = int(os.environ.get("GROQ_MAX_CALLS_PER_MINUTE", "3"))
MAX_CALLS_PER_HOUR = int(os.environ.get("GROQ_MAX_CALLS_PER_HOUR", "30"))

SYSTEM_PROMPT = (
    "Voce e um observador visual de caixas de supermercado. Identifique apenas "
    "o produto fisico que esta na mao do operador ou atravessa o scanner. Nao "
    "recebera o nome registrado no PDV, para evitar confirmacao induzida. Ignore "
    "produtos parados em expositores, bancadas e ao fundo. Use a continuidade "
    "entre os paineis ANTES, BIP e DEPOIS. Nunca chute categoria apenas pela cor."
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
        "resultado": "NAO_ANALISADO",
        "confianca": None,
        "o_que_aparece_na_imagem": "",
        "comparacao_pdv": "A auditoria visual nao foi executada.",
        "possivel_divergencia": "",
        "acao_recomendada": "revisar gravacao",
        "erro_tecnico": mensagem,
    }


def erro_cota_como_json(espera_segundos):
    espera = max(int(espera_segundos), 1)
    return {
        "resultado": "NAO_ANALISADO",
        "confianca": None,
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
        "erro_tecnico": "Cota temporaria da Groq atingida.",
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


def reservar_chamada_api():
    now = time.time()
    try:
        timestamps = [
            float(value)
            for value in json.loads(REQUESTS_PATH.read_text()).get("timestamps", [])
            if now - float(value) < 3600
        ]
    except Exception:
        timestamps = []

    last_minute = [value for value in timestamps if now - value < 60]
    if len(last_minute) >= MAX_CALLS_PER_MINUTE:
        wait = max(int(60 - (now - min(last_minute))) + 1, 1)
        return False, wait, "LIMITE_LOCAL_MINUTO"
    if len(timestamps) >= MAX_CALLS_PER_HOUR:
        wait = max(int(3600 - (now - min(timestamps))) + 1, 1)
        return False, wait, "LIMITE_LOCAL_HORA"

    REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamps.append(now)
    temporary = REQUESTS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"timestamps": timestamps}))
    temporary.replace(REQUESTS_PATH)
    return True, 0, ""


def erro_limite_local(espera_segundos, motivo):
    resultado = erro_cota_como_json(espera_segundos)
    resultado["erro_api"] = motivo
    resultado["erro_tecnico"] = (
        "Chamada bloqueada pela protecao local para evitar exceder a Groq."
    )
    return resultado


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
        "Identifique de forma independente o produto movimentado na area do "
        "scanner. Nao tente adivinhar o que foi registrado no sistema.\n\n"
        "A imagem pode conter tres paineis temporais rotulados ANTES, BIP e "
        "DEPOIS. Eles representam o mesmo evento. Identifique o produto que "
        "atravessa ou permanece na area do scanner em pelo menos dois paineis. "
        "Nao use um produto parado ao fundo ou no expositor como item do bip. "
        "Se os paineis mostrarem produtos diferentes e nao houver continuidade, "
        "marque identificacao_conclusiva como false.\n\n"
        "Quando possivel, use uma destas categorias em categoria_visual: "
        "cerveja, carne, bombom, cafe, feijao, arroz, refrigerante, azeite, "
        "sabao, leite, acucar, oleo, biscoito, salgadinho, papel_higienico, "
        "margarina ou macarrao. Use 'desconhecido' quando nenhuma se aplicar.\n\n"
        "Responda apenas JSON com: identificacao_conclusiva (boolean), "
        "confianca_visual (0 a 100), categoria_visual, marca_visual, "
        "produto_visual e evidencia_temporal. Nao mencione dados do PDV."
    )


def montar_prompt_presenca():
    return (
        "Verifique exclusivamente se um produto fisico de varejo atravessa ou "
        "permanece sobre a area do scanner durante a sequencia ANTES, BIP e "
        "DEPOIS. Ignore maos, bracos, teclado, sacolas vazias, maquininha de "
        "cartao, dinheiro e objetos parados ao fundo. Nao tente identificar o "
        "item registrado no sistema.\n\n"
        "Responda apenas JSON com: produto_presente (boolean), "
        "confianca_visual (0 a 100), evidencia_visual e objeto_principal. "
        "Use produto_presente=false somente quando a area estiver visivel e "
        "nenhum produto fisico cruzar o scanner. Se houver oclusao ou duvida, "
        "reduza a confianca."
    )


def normalizar_texto(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = texto.encode("ascii", "ignore").decode("ascii")
    return texto.lower()


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
    for token in re.findall(r"[a-z0-9]+", normalizar_texto(produto)):
        if len(token) < 4 or token in ignorar or token.isdigit():
            continue
        tokens.append(token)
    return tokens


def categorias_do_texto(texto):
    texto = normalizar_texto(texto)
    grupos = {
        "cerveja": ("cerveja", "cerv ", "lata original", "antarctica"),
        "carne": (
            "carne",
            "bife",
            "picanha",
            "costela",
            "frango",
            "suino",
            "peixe",
        ),
        "bombom": ("bombom", "chocolate", "garoto", "caixa amarela"),
        "cafe": ("cafe", "marata"),
        "feijao": ("feijao",),
        "arroz": ("arroz",),
        "refrigerante": ("refrigerante", "refri ", "coca cola", "guarana"),
        "azeite": ("azeite",),
        "sabao": ("sabao", "detergente", "lava roupa"),
        "leite": ("leite",),
        "acucar": ("acucar",),
        "oleo": ("oleo",),
        "biscoito": ("biscoito", "bolacha",),
        "salgadinho": ("salgadinho", "snack",),
        "papel_higienico": ("papel higienico",),
        "margarina": ("margarina",),
        "macarrao": ("macarrao",),
    }
    return {
        categoria
        for categoria, termos in grupos.items()
        if any(termo in texto for termo in termos)
    }


def imagem_sem_evidencia(texto):
    texto = normalizar_texto(texto)
    termos = (
        "scanner vazio",
        "balanca vazia",
        "nenhum produto",
        "nao ha produto",
        "produto nao aparece",
        "produto nao esta visivel",
        "totalmente encoberto",
        "imagem escura",
        "imagem desfocada",
        "nao e possivel identificar",
    )
    return not texto.strip() or any(termo in texto for termo in termos)


def normalizar_inconclusivo(resultado, produto, quantidade):
    texto_imagem = normalizar_texto(
        resultado.get("o_que_aparece_na_imagem", "")
    )
    categorias_produto = categorias_do_texto(produto)
    categorias_imagem = categorias_do_texto(texto_imagem)
    categorias_compativeis = categorias_produto & categorias_imagem
    tokens_identificados = [
        token
        for token in tokens_relevantes(produto)
        if token in texto_imagem
    ]

    if categorias_compativeis or tokens_identificados:
        evidencia = sorted(categorias_compativeis) or tokens_identificados
        resultado["resultado"] = "CONFERE"
        resultado["confianca"] = max(
            int(resultado.get("confianca") or 0),
            80 if float(quantidade or 0) <= 1 else 85,
        )
        resultado["comparacao_pdv"] = (
            "A imagem reconhece evidencia visual compativel com o item do PDV "
            "(%s). Peso, gramatura e a quantidade total da linha nao precisam "
            "estar legiveis no quadro do scanner."
            % ", ".join(evidencia)
        )
        resultado["possivel_divergencia"] = ""
        resultado["acao_recomendada"] = "liberar"
        return resultado

    if imagem_sem_evidencia(texto_imagem):
        resultado["confianca"] = min(
            max(int(resultado.get("confianca") or 0), 20),
            35,
        )
        resultado["possivel_divergencia"] = (
            resultado.get("possivel_divergencia")
            or "O produto nao aparece com evidencia suficiente no quadro."
        )
    else:
        resultado["confianca"] = min(
            max(int(resultado.get("confianca") or 0), 40),
            65,
        )
    resultado["acao_recomendada"] = "revisar gravacao"
    return resultado


def aplicar_trava_conservadora(resultado, produto, quantidade):
    texto_ia = " ".join(
        str(resultado.get(campo, ""))
        for campo in (
            "o_que_aparece_na_imagem",
            "comparacao_pdv",
            "possivel_divergencia",
        )
    ).lower()

    texto_imagem = normalizar_texto(
        resultado.get("o_que_aparece_na_imagem", "")
    )
    produto_norm = normalizar_texto(produto)
    comparacao_norm = normalizar_texto(resultado.get("comparacao_pdv", ""))
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
        tokens_produto = tokens_relevantes(produto)
        tokens_identificados = [
            token for token in tokens_produto if token in texto_imagem
        ]
        if float(quantidade or 0) > 1 and tokens_identificados:
            resultado["resultado"] = "CONFERE"
            resultado["confianca"] = max(int(resultado.get("confianca") or 0), 85)
            resultado["comparacao_pdv"] = (
                "Produto visualmente compativel com o registro do PDV: a "
                "imagem identificou %s. A quantidade %s foi tratada como "
                "multiplicacao informada no caixa; a imagem do momento do "
                "scanner precisa mostrar a unidade escaneada, nao todas as "
                "unidades da linha."
                % (", ".join(tokens_identificados), quantidade)
            )
            resultado["possivel_divergencia"] = ""
            resultado["acao_recomendada"] = "liberar"
            return resultado

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

        return normalizar_inconclusivo(resultado, produto, quantidade)

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


def gerar_recorte_scanner(imagem_path):
    if str(imagem_path).endswith("_sequence.jpg"):
        return None
    temp = tempfile.NamedTemporaryFile(
        prefix="pdv_scanner_",
        suffix=".jpg",
        delete=False,
    )
    temp.close()
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(imagem_path),
        "-vf",
        (
            "crop=iw*0.62:ih*0.75:iw*0.35:ih*0.22,"
            "scale=1280:-2:flags=lanczos"
        ),
        "-frames:v",
        "1",
        temp.name,
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if result.returncode != 0 or Path(temp.name).stat().st_size < 1024:
        Path(temp.name).unlink(missing_ok=True)
        return None
    return Path(temp.name)


def comparar_identificacao_visual(raw, produto, quantidade):
    valor_conclusiva = raw.get("identificacao_conclusiva")
    conclusiva = valor_conclusiva is True or normalizar_texto(
        valor_conclusiva
    ) in {"true", "1", "sim"}
    confianca_visual = max(
        0,
        min(int(raw.get("confianca_visual") or 0), 100),
    )
    categoria = str(raw.get("categoria_visual") or "")
    marca = str(raw.get("marca_visual") or "")
    produto_visual = str(raw.get("produto_visual") or "")
    evidencia = str(raw.get("evidencia_temporal") or "")
    descricao_visual = " ".join(
        value for value in (produto_visual, categoria, marca) if value
    ).strip()

    if not conclusiva or not descricao_visual:
        return {
            "resultado": "INCONCLUSIVO",
            "confianca": min(max(confianca_visual, 20), 65),
            "o_que_aparece_na_imagem": produto_visual or categoria,
            "comparacao_pdv": (
                "A sequencia nao permitiu identificar com seguranca o produto "
                "que atravessou o scanner."
            ),
            "possivel_divergencia": evidencia,
            "acao_recomendada": "revisar gravacao",
        }

    categorias_produto = categorias_do_texto(produto)
    categorias_imagem = categorias_do_texto(descricao_visual)
    tokens_produto = tokens_relevantes(produto)
    texto_visual = normalizar_texto(descricao_visual)
    texto_produto_visual = normalizar_texto(produto_visual)
    marca_normalizada = normalizar_texto(marca)
    tokens_compativeis = [
        token for token in tokens_produto if token in texto_visual
    ]

    if (categorias_produto & categorias_imagem) or tokens_compativeis:
        compatibilidade = sorted(categorias_produto & categorias_imagem)
        compatibilidade = compatibilidade or tokens_compativeis
        return {
            "resultado": "CONFERE",
            "confianca": max(confianca_visual, 80),
            "o_que_aparece_na_imagem": produto_visual or descricao_visual,
            "comparacao_pdv": (
                "A identificacao visual independente (%s) e compativel com o "
                "produto registrado no PDV: %s."
                % (", ".join(compatibilidade), produto)
            ),
            "possivel_divergencia": "",
            "acao_recomendada": "liberar",
        }

    marca_conhecida = marca_normalizada not in {
        "",
        "desconhecido",
        "nao identificado",
        "nao identificada",
        "ilegivel",
    }
    categoria_confirmada_na_descricao = bool(
        categorias_do_texto(texto_produto_visual)
    )
    identificacao_forte = (
        confianca_visual >= 85
        and (marca_conhecida or categoria_confirmada_na_descricao)
    )

    if categorias_produto and categorias_imagem and identificacao_forte:
        return {
            "resultado": "NAO_CONFERE",
            "confianca": max(confianca_visual, 85),
            "o_que_aparece_na_imagem": produto_visual or descricao_visual,
            "comparacao_pdv": (
                "A visao identificou %s, enquanto o PDV registrou %s."
                % (descricao_visual, produto)
            ),
            "possivel_divergencia": (
                "Categorias diferentes: imagem=%s; PDV=%s."
                % (
                    ", ".join(sorted(categorias_imagem)),
                    ", ".join(sorted(categorias_produto)),
                )
            ),
            "acao_recomendada": "revisar cupom",
        }

    return {
        "resultado": "INCONCLUSIVO",
        "confianca": min(max(confianca_visual, 40), 70),
        "o_que_aparece_na_imagem": produto_visual or descricao_visual,
        "comparacao_pdv": (
            "A visao descreveu o objeto, mas a marca ou a categoria nao ficou "
            "forte o bastante para gerar um alerta automatico."
        ),
        "possivel_divergencia": evidencia,
        "acao_recomendada": "revisar gravacao",
    }


def comparar_presenca_visual(raw):
    valor_presente = raw.get("produto_presente")
    produto_presente = valor_presente is True or normalizar_texto(
        valor_presente
    ) in {"true", "1", "sim"}
    confianca = max(
        0,
        min(int(raw.get("confianca_visual") or 0), 100),
    )
    evidencia = str(raw.get("evidencia_visual") or "")
    objeto = str(raw.get("objeto_principal") or "")

    if produto_presente and confianca >= 60:
        return {
            "resultado": "CONFERE",
            "confianca": confianca,
            "o_que_aparece_na_imagem": objeto,
            "comparacao_pdv": (
                "Foi identificada passagem fisica de produto na janela do VIT."
            ),
            "possivel_divergencia": "",
            "acao_recomendada": "liberar",
        }

    if not produto_presente and confianca >= 88:
        return {
            "resultado": "NAO_CONFERE",
            "confianca": confianca,
            "o_que_aparece_na_imagem": objeto,
            "comparacao_pdv": (
                "Houve registro de item no PDV, mas a sequencia nao mostra "
                "produto fisico atravessando o scanner."
            ),
            "possivel_divergencia": evidencia,
            "acao_recomendada": "revisar gravacao",
            "tipo_alerta": "REGISTRO_SEM_PASSAGEM_VISUAL",
        }

    return {
        "resultado": "INCONCLUSIVO",
        "confianca": min(confianca, 75),
        "o_que_aparece_na_imagem": objeto,
        "comparacao_pdv": (
            "A sequencia nao permitiu confirmar nem descartar a passagem de "
            "um produto fisico."
        ),
        "possivel_divergencia": evidencia,
        "acao_recomendada": "revisar gravacao",
    }


def chamar_groq(imagem_path, produto, valor, quantidade, modo="produto"):
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY nao configurada em nenhum dos arquivos: "
            + ", ".join(ENV_PATHS)
        )
    image_paths = []
    crop_path = gerar_recorte_scanner(imagem_path)
    if crop_path:
        image_paths.append(crop_path)
    image_paths.append(Path(imagem_path))
    image_parts = []
    for path in image_paths:
        image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        image_parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + image_b64,
                },
            }
        )
    payload = {
        "model": MODEL_NAME,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            montar_prompt_presenca()
                            if modo == "presenca"
                            else montar_prompt(produto, valor, quantidade)
                        ),
                    },
                ] + image_parts,
            },
        ],
    }
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": "Bearer " + GROQ_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
    finally:
        if crop_path:
            crop_path.unlink(missing_ok=True)
    if response.status_code >= 400:
        error = RuntimeError(
            "Groq HTTP %s: %s" % (response.status_code, response.text[:500])
        )
        error.status_code = response.status_code
        error.retry_after = response.headers.get("retry-after")
        raise error

    content = response.json()["choices"][0]["message"]["content"]
    raw = json.loads(content)
    resultado = (
        comparar_presenca_visual(raw)
        if modo == "presenca"
        else comparar_identificacao_visual(raw, produto, quantidade)
    )
    resultado["identificacao_visual"] = raw
    return resultado


def _extrair_retry_after(exc_text, default=30):
    m = re.search(r"retry in\s+([\d.]+)s", exc_text, re.IGNORECASE)
    if m:
        return max(int(float(m.group(1))) + 2, 5)
    return default


def executar_auditoria_cli(
    imagem,
    produto,
    valor,
    quantidade,
    modo="produto",
    forcar_api=False,
):
    valor_total = valor * quantidade
    if (
        modo == "produto"
        and not forcar_api
        and valor < 8.0
        and valor_total < 20.0
        and not produto_tem_risco(produto)
    ):
        return resultado_regra_valor(produto, valor, quantidade)

    if not Path(imagem).is_file():
        return erro_como_json(f"Imagem nao encontrada: {imagem}")

    cooldown = carregar_cooldown()
    if cooldown > 0:
        return erro_cota_como_json(cooldown)

    permitido, espera, motivo = reservar_chamada_api()
    if not permitido:
        return erro_limite_local(espera, motivo)

    try:
        return chamar_groq(imagem, produto, valor, quantidade, modo=modo)
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
    parser.add_argument(
        "--modo",
        choices=("produto", "presenca"),
        default="produto",
        help="Tipo de auditoria visual",
    )
    parser.add_argument(
        "--forcar-api",
        action="store_true",
        help="Ignora apenas a regra local de valor; os limites de API continuam ativos",
    )
    return parser.parse_args()


def registrar_resultado(args, resultado):
    registro = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "imagem": str(args.imagem),
        "produto": str(args.produto),
        "valor_unitario": float(args.valor),
        "quantidade": float(args.quantidade),
        "modo": str(args.modo),
        "resultado": resultado,
    }
    try:
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RESULTS_PATH.open("a", encoding="utf-8") as output:
            output.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception:
        # O historico nunca pode quebrar a saida JSON consumida pelo Telegram.
        pass


def main():
    args = parse_args()
    resultado = executar_auditoria_cli(
        imagem=args.imagem,
        produto=args.produto,
        valor=args.valor,
        quantidade=args.quantidade,
        modo=args.modo,
        forcar_api=args.forcar_api,
    )
    registrar_resultado(args, resultado)
    print(json.dumps(resultado, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
