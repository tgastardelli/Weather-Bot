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
- Status atual de aprovação: `NEEDS_MODEL_REPAIR`; o histórico mostrou amostra
  suficiente, mas `max_edge` ficou superconfiante e perdeu para mercado em
  Brier/PnL.
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

### Data-Alvo De Evento

Para mercados historicos da Gamma/Polymarket, `gameStartTime` pode vir deslocado
em relacao ao dia descrito no slug do evento. A normalizacao agora usa, em ordem:

1. Data extraida do slug `highest-temperature-in-<city>-on-<month>-<day>`.
2. `gameStartTime`, quando o slug nao informa uma data parseavel.
3. `endDate - 1 dia`, apenas como fallback.

Eventos ja persistidos podem ser reparados com `analysis.event_target_date_repair`.
Esse reparo nao cria sinais, ordens ou fills; ele apenas alinha `Event.target_date`
ao dia de resolucao auditavel.

### Promocao De Cidades

Cidades novas entram como `needs_review=true` e so passam para o universo
operacional depois de uma auditoria de resolucao:

- Observacoes `DailyObservedMax(source="resolution")` vindas de fonte oficial ou
  CSV local reconstruivel.
- `mismatch_rate <= 0.02` contra `Market.winner`.
- `missing_observations = 0`.
- Historico de mercado validado.
- Nenhuma quarentena operacional.

ERA5 continua util para diagnostico e backfill climatico, mas nao prova promocao
para V5, shadow paper, paper execution ou live-readiness.

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

### Camada De Reparo De Probabilidade

Quando a validação histórica reprova por superconfiança, o bot aplica uma
camada de calibração walk-forward depois de `bucket_probabilities()` e antes de
`net_edge()`.

A primeira versão calibra por:

- `city_slug`.
- `bucket_kind`.
- bucket da probabilidade bruta do modelo.
- bucket do preço de mercado.
- bucket de horas até fechamento.

O fallback é conservador: segmento específico, cidade + bucket de
probabilidade, bucket global de probabilidade e, por fim, calibração global.
Cada segmento exige no mínimo 50 amostras já observadas em datas anteriores ao
ponto simulado. A probabilidade calibrada fica limitada a `0.80` nesta primeira
versão.

Filtros de reparo iniciais bloqueiam:

- `price_bucket = 0.00-0.05`.
- `bucket_kind = above` quando a probabilidade bruta está em `0.9-1.0`.
- `edge_bucket = 0.75+` quando a calibração indicar superconfiança.

Esses filtros existem para provar medição e reduzir excesso de confiança; eles
não autorizam live trading.

A versão de reparo v2 adiciona uma âncora no mercado para reduzir
superconfiança:

- `p_smoothed = (wins + 20 * global_rate) / (n + 20)`.
- `p_final = market_price + alpha * (p_smoothed - market_price)`.
- O grid walk-forward testa `alpha`, mínimo de amostras, cap de probabilidade e
  `min_edge_net`.
- Um segmento só fica elegível se tiver amostra mínima, Brier melhor que o
  mercado e PnL histórico positivo.

Quando uma variante v2 passa todos os gates, seus segmentos são persistidos em
`strategy_calibration_segments`; sinais paper gerados com a política reparada
registram `signal_strategy_audit` com probabilidade bruta, probabilidade
calibrada, política, segmento e amostras.

A versão de reparo v3 é mais seletiva e conservadora. Ela usa a mesma fórmula
market-aware da v2, mas permite apenas segmentos específicos
`city + bucket_kind + model_prob_bucket + price_bucket + hours_to_close_bucket`.
Fallbacks globais, por cidade ou por probabilidade continuam úteis para
smoothing, mas não podem autorizar sinal. A v3 também troca o bloqueio absoluto
de preços `0.00-0.05` por uma exceção controlada: o trade só passa se o
segmento específico for elegível, tiver Brier positivo, PnL positivo e
`observed_rate > cost_per_share(price)`. PnL positivo sem Brier positivo não
aprova a estratégia, porque isso indicaria lucro histórico sem evidência de que
o modelo bate a probabilidade implícita do mercado.

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
  proxy retrospectivo, mas não reconstrói book depth nem melhor ask. Para manter
  a validação performática com centenas de milhares de trades, o backtest usa o
  último trade por `market_id` em buckets de 60 minutos
  (`price_sampling = last_trade_per_market_per_60m_bucket`) e registra pontos
  brutos vs amostrados. O resultado deve marcar
  `execution_proxy = historical_last_trade_no_book_depth` quando usar trades, ou
  `polymarket_prices_history_last_price_no_book_depth` quando usar apenas
  `prices-history`, sempre com calibração walk-forward e sem observação futura.

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

