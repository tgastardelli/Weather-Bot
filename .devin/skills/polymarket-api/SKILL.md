---
name: polymarket-api
description: Referência técnica completa das APIs da Polymarket (Gamma, CLOB, Data API, WebSocket) e do SDK Python oficial, incluindo a estrutura e resolução dos mercados de clima. Usar ao implementar qualquer integração com a Polymarket.
---

# Polymarket API — Referência Técnica

Fontes: docs.polymarket.com (verificado em 09–10/06/2026) + sondagem real da Gamma API.

## 1. Visão geral das APIs

| API | Base URL | Auth | Uso |
|---|---|---|---|
| Gamma | `https://gamma-api.polymarket.com` | Não | Eventos, mercados, tags, séries, busca |
| CLOB | `https://clob.polymarket.com` | Pública p/ dados; L1+L2 p/ ordens | Orderbook, preços, histórico, ordens |
| Data | `https://data-api.polymarket.com` | Não | Posições, trades públicos, open interest |
| WSS | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Não (canal market) | Tempo real: book, preços, trades |
| Geoblock | `GET https://polymarket.com/api/geoblock` | Não | Elegibilidade geográfica (checar antes do live) |

- Chain: **Polygon (137)**; colateral: **pUSD**; preços = frações de pUSD entre 0 e 1.
- Specs OpenAPI: `docs.polymarket.com/api-spec/gamma-openapi.yaml`, `clob-openapi.yaml`; AsyncAPI: `docs.polymarket.com/asyncapi.json`.
- Docs em markdown puro: adicionar `.md` à URL da página (ex.: `docs.polymarket.com/api-reference/rate-limits.md`); índice em `docs.polymarket.com/llms.txt`.

## 2. SDK Python oficial — `polymarket-client` (beta)

```bash
pip install polymarket-client   # import polymarket  (changelog atual: 0.1.0b4)
```

Unifica Gamma + CLOB + Data + streams. Alternativa legada focada só em CLOB: `py-clob-client-v2` (`from py_clob_client_v2 import ClobClient`). **Padrão do projeto: `polymarket-client`.**

### Tipos
- IDs como `NewType`: `MarketId`, `ConditionId`, `TokenId`, `EventId`, `EvmAddress`.
- Preço/tamanho: `decimal.Decimal`; datas: `datetime` (tz-aware).
- Exceções: `PolymarketError` (base), `RateLimitError`, `UserInputError` — capturar específicas primeiro.

### Cliente público (leitura — suficiente para paper trading)

```python
import asyncio
from polymarket import AsyncPublicClient

async def main() -> None:
    async with AsyncPublicClient() as client:
        # Descoberta paginada
        markets = client.list_markets(closed=False, page_size=10)
        first_page = await markets.first_page()          # .items, .next_cursor
        async for page in markets.from_cursor(first_page.next_cursor):
            ...
        async for market in markets.items():             # itera direto nos itens
            ...

        # Por slug/URL/id
        market = await client.get_market(slug="highest-temperature-in-seoul-on-june-10-2026-22c")
        event = await client.get_event(slug="highest-temperature-in-seoul-on-june-10-2026")

        # Dados de mercado (token_id = outcome YES/NO)
        yes_token_id = market.outcomes.yes.token_id
        book = await client.get_order_book(token_id=yes_token_id)
        buy_price = await client.get_price(token_id=yes_token_id, side="BUY")
        midpoint = await client.get_midpoint(token_id=yes_token_id)
        spread = await client.get_spread(token_id=yes_token_id)
        last = await client.get_last_trade_price(token_id=yes_token_id)
        history = await client.get_price_history(token_id=yes_token_id, interval="1d")

        # Batch
        from polymarket import PriceRequest
        prices = await client.get_prices(requests=[PriceRequest(token_id=yes_token_id, side="BUY")])
        midpoints = await client.get_midpoints(token_ids=[yes_token_id])

        # Tags e busca
        tag = await client.get_tag(slug="weather")
        results = client.search(q="highest temperature", page_size=10)

asyncio.run(main())
```

