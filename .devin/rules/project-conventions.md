---
trigger: always_on
description: Convenções gerais do projeto Polymarket Weather Bot (stack, estilo, estrutura)
---

# Convenções do Projeto

## Idioma
- Respostas do assistente, comentários de UI e documentação: **Português (BR)**.
- Nomes de código (variáveis, funções, classes, arquivos): **inglês**.

## Stack (decidida — não alterar sem aprovação do usuário)
- **Back-end**: Python 3.12+, FastAPI, SQLite (SQLAlchemy 2.x), pydantic-settings.
- **SDK Polymarket**: SDK oficial Python (ver rule `polymarket-api.md`).
- **Front-end**: React + Vite + TypeScript estrito, TailwindCSS, shadcn/ui, Lucide, Recharts.
- **Fontes de previsão do tempo**: AINDA NÃO DEFINIDAS — não implementar integração com NWS, Open-Meteo ou similares sem decisão conjunta com o usuário.

## Python
- Tipagem completa (type hints em todas as assinaturas); validar com `mypy --strict`.
- Lint/format com Ruff (`ruff check` + `ruff format`).
- Testes com `pytest`; código novo de lógica de negócio exige teste correspondente.
- Preços e tamanhos de ordem usam `decimal.Decimal` — **nunca** `float` para dinheiro/preço.
- Datas sempre timezone-aware (`datetime` com `tzinfo`); armazenar em UTC.
- Async-first: clientes HTTP/WS assíncronos (httpx/asyncio) no caminho quente.

## TypeScript / Front-end
- `strict: true` no tsconfig; proibido `any` implícito.
- Componentes funcionais com hooks; estado de servidor via TanStack Query.
- UI exibe valores monetários com 2–3 casas decimais e probabilidade em %.

## Estrutura de pastas alvo (fases futuras)
```
backend/app/{api,polymarket,weather,strategy,execution,db}/
frontend/src/{components,pages,hooks,lib}/
```

## Git
- Commits convencionais (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`).
- Nunca commitar `.env`, bancos SQLite locais ou credenciais (ver `trading-safety.md`).
