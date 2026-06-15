---
name: python-backend
description: Referência da stack do back-end do Weather Bot — FastAPI + SQLAlchemy 2 async + SQLite com uv, incluindo scaffold, configs prontas (pyproject, ruff, mypy, pytest) e padrões obrigatórios (Decimal, UTC, async). Usar ao criar ou alterar código em backend/.
---

# Back-end Python — Stack e Padrões

Stack decidida: Python 3.12+, FastAPI, SQLAlchemy 2.x async, SQLite (aiosqlite), pydantic-settings, uv. Versões verificadas em 10/06/2026: FastAPI 0.136.x · pydantic 2.13.x · SQLAlchemy 2.0.50 · Vite/React no skill `react-frontend`.

## 1. Setup & scaffold (uv)

```powershell
# na raiz do repo
uv init backend --app --python 3.12
# dentro de backend/
uv add "fastapi>=0.136" "uvicorn[standard]>=0.34" "sqlalchemy[asyncio]>=2.0.50" "aiosqlite>=0.21" "pydantic>=2.13" "pydantic-settings>=2.10" "httpx>=0.28" polymarket-client
uv add --dev "pytest>=8.4" "pytest-asyncio>=1.0" "mypy>=1.16" "ruff>=0.12"
uv sync
# rodar
uv run uvicorn app.main:app --reload --port 8000
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy app
```

Estrutura alvo (criar pacotes com `__init__.py`):

```
backend/
  app/
    main.py            # FastAPI app + lifespan
    config.py          # Settings (pydantic-settings)
    api/               # routers HTTP (um arquivo por recurso)
    polymarket/        # wrapper do SDK polymarket-client
    weather/           # fontes de previsão (NÃO implementar antes da decisão)
    strategy/          # sinais/edge
    execution/         # engines paper/live (skill paper-trading)
    db/                # engine, session, models, types
  tests/
  pyproject.toml
  .env / .env.example  # .env NUNCA commitado
```

## 2. pyproject.toml — configs de qualidade

```toml
[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = [
  "E", "W", "F",     # pycodestyle/pyflakes
  "I",                # isort
  "UP",               # pyupgrade
  "B",                # bugbear
  "ASYNC",            # bloqueios em código async
  "DTZ",              # proíbe datetime naive — regra do projeto
  "RUF",
]

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
asyncio_mode = "auto"        # funções async de teste sem decorator
testpaths = ["tests"]
```

- `DTZ` e `ASYNC` não são opcionais: cobrem duas regras inegociáveis do projeto (UTC tz-aware; não bloquear o event loop).
- Migrations: começar com `Base.metadata.create_all` no lifespan; adotar Alembic quando o schema estabilizar.

## 3. FastAPI — padrões

### App + lifespan (startup/shutdown de recursos)

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # startup: criar engine, abrir AsyncPublicClient, iniciar tasks de background
    yield
    # shutdown: cancelar tasks, fechar clients e engine

app = FastAPI(title="Weather Bot", lifespan=lifespan)
```

- Routers por recurso em `app/api/` (`APIRouter(prefix="/api/markets", tags=["markets"])`), incluídos no `main.py`.
- CORS em dev: `CORSMiddleware` com `allow_origins=["http://localhost:5173"]` (Vite). Em produção o front é servido atrás do mesmo host/proxy.

### Dependency injection com Annotated

```python
from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

@router.get("/positions")
async def list_positions(session: SessionDep) -> list[PositionOut]: ...
```

### Schemas pydantic v2 (request/response)

- Schemas em módulo próprio (`schemas.py` por domínio); ORM → schema com `model_config = ConfigDict(from_attributes=True)`.
- **Dinheiro/preço sai como string no JSON** (contrato com o front): anotar campos `Decimal` com serializer explícito — não depender de default de serialização:

```python
from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, PlainSerializer

Money = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="json")]

class PositionOut(BaseModel):
    token_id: str
    qty: Money
    avg_cost: Money
```

- Erros: levantar `HTTPException` nos routers; exception handler global para erros de domínio (ex.: `RiskLimitExceeded` → 422 com motivo explícito).

## 4. SQLAlchemy 2 async + SQLite

### Engine, PRAGMAs e session

```python
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

