---
name: paper-trading
description: Especificação do motor de paper trading do Weather Bot — simulação de fills contra o orderbook real da Polymarket, contabilidade de posições, fees, PnL e métricas. Usar ao implementar ou alterar o módulo execution/.
---

# Paper Trading — Especificação do Simulador

Objetivo: validar a estratégia com dados reais de mercado sem capital, com contabilidade idêntica à do modo live (mesma interface), para que a troca paper→live seja apenas de engine.

## 1. Princípios

- **Mesma interface do engine live**: `ExecutionEngine` (protocolo) com `submit_order`, `cancel_order`, `get_open_orders`, `get_positions`. `PaperEngine` e (futuro) `LiveEngine` implementam o mesmo contrato.
- **Fills contra o orderbook real** (snapshot REST `/book` ou stream WSS `book`/`price_change`) no momento da ordem — nunca contra preços inventados.
- **Dinheiro = `Decimal`** com quantização explícita; saldo inicial virtual configurável (ex.: 1000 pUSD).
- Tudo persistido em SQLite com timestamps UTC para auditoria e replays.

## 2. Modelo de simulação de fill

### Ordem a mercado (taker)
1. Obter o book do `token_id`; lado BUY consome `asks` do menor preço para o maior; SELL consome `bids` do maior para o menor.
2. Cruzar níveis até preencher `size` (shares) ou esgotar `max_spend` (pUSD). Registrar um fill por nível consumido (preço médio ponderado = resultado do conjunto de fills).
3. Tipos: `FAK` (fill-and-kill: preenche o possível, cancela resto) e `FOK` (all-or-nothing: simula tudo ou rejeita) — mesmos tipos do CLOB real.
4. Liquidez insuficiente: FAK preenche parcial; FOK rejeita com `INSUFFICIENT_LIQUIDITY`.

### Ordem limite (maker/taker)
1. Se o preço cruza o book (BUY ≥ best ask), executa imediatamente como taker (regras acima) até o limite do preço.
2. Resto vira ordem aberta simulada (resting). Um watcher de mercado (WSS `last_trade_price` / `price_change`) preenche quando o mercado negocia através do preço da ordem — aproximação conservadora: fill só quando `last_trade_price` cruza o preço (não assumir prioridade de fila melhor que a real).
3. Suportar `expiration` (GTD) e cancelamento manual.

### Validações pré-trade (espelham o CLOB + módulo de risco)
- `size ≥ orderMinSize` (5 nos mercados de clima); preço alinhado ao `orderPriceMinTickSize` (0.001/0.01) e em (0, 1).
- Saldo virtual suficiente (BUY: `price × size`; SELL: shares em carteira — sem short nu no paper).
- Limites de risco (rule `trading-safety.md`): stake máx./ordem, exposição máx./mercado, perda diária máx. — rejeitar com motivo explícito.

## 3. Fees — obrigatório

- Mercados de clima: `feeSchedule {rate: 0.05, takerOnly: true}` → fill taker paga **5%**; fills maker pagam 0.
- Convenção Polymarket atual: para preço `p` e `size` shares,
  `fee = rate × size × p × (1 − p)`; debitar do caixa no fill. Guardar `fee_paid` por fill.
- `rebateRate: 0.25` existe para makers (programa de rebates) — **não** creditar no paper (conservador).
- EV de um sinal deve já descontar a fee esperada; o engine recalcula a fee real no fill.

## 4. Contabilidade e PnL

- **Posição** por `token_id`: `qty`, `avg_cost` (custo médio incluindo fees de entrada).
- **PnL realizado**: em SELL/resolução, `(preço_saída − avg_cost) × qty_vendida − fees_de_saída`.
- **PnL não-realizado**: `(midpoint_atual − avg_cost) × qty` (marcar a mercado pelo midpoint; usar best_bid para visão conservadora de liquidação).
- **Resolução**: quando o mercado resolve (WSS `market_resolved` ou Gamma `closed`), tokens vencedores viram 1.00 e perdedores 0.00; gerar fill sintético de settlement e mover PnL para realizado.
- **Caixa virtual**: `cash += / −=` a cada fill; equity = cash + Σ(posições × mark). Snapshot periódico de equity para curva de PnL.

## 5. Esquema SQLite (mínimo)

```
orders(id, ts_utc, market_condition_id, token_id, side, type, price, size,
       filled_size, status[OPEN|FILLED|PARTIAL|CANCELED|REJECTED|EXPIRED],
       reject_reason, expiration_ts, signal_id?)
fills(id, order_id, ts_utc, price, size, fee_paid, liquidity[MAKER|TAKER])
positions(token_id, market_condition_id, qty, avg_cost, updated_at)
equity_snapshots(ts_utc, cash, equity, realized_pnl, unrealized_pnl)
book_snapshots(ts_utc, token_id, bids_json, asks_json)   # auditoria/replay dos fills
```

## 6. Métricas de avaliação

- **Trading**: PnL total/diário, win rate, profit factor, máx. drawdown, exposição média, fees pagas.
- **Modelo vs mercado**: **Brier score** das probabilidades do modelo vs resolução real, comparado ao Brier do preço de mercado no mesmo instante (o modelo só tem valor se bater o mercado após fees).
- **Execução**: slippage médio (preço esperado vs preço médio dos fills), taxa de fills parciais/rejeições.
- Janela mínima de validação antes de considerar live: a definir com o usuário (sugestão: ≥ 30 dias e ≥ 50 trades simulados).

## 7. Armadilhas

- Não usar `outcomePrices` da Gamma como preço executável (é indicativo/defasado) — fill só contra book CLOB.
- Book pode estar vazio/rarefeito em buckets extremos (preços 0.001) — checar profundidade antes de simular fill; slippage explode em books finos.
- Tick size muda perto dos extremos (0.04/0.96) — revalidar preço de ordens abertas ao receber `tick_size_change`.
- Mercados de clima fecham 12:00 UTC — não simular ordens após `endDate`; cancelar ordens abertas no fechamento.
- Replays/backtests com `book_snapshots` têm viés de sobrevivência da liquidez (book visto ≠ book se nossa ordem existisse); tratar resultados como otimistas.