Modelo `Market` (campos principais): `id`, `slug`, `condition_id`, `question`, `description`, `state` (start/end_date), `outcomes` (label, token_id, price), `metrics`, `prices`, `trading`, `resolution`, `tags`.

### Streams (tempo real via SDK)

```python
from polymarket import AsyncPublicClient
from polymarket.streams import MarketSpec

async with AsyncPublicClient() as client:
    stream = await client.subscribe([MarketSpec(token_ids=[yes_token_id])])
    async with stream:
        async for event in stream:
            # MarketBookEvent | MarketPriceChangeEvent | MarketLastTradePriceEvent
            # | MarketTickSizeChangeEvent | MarketBestBidAskEvent | NewMarketEvent
            # | MarketResolvedEvent
            ...
```

### Cliente autenticado (SOMENTE Fase 5 — live)

```python
import os
from polymarket import AsyncSecureClient

async with await AsyncSecureClient.create(
    private_key=os.environ["POLYMARKET_PRIVATE_KEY"],   # somente .env
    wallet=os.environ.get("POLYMARKET_WALLET_ADDRESS"), # omitir => Deposit Wallet padrão
) as sc:
    await sc.setup_trading_approvals()  # on-chain, idempotente — EXIGE aprovação do usuário

    resp = await sc.place_limit_order(token_id=yes_token_id, side="BUY", price="0.52", size="10")
    # expiração: expiration=int(time.time())+3600
    resp2 = await sc.place_market_order(token_id=yes_token_id, side="BUY",
                                        amount="10", max_spend="11", order_type="FAK")  # ou FOK
    if resp.ok:
        order_id = resp.order_id
    else:
        resp.code, resp.message  # OrderResponseErrorCode

    # criar-assinar-depois-postar (suporta lote)
    order = await sc.create_limit_order(token_id=yes_token_id, side="BUY", price="0.52", size="10")
    responses = await sc.post_orders([order])

    # gestão
    o = await sc.get_order(order_id=order_id)
    async for page in sc.list_open_orders(market=market.condition_id): ...
    await sc.cancel_order(order_id=order_id)          # .canceled: tuple[str, ...]
    await sc.cancel_market_orders(token_id=yes_token_id)

    # posições on-chain: split_position / merge_positions / redeem_positions
    # user stream: await sc.subscribe(UserSpec()) -> UserOrderEvent | UserTradeEvent
```

## 3. Autenticação (modelo de 2 níveis)

- **L1 (chave privada, EIP-712)**: criar/derivar API creds e assinar ordens localmente.
- **L2 (API creds + HMAC)**: postar/cancelar ordens, consultar ordens/saldos. Headers: `POLY_ADDRESS`, `POLY_SIGNATURE` (HMAC do `secret`), `POLY_TIMESTAMP`, `POLY_API_KEY`, `POLY_PASSPHRASE`.
- Derivação: `client.create_or_derive_api_key()` → `{apiKey, secret, passphrase}` (py-clob-client-v2) — o `polymarket-client` faz isso internamente.
- **Signature types**: `0` EOA · `1` Poly Proxy (email/magic) · `2` Poly Safe (browser wallet) · `3` POLY_1271 Deposit Wallet (recomendado p/ novos usuários).
- Erros comuns: `INVALID_SIGNATURE` (chave/format errado), `NONCE_ALREADY_USED` (usar `deriveApiKey` com o mesmo nonce), funder address inválido (ver polymarket.com/settings).

## 4. REST direto (sem SDK) — endpoints essenciais

### Gamma
```bash
# Eventos de clima ativos (tag weather = id 84)
GET https://gamma-api.polymarket.com/events?tag_id=84&active=true&closed=false&limit=100
# Params úteis: order=volume_24hr|liquidity|end_date, ascending, offset,
#               related_tags=true, exclude_tag_id
GET https://gamma-api.polymarket.com/markets?slug={slug}
GET https://gamma-api.polymarket.com/tags
GET https://gamma-api.polymarket.com/public-search?q=highest+temperature&limit_per_type=5
# Paginação keyset (list-markets): usar next_cursor -> after_cursor (offset é rejeitado)
```

