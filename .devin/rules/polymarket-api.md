---
trigger: glob
globs: backend/**/*.py
description: Padrões obrigatórios ao usar as APIs da Polymarket
---

# Padrões de Uso das APIs Polymarket

## SDK e clientes
- Usar o SDK oficial Python unificado **`polymarket-client`** (`pip install polymarket-client`, import `polymarket`): `AsyncPublicClient` para dados públicos e `AsyncSecureClient` para trading (somente Fase 5).
- Não reimplementar assinatura EIP-712/HMAC manualmente; o SDK cuida de L1/L2.
- Fechar clientes com `async with` (ou `await client.close()`).
- Consultar a skill `polymarket-api` para endpoints, métodos e payloads detalhados.

## Identificadores — não confundir
- `event` (Gamma): agrupa mercados de um dia/cidade; mercados de clima são eventos **negRisk** com ~11 buckets.
- `market.conditionId` (0x…): identifica o mercado binário no CLOB.
- `clobTokenIds` `[YES, NO]`: token do outcome — é o que o orderbook, preços e WSS usam (`asset_id`).
- `questionID` / `negRiskMarketID`: resolução UMA/negRisk — não usar para trading.

## Dados e precisão
- Preços são frações de pUSD em `0.00–1.00` = probabilidade implícita; usar `Decimal`, nunca float.
- Respeitar `orderPriceMinTickSize` (0.001 ou 0.01 — muda dinamicamente perto de 0.04/0.96) e `orderMinSize` (5 shares nos mercados de clima).
- Campos JSON da Gamma como `outcomes`, `outcomePrices`, `clobTokenIds` vêm como **string JSON** — fazer parse explícito.

## Rate limits e tempo real
- Preferir WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) a polling para preços/orderbook.
- Polling REST respeita os limites documentados (Gamma `/markets` 300 req/10s, `/events` 500 req/10s; CLOB `/book` e `/price` 1500 req/10s, `/prices-history` 1000 req/10s). Implementar backoff exponencial; tratar `RateLimitError` do SDK.
- Cloudflare faz throttling (atrasa, não rejeita) — timeouts generosos em clients HTTP.

## Descoberta de mercados de clima
- Filtrar por tag na Gamma: `weather` (id 84), `daily-temperature` (id 103040), `highest-temperature` (id 104596); ou por série diária (`{cidade}-daily-weather`).
- Slug padrão dos eventos: `highest-temperature-in-{cidade}-on-{mês}-{dia}-{ano}`.
- A fonte de resolução está na `description` do mercado e varia por cidade (ex.: Hong Kong Observatory; Wunderground/estação RKSI para Seul) — extrair e armazenar junto ao mercado.
