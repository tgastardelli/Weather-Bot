# Estratégia Do Weather Bot

## Objetivo

O Weather Bot busca mercados de temperatura máxima da Polymarket em que o preço
do mercado diverge de uma probabilidade climática calibrada. A fase atual é de
pesquisa e geração de sinais paper-only. O bot não envia ordens reais.

## Tese Alto Risco / Alta Recompensa

A pergunta central não é apenas "qual bucket está barato?". A pergunta certa é:
"quais cidades produzem surpresas meteorológicas com frequência suficiente para
que buckets baratos sejam mal precificados?"

A versão 1 ranqueia cidades por surpresa histórica da previsão:

- Métrica principal: erro entre a previsão arquivada da máxima diária e a máxima
  observada na estação de resolução.
- Métrica secundária: volatilidade intradiária, medida a partir da temperatura
  horária observada.
- Métrica de cauda: frequência de grandes erros, como diferenças de 2 °C, 3 °C
  e 5 °C entre previsão e realizado.
- Métrica direcional: taxa de surpresa para cima e para baixo, porque erros de
  calor e frio apontam para buckets diferentes.

O bot não deve escolher cidades de alta recompensa por intuição. Ele deve
produzir um ranking objetivo do universo de cidades da Polymarket e depois
cruzar esse ranking com mercados ativos, liquidez, spreads e desempenho por
perfil.

## Estado Atual

- Modo: paper-only.
- Live trading: fora de escopo.
- Perfis implementados: `max_edge` e `longshot`.
- Fee obrigatória: 5% taker em mercados de clima.
- Dados disponíveis: eventos, mercados, books, snapshots de preço da
  Polymarket, forecasts históricos, observações diárias, pontos de
  `prices-history` e trades históricos públicos validados.
- Dados em construção: validação forward de fills paper contra book real,
  slippage por profundidade e settlement paper resolvido.
- Universo inicial de evidência: `seoul`, `tokyo` e `hong-kong`.
- Caminho para live: bloqueado por gates históricos, gates paper, geoblock,
  kill switch e limites de micro-capital.

## Fluxo De Dados

1. Descobrir eventos ativos de temperatura máxima na Polymarket.
2. Normalizar evento, bucket, tokens e metadados de estação.
3. Gravar preços e snapshots de book no SQLite.
4. Coletar previsões e membros de ensemble para a estação de resolução.
5. Coletar observações e máxima diária realizada.
6. Detectar mercados resolvidos e buckets vencedores.
7. Usar o histórico para calibrar probabilidades e backtestar sinais.

A estação usada pela resolução do mercado é o ponto climático verdadeiro. O bot
deve usar as coordenadas da estação, nunca o centro da cidade.

## Modelo De Probabilidade

A versão 0 estima `P(bucket)` a partir dos membros do ensemble:

- Converter máximas diárias dos membros para a unidade do mercado.
- Aplicar correção de viés por cidade/estação.
- Inflar o spread quando resíduos históricos mostrarem underdispersion.
- Aplicar a regra de arredondamento do mercado antes de alocar o membro no
  bucket.
- Contar a fração de membros em cada bucket.
- Aplicar clamp somente via configuração explícita.

Versões futuras devem comparar o modelo contra o mercado com Brier score,
curvas de calibração, resíduos por cidade, lead time e qualidade da fonte de
resolução.

## Edge De Mercado

Nesta fase o bot avalia apenas posições YES.

- Edge bruto: `model_probability - market_price`.
- Edge líquido: `gross_edge - taker_fee`.
- A fee taker de clima deve sempre ser descontada.
- A fórmula de fee por share segue a documentação oficial da Polymarket:
  `fee = fee_rate * price * (1 - price)`, com `fee_rate = 0.05` para clima.
- Preço, stake, PnL e valores sensíveis a fee permanecem como `Decimal` no
  backend.
- O frontend apenas formata valores; ele não recalcula decisões estratégicas.

## Perfis De Estratégia

### max_edge

`max_edge` considera qualquer bucket em que o modelo vê edge líquido suficiente
após fees.

Critérios:

- Evento ativo e dentro da janela configurada.
- Melhor ask disponível.
- Edge líquido maior ou igual a `min_edge_net`.
- Stake Kelly positivo após caps.
- Limites de exposição respeitados.

Hipótese:

As maiores divergências de EV devem performar melhor quando o modelo está
calibrado e a liquidez é utilizável.

Riscos:

- Viés do modelo por cidade ou estação.
- Books finos e asks obsoletos.
- Movimento rápido de preço antes da entrada.
- Leitura incorreta das regras de resolução.

### longshot

`longshot` foca buckets baratos em que a probabilidade do modelo está
materialmente acima do preço do mercado.

Critérios:

- Todos os critérios de `max_edge`.
- Ask menor ou igual a `longshot_max_price`.
- Preferência por cidades com alta surpresa histórica de previsão.

Hipótese:

Buckets extremos podem ser subprecificados quando o mercado confia demais na
previsão central ou subestima risco de cauda.

Riscos:

- Alta variância e longas sequências de perdas.
- Spreads maiores e slippage prático mais alto.
- Underdispersion do ensemble criando falsos positivos.

Encaixe estratégico:

`longshot` deve ser favorecido apenas quando a cidade tem histórico de surpresa
meteorológica. Buckets baratos em cidades estáveis tendem a ser baratos por um
bom motivo; buckets baratos em cidades historicamente surpreendentes podem ser
a zona de maior risco e maior recompensa do bot.

## Sizing E Risco

A versão 0 usa Kelly fracionário:

- Estimar custo por share como ask mais fee taker.
- Calcular a fee esperada com `fee_rate * ask * (1 - ask)`.
- Converter edge e probabilidade em fração Kelly bruta.
- Multiplicar pela fração Kelly configurada.
- Aplicar caps de bankroll, stake por ordem, exposição por mercado e perda
  diária.

As regras de risco já valem para sinais paper e devem valer antes de qualquer
caminho futuro de ordem real.

## Filtros De Sinal

Sinais só devem ser criados quando:

- O evento tem tempo suficiente até a resolução, mas não está longe demais.
- Existe snapshot recente de ensemble para cidade e data-alvo.
- Existe preço atual de mercado.
- Edge líquido passa o mínimo configurado.
- Sinais duplicados recentes são suprimidos.
- Limites de exposição não rejeitam o candidato.

Filtros planejados:

- Exigir confirmação por duas rodadas consecutivas do modelo.
- Penalizar slippage esperada por profundidade do book.
- Separar thresholds por cidade, lead time e tipo de bucket.
- Reduzir confiança em estações ou mercados marcados como `needs_review`.
- Restringir `longshot` em cidades com baixo ranking de surpresa histórica.

## Métricas De Backtest

Backtests devem comparar os perfis com:

- PnL total e ROI.
- Retorno composto.
- Drawdown máximo.
- Win rate.
- Profit factor.
- Brier score das probabilidades do modelo.
- Comparação contra probabilidades implícitas do mercado.
- Performance por cidade, estação, lead time, tipo de bucket e ranking de
  volatilidade/recompensa.

O backtest suporta três fontes:

- `stored-signals`: sinais paper gravados e mercados resolvidos.
- `replay_price_snapshots`: snapshots locais capturados pelo bot. Apenas
  forecasts/ensembles com `fetched_at <= price_snapshot.ts` podem ser usados. A
  execução é aproximada por `best_ask` taker, sem slippage por profundidade de
  book, com `execution_proxy = best_ask_taker_no_depth_slippage`.
- `historical_price_points`: pontos do CLOB `prices-history` em tabela separada.
  Quando `prices-history` vem vazio, o backfill tenta trades históricos públicos
  e só persiste a fonte se todos os trades retornados baterem o token,
  `conditionId` ou evento solicitado. Essa fonte mede Brier, PnL e ROI como
  proxy retrospectivo, mas não reconstrói book depth nem melhor ask. O resultado
  deve marcar `execution_proxy = historical_last_trade_no_book_depth` quando
  usar trades, ou `polymarket_prices_history_last_price_no_book_depth` quando
  usar apenas `prices-history`, sempre com calibração walk-forward e sem
  observação futura.

## Protocolo De Evidência

A estratégia só deve ser considerada promissora depois de passar por uma
esteira persistida de evidência com três camadas:

- Data health: cobertura de preços, books, forecasts, ensembles, observações e
  resoluções.
- Model health: viés, MAE, erros de cauda, calibração por cidade/modelo/lead e
  qualidade da estação.
- Trading evidence: PnL, ROI, Brier do modelo contra mercado, drawdown, profit
  factor, win rate e proxy de slippage.

Operacionalmente, a validação automatizada roda em paper-only: o scheduler
gera `evidence_runs` após rodadas de resolução e executa semanalmente
calibração, ranking de volatilidade, backtest `both`, validação histórica e
novo relatório de evidência.