engine = create_async_engine("sqlite+aiosqlite:///./data/bot.db")

@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn: object, _record: object) -> None:
    cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        async with session.begin():
            yield session   # commit no sucesso, rollback na exceção
```

### Tipos custom — Decimal e UTC (obrigatórios)

SQLite não tem DECIMAL nem timezone; armazenar `Decimal` como TEXT e garantir tzinfo na leitura:

```python
from datetime import UTC, datetime
from decimal import Decimal
from sqlalchemy import DateTime, String, TypeDecorator

class DecimalText(TypeDecorator[Decimal]):
    impl = String(40)
    cache_ok = True
    def process_bind_param(self, value: Decimal | None, dialect: object) -> str | None:
        return None if value is None else str(value)
    def process_result_value(self, value: str | None, dialect: object) -> Decimal | None:
        return None if value is None else Decimal(value)

class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime()
    cache_ok = True
    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime naive proibido — use tz-aware UTC")
        return None if value is None else value.astimezone(UTC)
    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)
```

### Models tipados (Mapped)

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    type_annotation_map = {Decimal: DecimalText, datetime: UTCDateTime}

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts_utc: Mapped[datetime]
    price: Mapped[Decimal]
    size: Mapped[Decimal]
    status: Mapped[str] = mapped_column(String(10), index=True)
```

- SQLite tem **1 writer por vez**: transações curtas, nunca segurar a session durante chamadas de rede.
- `datetime.now(UTC)` sempre (`datetime.utcnow()` é deprecated e naive).

## 5. Settings (pydantic-settings)

```python
from decimal import Decimal
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mode: Literal["paper", "live"] = "paper"   # live só com flag explícita (trading-safety)
    db_url: str = "sqlite+aiosqlite:///./data/bot.db"
    paper_initial_cash: Decimal = Decimal("1000")
    max_stake_per_order: Decimal = Decimal("10")
    max_exposure_per_market: Decimal = Decimal("50")
    max_daily_loss: Decimal = Decimal("25")
```

- Singleton via `@lru_cache` em `get_settings()`; nos endpoints/serviços usar `SettingsDep`.
- Credenciais (Fase 5) só via `.env`; ver rule `trading-safety.md`.

## 6. Padrões async

- **Um `httpx.AsyncClient`/`AsyncPublicClient` por app**, criado no lifespan e reutilizado — nunca um client por request.
- Tasks de background (loop de mercado, watcher WSS): `asyncio.create_task` no lifespan; guardar referência; no shutdown `task.cancel()` + `await` com `suppress(asyncio.CancelledError)`.
- Backoff exponencial com jitter para REST; reconexão de WSS com resubscribe (rate limits no skill `polymarket-api`).
- Proibido no caminho async: `time.sleep`, `requests`, I/O de arquivo pesado síncrono (regra `ASYNC` do ruff pega a maioria).
- CPU-bound raro → `asyncio.to_thread`.

## 7. Testes (pytest + pytest-asyncio)

```python
# tests/conftest.py
import pytest
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)  # in-memory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
```

- API: `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")` — sem servidor real.
- SDK Polymarket sempre mockado (fixtures de book/preço); **nunca** ordens reais em teste (rule `trading-safety.md`).
- Toda lógica de negócio nova (fills, fees, risco, PnL) exige teste com casos de borda em `Decimal` (quantização, arredondamento).

## 8. Armadilhas

- `float` em qualquer cálculo de dinheiro/preço — converter na borda (`Decimal(str(x))` se a fonte der float; preferir parse direto da string da API).
- Compartilhar engine/session entre event loops (pytest cria loop novo por teste — criar engine na fixture, não global).
- `expire_on_commit=True` (default) + acesso a atributos pós-commit dispara lazy load síncrono em contexto async → sempre `expire_on_commit=False`.
- Esquecer `await engine.dispose()` no shutdown → warnings de conexões abertas.
- Validar payloads da Gamma com pydantic: campos como `clobTokenIds` chegam como **string JSON** (ver skill `polymarket-api`).
- `uvicorn --reload` em Windows: usar `--reload-dir app` para evitar restart em loop por escrita no SQLite/`data/`.