Quando a validação histórica reprova com amostra suficiente, o próximo artefato
obrigatório é `historical_diagnostics_runs`. Esse relatório não muda a
estratégia nem aprova live: ele explica a reprovação por cidade, tipo de bucket,
faixa de preço, faixa de probabilidade, faixa de edge e horas até fechamento.
Ele também gera buckets de calibração para detectar superconfiança do modelo,
lista os piores segmentos e propõe ações como calibrar probabilidades, limitar
buckets superconfiantes e elevar thresholds segmentados antes de qualquer nova
rodada de validação.

Depois do diagnóstico, o artefato obrigatório de correção é
`strategy_repair_runs`. Ele compara baseline, calibração com cap, filtros
segmentados e variantes `repair_v2`/`repair_v3`/`repair_v4`, sempre em walk-forward e usando
`last_trade_per_market_per_60m_bucket`. O relatório persiste variante vencedora,
gates, segmentos bloqueados, bootstrap, Brier, PnL e ROI. A estratégia só pode
sair de `NEEDS_MODEL_REPAIR`/`NO_HISTORICAL_EDGE` se a melhor variante passar Brier, PnL histórico,
amostra mínima, concentração, bootstrap e qualidade das cidades.

Quando a V4 termina em `NO_HISTORICAL_EDGE`, o próximo artefato não é uma V5
automática: é `strategy_hypothesis_audit_runs`. Essa auditoria verifica se a
falha vem de dados ou hipótese, separando timing inválido, inconsistência de
bucket/resolução, superconfiança do modelo e falta de recorrência dos segmentos
entre treino e folds fora da amostra. Se houver problema de timing ou
mapeamento, o candidato histórico/backtest deve ser corrigido antes de qualquer
novo repair. Se os dados estiverem limpos e não houver recorrência OOS, a
conclusão é `NO_STABLE_HISTORICAL_EDGE`, mantendo paper forward e live
bloqueados.

Se a auditoria encontrar recorrência OOS, mas nenhum candidato acionável, a
próxima etapa permitida é diagnóstica: `strategy_experiment_runs`. Esses
experimentos flexibilizam a pesquisa, não os gates de live. O experimento
`flexible_validation_v1` testa segmentos moderados de probabilidade, preço e
horas até fechamento, bloqueia a combinação superconfiante
`above + raw_prob 0.9-1.0 + price 0.95-1.00`, e grava métricas de modelo
separadas de métricas de trade proxy. Seus status são apenas de pesquisa:
`REJECTED`, `VALIDATION_CANDIDATE`, `READY_FOR_SHADOW_PAPER` ou
`NO_STABLE_EDGE`. Mesmo `READY_FOR_SHADOW_PAPER` não aprova sinais reais,
ordens paper, ordens live ou micro-capital.

O shadow paper é uma trilha separada para decisões hipotéticas. Quando ativado
em fase futura, grava `strategy_shadow_decisions` com probabilidade bruta,
probabilidade calibrada, preço, edge, motivo e `would_trade`, sem criar
`signals`, `paper_orders` ou `paper_fills`. Ele serve para aumentar amostra
forward e comparar comportamento com o histórico antes de desenhar uma V5.

