# Weather Bot

Bot em modo paper para mercados de clima da Polymarket. A fase atual coleta mercados, previsoes, observacoes e resolucoes; calibra probabilidades; gera sinais; simula fills paper contra books capturados; persiste evidence/measurement reports; e expoe um dashboard React. Nao ha envio de ordens reais, scraping de fontes oficiais de resolucao ou modo live.

## Estrategia

O mapa vivo de estrategia fica em [`STRATEGY.md`](STRATEGY.md). Atualize esse arquivo quando mudar probabilidade, edge, sizing, filtros de sinal, risco ou backtest.

## Backend

```powershell
cd backend
$env:UV_CACHE_DIR = (Join-Path (Get-Location) ".uv-cache")
$env:UV_PROJECT_ENVIRONMENT = ".venv-codex"
uv sync
uv run uvicorn app.main:app --reload --reload-dir app --port 8000
```

Checks:

```powershell
cd backend
$env:UV_CACHE_DIR = (Join-Path (Get-Location) ".uv-cache")
$env:UV_PROJECT_ENVIRONMENT = ".venv-codex"
uv run pytest
uv run ruff check .
uv run mypy app
```

Coleta manual para confirmar o baseline:

```powershell
cd backend
$env:UV_CACHE_DIR = (Join-Path (Get-Location) ".uv-cache")
$env:UV_PROJECT_ENVIRONMENT = ".venv-codex"
uv run python -m app.collectors.run_once all --cities seoul,tokyo,hong-kong --json
```

Validacao inicial:

```powershell
cd backend
uv run python -m app.collectors.backfill --days 730
uv run python -m app.collectors.market_history_backfill --cities seoul,tokyo,hong-kong --days 730 --json
uv run python -m app.collectors.market_history_backfill --cities seoul,tokyo,hong-kong --days 730 --probe-trades --json
uv run python -m analysis.calibration
uv run python -m analysis.city_volatility --cities seoul,tokyo,hong-kong --days 730 --min-samples 120
uv run python -m analysis.backtest --mode both
uv run python -m analysis.backtest --mode historical-price
uv run python -m analysis.historical_validation --cities seoul,tokyo,hong-kong --days 730 --json
uv run python -m analysis.evidence --cities seoul,tokyo,hong-kong
uv run python -m analysis.measurement
```

Com `COLLECTORS_ENABLED=true`, o backend inicia o scheduler automaticamente:

- mercados, precos e books a cada 15 min;
- forecasts e ensembles a cada 60 min;
- resolucoes a cada 30 min;
- evidence report apos cada rodada de resolucao;
- paper settlement, measurement e evidence apos resolucoes;
- replay/calibracao/volatilidade/measurement/evidence semanalmente, por padrao domingo 18:00 UTC.

## Frontend

```powershell
cd frontend
pnpm install
pnpm dev
pnpm build
```

O Vite faz proxy de `/api` para `http://127.0.0.1:8000`.

## Configuracao

Copie `.env.example` para `.env` e ajuste conforme necessario. O default e `MODE=paper` com validacao automatizada paper-only. A coleta comeca controlada por:

```text
CITIES=["seoul","tokyo","hong-kong"]
```

Deixe `CITIES` vazio/remova a variavel apenas quando quiser coletar todas as cidades ativas com estacao conhecida. Valores monetarios e precos sempre sao `Decimal` no backend e strings no JSON; o frontend apenas formata.

O endpoint `/api/analysis/measurement` mostra se o motor paper esta medindo corretamente fees, fills, ledger, settlement, slippage e diferenca contra replay. Ele e requisito antes de qualquer plano futuro de capital real.

O endpoint `/api/analysis/historical-validation` mostra a prova retrospectiva com historico climatico, `prices-history` e trades historicos publicos validados. Esse relatorio mede Brier/PnL proxy sem book depth; ele ajuda a validar a tese, mas nao substitui a auditoria forward de fills paper contra book real. O modo `--probe-trades` testa filtros da Data API e nao persiste trade points.
