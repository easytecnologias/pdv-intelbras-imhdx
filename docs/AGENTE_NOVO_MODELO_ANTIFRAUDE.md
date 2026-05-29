# Agente de Estudo do Novo Modelo Antifraude

## Missao

Estudar o fluxo real do caixa e propor um novo modelo antifraude antes de qualquer implementacao em producao.

## Regras

- Nao criar alerta automatico sem evidencia objetiva.
- Nao usar pulso de linha como quantidade de item.
- Nao tratar movimento de mao, braco, sacola ou operador como fraude.
- Separar coleta, rotulagem, treino, validacao e producao.
- Validar com imagens reais do PDV antes de ativar Telegram.

## Evidencias a Coletar

- Imagem antes do scanner.
- Imagem no scanner.
- Imagem depois do scanner.
- Evento `VIT` correspondente.
- Evento `CSP` correspondente.
- Horario exato do cupom.
- Item vendido, quantidade e unidade.
- Casos sem fraude que antes geravam falso positivo.

## Classes Candidatas

- `produto_entrada`
- `produto_scanner`
- `produto_saida`
- `mao_com_produto`
- `mao_sem_produto`
- `sacola`
- `cesta`
- `operador`
- `cliente`

## Perguntas que o Agente Deve Responder

- O que realmente caracteriza uma venda visualmente?
- Qual evidencia visual precisa existir para suspeitar?
- Quanto tempo depois do `VIT` o produto costuma cruzar o scanner?
- Quais movimentos normais parecem fraude?
- Quais regras antigas devem virar apenas log?
- Qual metrica minima de precisao libera teste controlado?

## Saida Esperada

- Plano de coleta.
- Plano de rotulagem.
- Dataset minimo.
- Regras de validacao.
- Criterios para ligar alerta Telegram.
- Lista de riscos operacionais.

## Agente Passivo Implementado

Servico proposto: `pdv-learning-agent.service`.

Funcoes:

- Observar snapshot HTTP da camera.
- Ler eventos recentes do Espiao.
- Salvar amostras quando houver mudanca de cena ou evento do PDV.
- Gravar metadados com `label_status=pending_human_review`.
- Gerar uma impressao visual simples da imagem.
- Classificar contexto operacional provavel, sem acusar fraude.
- Atualizar um arquivo de entrega para o futuro agente antifurto.
- Nao gerar alerta.
- Nao classificar fraude.
- Nao enviar Telegram.

Destino:

```text
/var/log/pdv-learning-agent/AAAAMMDD/images
/var/log/pdv-learning-agent/AAAAMMDD/metadata.jsonl
/var/log/pdv-learning-agent/knowledge/lessons.jsonl
/var/log/pdv-learning-agent/knowledge/future_antitheft_handoff.json
```

Contextos aprendidos:

- `venda_confirmada`
- `consulta_preco`
- `pagamento`
- `cupom_aberto`
- `movimento_sem_evento_pdv`
- `ambiente`

## Agente Antifurto Sombra

Servico proposto: `pdv-shadow-antitheft.service`.

Funcoes:

- Consumir `lessons.jsonl` do agente de aprendizado.
- Classificar prioridade de revisao sem acusar furto.
- Separar exemplos normais de candidatos para revisao humana.
- Manter `no_accusation=true` em todos os registros.
- Nao enviar Telegram.
- Nao interferir no caixa.

Destino:

```text
/var/log/pdv-shadow-antitheft/AAAAMMDD/observations.jsonl
/var/log/pdv-shadow-antitheft/AAAAMMDD/review_queue.jsonl
/var/log/pdv-shadow-antitheft/summary.json
```

Uso no teste:

- Rodar por 2 a 3 dias.
- Revisar manualmente a fila `review_queue.jsonl`.
- Usar os acertos e erros como base para o modelo antifurto real.