Se `flexible_validation_v1` também retornar `NO_STABLE_EDGE`, a próxima etapa é
`strategy_discovery_runs`. O discovery amplia o universo apenas para pesquisa.
Na V1 ele usa cidades ativas, sem `needs_review`, com amostra mínima de
calibração. Na V2 (`--universe poc --discovery-version v2
--include-research-only`) ele pode incluir cidades `needs_review` como
`research_only`, mas essas cidades nunca ficam live-eligible por causa do
discovery. A auditoria `city_research_audit_runs` classifica cada cidade como
`live_eligible`, `research_only` ou `excluded`, sem alterar o cadastro canônico.
Quando uma cidade nova ainda não tem histórico climático ou metadados
suficientes, o funil usa `city_onboarding_runs`: esse relatório valida estação,
coordenadas, unidade, timezone, rounding, fonte de resolução, forecast/observed,
mercados resolvidos e histórico de trades/preços. Onboarding é research-only e
não cria `signals`, `paper_orders` nem `paper_fills`.
Para cidades com defaults conhecidos, como `nyc` e `shanghai`, o comando pode
ser rodado com `--repair-metadata` para preencher estação/coordenadas/fonte sem
liberar live: `needs_review` permanece verdadeiro até revisão manual.

Quando uma POC depende de cidade `research_only`, a troca de cidade passa por
`city_edge_ranking_runs`. Esse ranking é diagnóstico e compara apenas cidades
ativas com `needs_review=false` para a trilha live, mantendo `needs_review=true`
em um bloco separado de pesquisa. Para cada cidade ele mede cobertura climática,
mercados resolvidos, histórico de trades/preços, folds OOS, Brier delta, PnL
proxy, bootstrap e concentração. Uma cidade só entra no Discovery direcionado
(`--universe ranked-live --cities ...`) quando tem dados suficientes e edge OOS
positivo; o ranking não cria `signals`, `paper_orders` ou `paper_fills` e nunca
libera live diretamente.

Se o ranking das cidades `live_eligible` atuais não encontrar edge, a próxima
fase é expandir o universo com `weather_city_discovery_runs`. Essa etapa lê
eventos weather da Polymarket, extrai metadados de resolução a partir da
descrição e cadastra cidades novas sempre com `needs_review=true`. A promoção
para pesquisa acionável depende de `city_resolution_promotion_audit_runs`, que
reconstrói vencedores a partir de `DailyObservedMax` e compara com
`Market.winner`. Mesmo uma cidade promovível só libera shadow paper, nunca live
direto.

Quando a promoção falha porque `DailyObservedMax(source="era5")` diverge de
`Market.winner`, o próximo passo é preencher `source="resolution"` com uma fonte
oficial ou reconstruível. O comando `app.collectors.resolution_backfill` importa
máximas diárias de resolução, primeiro tentando Wunderground quando a página for
parseável e, obrigatoriamente, aceitando fallback CSV local com
`city_slug,target_date,station_code,tmax,unit,source_url`. O valor é salvo em
Celsius em `DailyObservedMax`, mas a auditoria converte para a unidade do bucket
detectada no label do mercado. `resolution` tem prioridade sobre `era5` e
`metar`; `era5` continua útil como diagnóstico, mas não basta para promover uma
cidade `research_only` para candidata de shadow/V5.

NYC esta em quarentena operacional ate nova revisao de resolucao. O achado
diagnostico em NYC nao pode entrar em `repair_v5`, shadow paper, paper execution
ou live-readiness porque a resolucao oficial ainda nao foi verificada: a
auditoria com ERA5 mostrou mismatch de 22,88% e o Wunderground automatico foi
rejeitado como serie constante suspeita. A cidade continua disponivel apenas em
relatorios `research_only_diagnostic`, preservando aprendizado sem autorizar
capital.

O `strategy_discovery` V3 (`--universe expanded-poc --discovery-version v3`)
flexibiliza a pesquisa com famílias adicionais como `inverse_model_value`,
`market_follow`, `time_decay_specialist`, `resolution_source_specialist` e
`buy_no_value`. BUY NO é permitido apenas em pesquisa/shadow paper até existir
uma `repair_v5` auditada. Discovery V3/V4 pode sugerir candidato, mas live continua
exigindo `strategy_repair_runs.status = PROMISING` e measurement compatível.

O discovery testa famílias explicáveis como `model_value`, `market_anchor`,
`tail_value`, `bucket_specialist`, `avoid_overconfidence`,
`tail_surprise_city`, `bucket_mispricing`, `time_window_specialist`,
`market_implied_baseline` e `no_trade_filter`. A seleção da família ocorre só
em treino rolling-origin e a avaliação final usa folds OOS. A V2 pode retornar
`DISCOVERY_CANDIDATE` com gates mais flexíveis de POC, mas
`READY_FOR_SHADOW_PAPER` ainda exige Brier positivo, PnL positivo, amostra,
folds e concentração aceitáveis. Se o resultado depender apenas de cidades
`research_only`, o status máximo é `DISCOVERY_CANDIDATE`. O discovery continua
diagnóstico: não muda `strategy_repair_runs`, não cria sinais e nunca libera
live readiness.

