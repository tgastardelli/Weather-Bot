# Weather Bot Agent Instructions

## Project Context

This repository is the Polymarket Weather Bot. Codex must treat the `.devin`
folder as canonical project guidance and consult it before changing code.

Always read and follow these rules when they are relevant:

- `.devin/rules/project-conventions.md`
- `.devin/rules/trading-safety.md`
- `.devin/rules/polymarket-api.md`

Use these local skills as implementation references:

- Backend Python/FastAPI work: `.devin/skills/python-backend/SKILL.md`
- Frontend React/Vite work: `.devin/skills/react-frontend/SKILL.md`
- Polymarket API work: `.devin/skills/polymarket-api/SKILL.md`
- Paper trading work: `.devin/skills/paper-trading/SKILL.md`

## Working Agreements

- Respond to the user in Brazilian Portuguese.
- Use English for code identifiers, filenames, classes, functions, and variables.
- Prefer the existing project patterns over new abstractions.
- Keep changes scoped to the requested behavior.
- Do not commit `.env`, local SQLite databases, caches, virtualenvs, credentials, or secrets.

## Strategy Documentation

- `STRATEGY.md` is the canonical human-readable map of how the bot thinks.
- Update `STRATEGY.md` whenever changing strategy logic, probability modeling, edge
  calculation, sizing, signal filters, risk rules, or backtest behavior.
- Keep the frontend Strategy page aligned with `STRATEGY.md`; the document is the
  source of truth.

## Stack

- Backend: Python 3.12+, FastAPI, SQLAlchemy 2 async, SQLite, pydantic-settings, uv.
- Frontend: React, Vite, strict TypeScript, Tailwind CSS, shadcn/ui, Lucide, Recharts.
- Server state in the frontend should use TanStack Query.
- Backend JSON must serialize money/prices as strings; frontend should format, not recalculate.

## Safety Rules

- Paper mode is the default.
- Do not implement, execute, test, or simulate real live orders unless the user explicitly asks for that phase.
- Never request, hardcode, print, log, or commit private keys, API secrets, wallet credentials, or `.env` values.
- Use `Decimal` for money, prices, probabilities used in trading decisions, and PnL-sensitive calculations.
- Use timezone-aware UTC datetimes.
- Weather-market edge and PnL calculations must account for the 5% taker fee.
- Live trading remains out of scope unless the user explicitly changes that scope.

## Validation Commands

Backend checks should be run from `backend/`:

```powershell
uv run pytest
uv run ruff check .
uv run mypy app
```

Frontend checks should be run from `frontend/`:

```powershell
pnpm build
```
