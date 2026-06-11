# Relatório para o Gerente — Por que o iMHDX não entrega o vídeo de 15 segundos
**Data:** 06/06/2026 | **PDV:** 001 (192.168.24.97)

---

## Resumo executivo

Quando o sistema detecta uma suspeita e o supervisor solicita o clipe de vídeo, o iMHDX não entrega o arquivo. O motivo é técnico e tem três causas encadeadas, explicadas abaixo em linguagem simples.

---

## Como deveria funcionar

```
1. Sistema detecta suspeita → alerta no Telegram com foto
2. Supervisor toca em "Ver vídeo"
3. Sistema vai ao iMHDX e pede o clipe do horário do alerta (15 a 20 segundos)
4. iMHDX entrega o arquivo de vídeo
5. Sistema converte e envia no Telegram
```

---

## Por que não funciona — as 3 causas

### Causa 1 — O botão "Ver vídeo" não existe na tela de alerta

O alerta que chega no Telegram hoje mostra apenas dois botões:

```
[ ✅ Fraude real ]  [ ❌ Falso positivo ]
```

O botão **"Ver vídeo"** nunca foi adicionado à mensagem de alerta. A função que baixa e envia o vídeo existe no sistema, mas nenhum botão a aciona. O supervisor não tem como solicitar o vídeo pela interface atual.

**Impacto:** o fluxo de vídeo é completamente inacessível para o usuário.

---

### Causa 2 — O iMHDX entrega um formato proprietário que precisa de conversão

Quando o sistema pede o clipe ao iMHDX, o gravador não entrega um arquivo `.mp4` pronto para envio. Ele entrega um arquivo `.dav` — formato proprietário da Intelbras/Dahua. Esse arquivo precisa ser convertido para `.mp4` dentro do PDV antes de ser enviado ao Telegram.

O conversor (`ffmpeg`) **está instalado** no PDV1, então essa etapa funciona — mas é uma etapa a mais que pode falhar se:
- O vídeo for muito grande (câmera em alta resolução)
- O processo demorar mais de 60 segundos
- O arquivo `.dav` vier corrompido ou incompleto

---

### Causa 3 — O iMHDX pode não ter a gravação disponível no horário exato

O sistema pede ao iMHDX o vídeo de **5 segundos antes até 15 segundos depois** do horário do alerta. Se o iMHDX estiver configurado para gravação por movimento (e não contínua), pode não haver gravação naquele instante exato — especialmente se o movimento terminou antes do sistema pedir.

---

## O que foi identificado hoje

Durante a investigação foi encontrado um erro no código do agente que causava **travamento do serviço** toda vez que o Groq retornava uma análise. O erro impedia o sistema de funcionar desde as 11:59 de hoje. O problema foi corrigido e o serviço está rodando novamente desde as 12:07.

Esse erro não tem relação com o vídeo — foi corrigido como parte da manutenção do dia.

---

## O que precisa ser feito para o vídeo funcionar

| Ação | Responsável | Complexidade | Estimativa |
|---|---|---|---|
| Adicionar botão "Ver vídeo" na mensagem de alerta | TI | Baixa | 1 hora |
| Testar download do `.dav` do iMHDX no horário do alerta | TI | Média | 2 horas |
| Verificar se gravação é contínua ou por movimento no canal 1 | Operação / TI | Baixa | 30 minutos |
| Ajustar timeout se o download for lento | TI | Baixa | 30 minutos |

---

## Estado atual do sistema (após correção de hoje)

| Componente | Status |
|---|---|
| Agente antifurto (câmera + Groq) | ✅ Rodando — corrigido às 12:07 |
| Detecção de tipo de furto no alerta | ✅ Ativo |
| Assistente Telegram | ✅ Rodando |
| Botão "Ver vídeo" no alerta | ❌ Não implementado |
| Download de vídeo do iMHDX | ❌ Não testado em produção |