Quando o discovery retorna `DISCOVERY_CANDIDATE`, o próximo artefato obrigatório
é `discovery_candidate_audit_runs`. Essa auditoria decompõe PnL/Brier por
cidade, família, fold, segmento, tipo de bucket, preço e horas até fechamento;
também reconstrói o bucket vencedor a partir de `DailyObservedMax` para cidades
`research_only` negociadas pelo candidato. Um achado concentrado em cidade
`research_only`, como NYC, não pode virar V5, shadow paper ou live se houver
divergência entre bucket reconstruído e `Market.winner`. A auditoria só retorna
`READY_FOR_REPAIR_V5` quando Brier, PnL, folds, bootstrap, timing e resolução
auditada passam juntos.

Apos a quarentena de NYC, o ranking operacional considera apenas cidades fora de
quarentena. Na rodada atual, as candidatas operacionais sao `seoul`, `tokyo` e
`hong-kong`; Discovery V3 direcionado nelas retornou `NO_EDGE_FOUND`. Portanto,
o caminho mais rapido para live deixa de ser insistir em NYC e passa a ser
promover outra cidade com resolucao reconstruivel ou revisar a tese/features
meteorologicas antes de tentar uma nova V5.

Para acelerar esse caminho sem reduzir os gates de live, o sprint operacional
prioriza `london`, `dallas` e `atlanta` como novas candidatas. Essas cidades so
podem sair de `needs_review` por meio de `city_promotion_apply`, que exige uma
auditoria de resolucao anterior com `promotion_status =
LIVE_ELIGIBLE_CANDIDATE`, `mismatch_rate <= 0.02`, zero observacoes faltantes,
`resolution_source_used = resolution`, pontos de resolucao oficiais e historico
de mercado validado. A promocao apenas amplia o universo operacional para
ranking, Discovery V3 e possivel V5; ela nao aprova shadow paper, paper
execution ou live trading.

Na V4, a validação principal usa `rolling-origin`: seleciona uma política em
uma janela de treino válida e avalia a mesma política, sem retreinar parâmetros,
em folds fora da amostra. O holdout fixo continua disponível para diagnóstico,
mas não é mais o caminho padrão quando o histórico de mercado é curto e
irregular. Uma variante V4 com zero trades no treino de seleção não pode ser
promovida a política vencedora; nesses casos o relatório deve permanecer como
`NO_HISTORICAL_EDGE` ou `INSUFFICIENT_HISTORY`, com o motivo em `summary_json`.

A mesma função de decisão é usada no backtest e no runtime.
`price_bucket = 0.00-0.05` fica em modo diagnóstico: esses pontos podem ser
contados para auditoria, mas não aprovam a política principal nem autorizam
paper forward/live. Segmentos V4 precisam ser específicos, ter amostra mínima,
Brier positivo, PnL positivo e taxa observada acima do custo médio por share.
PnL positivo sem Brier positivo não aprova a estratégia.

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
- Se a validação histórica falhar com amostra suficiente,
  `strategy_repair_runs.status` deve sair de `NEEDS_MODEL_REPAIR` por meio de
  calibração/thresholds testados novamente em walk-forward.
- Quando existir `strategy_repair_runs`, o gate histórico principal para live
  passa a ser `strategy_repair_runs.status = PROMISING`; `historical_validation`
  só serve como fallback se ainda não houver repair run.
- `strategy_experiment_runs` e `strategy_shadow_decisions` são diagnósticos e
  nunca liberam live readiness diretamente.
- `strategy_discovery_runs` também é diagnóstico; ele pode indicar uma família
  candidata a shadow paper, mas não autoriza paper execution nem live.
- `city_research_audit_runs` é diagnóstico; uma cidade `research_only` precisa
  ser revisada separadamente antes de entrar em qualquer universo live.
- `city_onboarding_runs` é diagnóstico; ele prepara cidades para POC histórica,
  mas não autoriza shadow paper, paper execution ou live.