### CLOB (dados públicos)
```bash
GET https://clob.polymarket.com/book?token_id={token_id}
GET https://clob.polymarket.com/price?token_id={token_id}&side=BUY
GET https://clob.polymarket.com/midpoint?token_id={token_id}
GET https://clob.polymarket.com/spread?token_id={token_id}
GET https://clob.polymarket.com/prices-history?market={token_id}&interval=1d
GET https://clob.polymarket.com/fee-rate?token_id={token_id}
GET https://clob.polymarket.com/tick-size?token_id={token_id}
```
- `last-trade-price` retorna default `"0.5"`/side vazio se nunca houve trade.
- Batch (`/books`, `/prices`, `/midpoints`): máx. 500 token_ids por chamada.
- Trading autenticado: `POST /order`, `POST /orders` (máx. 15), `DELETE /order|/orders|/cancel-all|/cancel-market-orders`; heartbeat: `POST` (se a sessão usa heartbeat e ele falha, TODAS as ordens abertas são canceladas).

## 5. WebSocket — canal market (raw)

```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```
Subscribe (token_ids = `asset_id`):
```json
{"assets_ids": ["<token_id_1>", "<token_id_2>"], "type": "market", "custom_feature_enabled": true}
```
`custom_feature_enabled: true` habilita `best_bid_ask`, `new_market`, `market_resolved`.

Mensagens (`event_type`):
- **`book`** — snapshot ao subscrever e a cada trade que altera o book: `{asset_id, market, bids:[{price,size}], asks:[...], timestamp, hash}`
- **`price_change`** — ordem nova/cancelada: `{market, price_changes:[{asset_id, price, size, side, best_bid, best_ask, hash}], timestamp}`; `size: "0"` = nível removido
- **`last_trade_price`** — trade: `{asset_id, market, price, side, size, fee_rate_bps, timestamp}`
- **`tick_size_change`** — tick muda quando preço > 0.96 ou < 0.04: `{old_tick_size, new_tick_size, ...}`
- **`best_bid_ask`** — `{best_bid, best_ask, spread, asset_id, market, timestamp}`
- Canal user (autenticado): ordens/trades próprios; via SDK = `UserSpec()`.

## 6. Rate limits (Cloudflare; throttling, não rejeição; janelas deslizantes)

| API | Endpoint | Limite |
|---|---|---|
| Geral | qualquer | 15.000 req/10s |
| Gamma | geral · `/events` · `/markets` · `/comments`,`/tags` · `/public-search` | 4.000 · 500 · 300 · 200 · 350 req/10s |
| Data | geral · `/trades` · `/positions` | 1.000 · 200 · 150 req/10s |
| CLOB | geral · `/book`,`/price`,`/midpoint` · `/books`,`/prices`,`/midpoints` · `/prices-history` · tick size | 9.000 · 1.500 · 500 · 1.000 · 200 req/10s |
| CLOB auth | API keys | 100 req/10s |
| Trading | `POST /order` burst/sustained | 5.000 req/10s / 120.000 req/10min |
| Trading | `POST /orders` · `DELETE /cancel-all` | 2.000 req/10s · 250 req/10s |

## 7. Mercados de clima — estrutura real (sondagem Gamma 10/06/2026)

### Organização
- **Séries diárias** por cidade: slug `{cidade}-daily-weather` (ex.: `seoul-daily-weather`, `hong-kong-daily-weather`), `recurrence: "daily"`.
- **Evento por dia/cidade**: slug `highest-temperature-in-{cidade}-on-{month}-{day}-{year}`, título "Highest temperature in {City} on {date}?", `negRisk: true` (buckets mutuamente exclusivos).
- **~11 mercados (buckets) por evento**: `groupItemTitle` = "23°C or below" | "24°C" | … | "33°C or higher"; `groupItemThreshold` ordena os buckets.
- Tags: `weather` (84), `daily-temperature` (103040), `highest-temperature` (104596), tag da cidade (ex.: `seoul` 102936, `hong-kong` 102923).

