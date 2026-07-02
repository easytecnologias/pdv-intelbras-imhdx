import base64, json, os, urllib.request, re

gemini_key = os.environ.get("GEMINI_API_KEY", "")  # nunca hardcode — use variável de ambiente

with open(r'C:\PROJETOS\Mikrotik\pdv-intelbras-imhdx\snap_latest.jpg', 'rb') as f:
    jpeg = f.read()
b64 = base64.b64encode(jpeg).decode()

prompt = (
    "Voce e um sistema de prevencao de perdas em supermercado.\n"
    "Analise esta imagem de camera do caixa.\n\n"
    "Contexto:\n"
    "- Produto registrado no PDV nos ultimos 10 segundos: nenhum\n"
    "- Camera posicionada acima do caixa, angulo de teto\n\n"
    'Responda APENAS com JSON: {"suspeito": true/false, "motivo": "explicacao em ate 15 palavras"}\n\n'
    "Suspeito = produto visivelmente na area do scanner SEM registro no PDV.\n"
    "Nao suspeito = produto COM registro, ou nenhum produto visivel.\n"
    "Seja conservador. Responda SOMENTE o JSON."
)

payload = json.dumps({
    "contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
    ]}]
}).encode()

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

with urllib.request.urlopen(req, timeout=15) as resp:
    data = json.loads(resp.read())

raw = data["candidates"][0]["content"]["parts"][0]["text"]
print("Bruto:", raw)
clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
result = json.loads(clean)
print("\n>>> suspeito:", result["suspeito"])
print(">>> motivo  :", result["motivo"])
