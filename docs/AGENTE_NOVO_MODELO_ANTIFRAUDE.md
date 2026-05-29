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