- `ensemble_members > 0` para eventos ativos das cidades foco.
- Pelo menos 30 dias de coleta forward ou 50 fills paper resolvidos antes de
  aprovar execução.
- Brier do modelo menor que Brier do mercado em `max_edge`.
- PnL líquido positivo após fee no replay forward.
- PnL líquido positivo após fee na execução paper.
- `measurement_runs.status = READY_FOR_LIVE_REVIEW`.
- O measurement aprovado deve usar a mesma `policy_name` reparada vencedora
  (`repair_v2`, `repair_v3` ou `repair_v4`) do strategy repair.
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

## Discovery V4 E Shadow Fast Lane

Discovery V4 amplia a pesquisa sem ampliar live readiness. Ela testa BUY YES e
BUY NO apenas em historico/shadow, com familias explicaveis como
`buy_no_value`, `market_extreme_fade`, `city_season_specialist`,
`time_to_close_specialist`, `forecast_error_regime` e `dallas_fast_lane`.
Resultados sao separados por `side`, familia, segmento, cidade e fold. A
`dallas_fast_lane` pode virar candidata diagnostica single-city, mas so conta
como pronta para shadow se tiver pelo menos 100 trades OOS e todos os demais
gates historicos passarem.

O shadow fast lane grava somente `strategy_shadow_decisions`; ele nao cria
`signals`, `paper_orders` ou `paper_fills`. Discovery V4 e shadow continuam
diagnosticos: podem apontar uma politica candidata para `repair_v5`, mas nao
autorizam paper execution nem live trading. Live segue exigindo
`strategy_repair_runs.status = PROMISING`, `measurement_runs.status =
READY_FOR_LIVE_REVIEW` e a mesma `policy_name`.

## Feature Discovery V5

Feature Discovery V5 e a trilha seguinte quando Discovery V4 nao encontra edge
com os segmentos basicos. Ela continua paper-only e diagnostica, persistindo
`feature_discovery_runs` sem criar `signals`, `paper_orders` ou `paper_fills`.
A busca combina features meteorologicas e de mercado derivadas sem lookahead:
distancia modelo/preco, dispersao/proxy de ensemble, revisao de probabilidade,
lead time, mes, tipo de bucket, bucket de preco, momentum historico de preco e
regime de erro por cidade.

As familias V5 sao explicaveis: `ensemble_confidence_value`,
`forecast_revision_value`, `market_momentum_fade`,
`threshold_distance_specialist`, `city_error_regime_specialist` e
`buy_no_feature_value`. BUY NO continua permitido apenas em pesquisa/shadow. Um
status `READY_FOR_REPAIR_V5` autoriza apenas desenhar uma `repair_v5` com a
mesma familia, side, cidades e segmentos; nao autoriza live trading.

Antes de criar qualquer `repair_v5` a partir de Feature Discovery, o bot deve
rodar `feature_candidate_audit_runs`. Essa auditoria reconstroi os folds OOS da
ultima `FeatureDiscoveryRun.status = FEATURE_CANDIDATE`, registra decision trace
por candidato, decompoe PnL/Brier por cidade/segmento/features e explica casos
em que o PnL proxy e positivo mas o Brier e negativo. Apenas um subset com Brier
positivo, PnL positivo apos fee, 50+ trades, 3+ folds, concentracao aceitavel e
sem cidade em quarentena pode seguir para um plano de `repair_v5_feature_subset`.
Sem esse subset, a tese volta para revisao de features/modelo climatico.

## High-Reward City Hunt

Quando o objetivo for alto risco/alta recompensa, o bot nao busca winrate alto.
Ele roda `high_reward_city_hunt_runs` para encontrar pelo menos tres cidades
operacionais em que poucos acertos historicos pagaram muitas perdas. Essa trilha
usa BUY YES e BUY NO apenas em pesquisa/shadow e prioriza cidades com alta
volatilidade de temperatura, `tail_miss_rate_3c/5c`, surpresa recente da
previsao e divergencia entre mercado e modelo.