O relatório `historical_validation_runs` é a primeira prova estatística da tese:
ele usa histórico climático e preços históricos da Polymarket para estimar se o
modelo bate o mercado em Brier e PnL após fee. Ele não aprova execução live,
porque last price histórico não prova fill FAK nem slippage.

O relatório também expõe `price_source_counts`, separando
`clob_prices_history` de `data_api_trades`. Respostas de trades que aparentam
ignorar o filtro de token/mercado são rejeitadas e não entram no backtest.
O backfill histórico longo roda em janelas persistidas em
`history_backfill_runs`, usando `--chunk-days`, `--resume` e concorrência
limitada. Janelas completas podem ser puladas em novas execuções, e cada janela
registra contagens de eventos, mercados, pontos de histórico, trades aceitos,
fontes rejeitadas e erros.

Antes de qualquer plano com capital real, o bot também precisa passar pelo
relatório `measurement_runs`. Esse relatório valida fills paper contra books
capturados, fee oficial, ledger de posições, settlement, slippage e divergência
entre replay e execução paper.

Gates mínimos:

- Pelo menos 120 pares forecast/observed por cidade foco na validação
  histórica.
- Pelo menos 50 trades históricos resolvidos em `max_edge`.
- Brier histórico do modelo menor que Brier do mercado em `max_edge`.
- PnL histórico líquido positivo após fee, sem concentração extrema em poucos
  trades.
- `ensemble_members > 0` para eventos ativos das cidades foco.
- Pelo menos 30 dias de coleta forward e 50 fills paper resolvidos antes de
  aprovar execução.
- Brier do modelo menor que Brier do mercado em `max_edge`.
- PnL líquido positivo após fee no replay forward.
- PnL líquido positivo após fee na execução paper.
- `measurement_runs.status = READY_FOR_LIVE_REVIEW`.
- Nenhuma cidade foco pode estar `needs_review`.

## Live Readiness E Micro-Capital

O repositório pode expor readiness para live, mas não deve enviar ordens reais
sem uma fase separada e aprovação explícita. O `LiveEngine` inicial existe
apenas como guarda de segurança: ele calcula blockers e recusa submissão de
ordens.

Condições obrigatórias antes de qualquer piloto:

- `MODE=live` e `LIVE_TRADING_ENABLED=true` configurados explicitamente.
- Kill switch funcional e não engajado.
- Geoblock da Polymarket aprovado no preflight.
- Histórico com status `PROMISING`.
- Measurement com status `READY_FOR_LIVE_REVIEW`.
- Limites conservadores de micro-capital: bankroll até 100 USDC, stake máximo
  por ordem até 5 USDC, exposição por mercado até 15 USDC e perda diária até
  10 USDC.
- Apenas o perfil `max_edge`; `longshot` continua separado e paper-only até
  provar drawdown e sequência de perdas aceitáveis.

Mesmo após esses gates, a primeira fase live deve ser um piloto de
micro-capital com BUY YES taker FAK, parada automática por perda diária, erro de
settlement, geoblock, divergência de ledger ou slippage acima do observado no
paper.

As conclusões devem ficar separadas por perfil. Para `max_edge`, as métricas
principais são Brier e ROI ajustado por drawdown. Para `longshot`, as métricas
principais são ROI, taxa de acerto em cauda, drawdown e sequência máxima de
perdas.

## Riscos Conhecidos

- Books da Polymarket são efêmeros e não podem ser totalmente recuperados de
  forma retroativa.
- Histórico de preços aproxima execução, mas não substitui profundidade real de
  book.
- Buckets extremos costumam ter liquidez fina.
- Fontes de resolução e regras de arredondamento variam por cidade.
- Ensembles climáticos podem ser subdispersos em extremos.
- Metadados de estação extraídos automaticamente precisam de revisão.

## Backlog Estratégico

- Popular histórico de forecasts, ensembles, observações e resoluções.
- Ranqueiar cidades Polymarket por surpresa de previsão e volatilidade
  intradiária.
- Calibrar por cidade, estação, modelo e lead time.
- Adicionar backtest histórico por replay de sinais.
- Adicionar modelo de slippage usando books capturados.
- Confirmar sinais em múltiplas rodadas de previsão.
- Penalizar estações `needs_review` ou com baixa qualidade de dados.
- Fatiar resultados por cidade, lead time, tipo de bucket e perfil.
- Gerar e revisar `evidence_runs` após rodadas de resolução.
- Manter execução paper-only até backtests e controles de risco justificarem a
  próxima fase.