### Campos críticos por mercado (payload Gamma real)
```jsonc
{
  "conditionId": "0x3510…",            // id do mercado no CLOB
  "clobTokenIds": "[\"6417…\", \"5336…\"]", // STRING JSON [YES, NO]
  "outcomes": "[\"Yes\", \"No\"]",
  "outcomePrices": "[\"0.41\", \"0.59\"]",   // STRING JSON
  "orderPriceMinTickSize": 0.001,        // ou 0.01
  "orderMinSize": 5,
  "negRisk": true, "negRiskMarketID": "0x…", "questionID": "0x…",
  "feeType": "weather_fees",
  "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": true, "rebateRate": 0.25},
  "makerBaseFee": 1000, "takerBaseFee": 1000,
  "customLiveness": 1800,                // UMA: 30 min de liveness
  "umaBond": "500", "umaReward": "2",
  "endDate": "2026-06-10T12:00:00Z",     // fim de trading: meio-dia UTC
  "gameStartTime": "2026-06-09 15:00:00+00",
  "restricted": true,
  "eventMetadata": {"context_description": "…"}  // contexto narrativo gerado
}
```

### ⚠️ Fees de clima — crítico para a estratégia
`weather_fees`: **5% taker-only** (`rate: 0.05`, `takerOnly: true`, `rebateRate: 0.25`). Edge bruto < fee = trade perdedor. Todo cálculo de EV/PnL desconta a fee de taker; ordens maker não pagam.

### ⚠️ Resolução — varia por cidade (ler `description` sempre)
Exemplos reais:
- **Hong Kong**: Hong Kong Observatory, "Absolute Daily Max (deg. C)" do Daily Extract (weather.gov.hk/en/cis/climat.htm); precisão **1 casa decimal**; revisões posteriores **não** contam; só resolve após publicação do dado.
- **Seul**: Wunderground, histórico diário da estação **Incheon Intl Airport (RKSI)** (`resolutionSource`: wunderground.com/history/daily/kr/incheon/RKSI); precisão **graus inteiros**; revisões contam **até** o primeiro datapoint do dia seguinte.
- Implicação: mapear cidade → fonte/estação/precisão/política de revisão a partir da `description` e armazenar como metadados do mercado.

### Mecânica de resolução (UMA Optimistic Oracle)
1. Proposta com bond (~$750 pUSD; mercados de clima: `umaBond` 500) → 2. Janela de disputa (default 2h; clima usa `customLiveness` 1800s = 30min) → 3. Sem disputa = resolve; com disputa = nova proposta; 2ª disputa = voto DVM (~48h). Possível resultado 50/50 em casos raros. Vencedor redime $1.00/share; perdedor $0.00.

## 8. Geoblock
- `GET https://polymarket.com/api/geoblock` (domínio polymarket.com, não API) verifica elegibilidade do IP.
- Bloqueados (ordens): US, GB, FR, DE, IT, NL, BE, AU, RU, IR, … ; close-only: PL, SG, TH, TW; região: Ontário (CA). **Brasil não está na lista** (verificado 10/06/2026). Checar sempre antes de habilitar modo live.
- Servidores primários: `eu-west-2`.

## 9. Armadilhas conhecidas
- Campos Gamma `clobTokenIds`/`outcomes`/`outcomePrices` são strings JSON — parse explícito (`json.loads`).
- Tick size muda dinamicamente (0.01 → 0.001) quando preço sai de [0.04, 0.96] — escutar `tick_size_change`.
- `last_trade_price` REST tem default 0.5 quando não há trades — não confundir com preço real.
- Eventos de clima fecham trading às 12:00 UTC do dia seguinte ao evento, mas o `gameStartTime` indica o dia-alvo da medição.
- Heartbeat ativo + processo travado = cancelamento automático de todas as ordens (comportamento desejado).
- `restricted: true` nos eventos de clima refere-se a restrições de acesso regional do frontend, não impede leitura de dados.