Uma cidade so passa como candidata se tiver resolucao auditavel, `needs_review =
false`, historico de mercado validado, 15+ trades OOS, PnL e ROI liquidos
positivos apos fee e payoff medio vencedor pelo menos 3x maior que a perda
media. `READY_FOR_SHADOW_FAST_LANE` exige tres cidades diferentes aprovadas e
autoriza apenas shadow paper diagnostico; nao cria `signals`, `paper_orders` ou
`paper_fills` e nao libera live trading.

Quando `high_reward_city_hunt_runs.status = READY_FOR_SHADOW_FAST_LANE`, o bot
pode gerar shadow decisions com `policy_name = high_reward_shadow_v1`. A fast
lane prioriza as tres cidades/sides aprovadas pelo hunt (`seattle` YES, `seoul`
NO e `toronto` NO no run inicial), mas pode avaliar cidades de `approved_all`
como fallback shadow se uma cidade primaria nao gerar decisoes ativas no recorte
forward. Isso preserva o objetivo de pelo menos tres cidades ativas sem promover
research-only, sem criar `signals`, `paper_orders` ou `paper_fills` e sem liberar
live trading. O shadow serve para acumular decisoes forward antes de qualquer
`repair_v5_high_reward`.

Quando o shadow high-reward tiver decisoes resolvidas suficientes, `analysis.high_reward_repair`
pode criar `strategy_repair_runs` com `policy_name = repair_v5_high_reward_v1`.
Essa V5 filtra a fast lane e promove somente cidades com PnL liquido positivo,
payoff medio vencedor pelo menos 3x maior que a perda media, bootstrap nao
claramente negativo e zero bloqueios operacionais. No run atual, a politica
promissora ficou restrita a `atlanta` YES, `seattle` YES e `toronto` NO, com
winrate baixo aceito pela assimetria de payoff.

Quando `STRATEGY_POLICY_MODE=repair_v5`, o scanner aplica essa politica
high-reward em runtime paper: gera apenas perfil `max_edge`, usa `yes_token_id`
para cidades YES, usa `no_token_id` para cidades NO e registra
`SignalStrategyAudit.policy_name = repair_v5_high_reward_v1`. Para BUY NO, a
decisao usa probabilidade `1 - P(YES)` e preco proxy `1 - best_bid(YES)`, mas a
PaperEngine preenche contra o book real do token NO. A PaperEngine tambem
liquida tokens NO de forma invertida ao `Market.winner`. Essa V5 ainda nao
autoriza live: o proximo gate e obter `measurement_runs.status =
READY_FOR_LIVE_REVIEW` para a mesma `policy_name`, com fee, slippage, ledger e
settlement reconciliados.

Operacionalmente, a fast lane deve rodar com o universo fixo `atlanta`,
`seattle` e `toronto`. O comando manual `run_once --high-reward-fast-lane` e o
scheduler quando `STRATEGY_POLICY_MODE=repair_v5` aplicam a mesma configuracao:
`mode=paper`, `live_trading_enabled=false`, cidades `atlanta,seattle,toronto` e
politica `repair_v5`. Isso evita que a coleta automatica volte ao universo
historico padrao enquanto acumulamos os 30 dias forward ou 50 fills resolvidos.

O `measurement` da fast lane escopa os gates de readiness aos fills ligados a
`SignalStrategyAudit.policy_name = repair_v5_high_reward_v1`. Fills legados de
politicas antigas ou sem audit continuam aparecendo em
`all_paper_fill_policy_counts`, mas nao bloqueiam a validacao da politica
reparada atual. O ledger global ainda precisa reconciliar contra todos os fills
persistidos.

O mesmo measurement tambem exige `slippage_reconciliation`: toda ordem paper
FILLED/PARTIAL da politica reparada precisa ter `slippage` persistido. A media
fica em `metrics_json.avg_slippage`; ordens legadas continuam visiveis em
contadores globais, mas o gate da V5 avalia apenas a politica aprovada.

Para evitar drift operacional, a fast lane pode ser acionada pela raiz do repo
com `.\scripts\fast-lane-paper.ps1 -Action status`, `-Action run-once` ou
`-Action scheduler`. O script sempre usa cache local `.tmp\uv-cache`, força
`MODE=paper`, `LIVE_TRADING_ENABLED=false`, `COLLECTORS_ENABLED=true` e
`STRATEGY_POLICY_MODE=repair_v5`. Ele nao configura credenciais, nao habilita
live trading e nao cria ordens reais.

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
