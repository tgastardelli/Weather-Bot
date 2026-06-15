---
name: react-frontend
description: Referência da stack do front-end do Weather Bot — Vite + React + TypeScript estrito + Tailwind v4 + shadcn/ui + TanStack Query + Recharts, incluindo scaffold, configs prontas (vite.config, tsconfig, proxy) e padrões de exibição de dinheiro/probabilidade. Usar ao criar ou alterar código em frontend/.
---

# Front-end React — Stack e Padrões

Stack decidida: Vite + React + TypeScript estrito, Tailwind CSS v4, shadcn/ui, Lucide, TanStack Query v5, Recharts. Versões verificadas em 10/06/2026: Vite 8 (Rolldown) · React 19 · Tailwind 4 (`@tailwindcss/vite`) · TanStack Query 5.101.x · Recharts 3. Gerenciador: **pnpm**.

## 1. Setup & scaffold

```powershell
# na raiz do repo
pnpm create vite@latest frontend --template react-ts
# dentro de frontend/
pnpm add tailwindcss @tailwindcss/vite
pnpm add @tanstack/react-query recharts lucide-react
pnpm add -D @types/node @tanstack/eslint-plugin-query
pnpm dlx shadcn@latest init
pnpm dlx shadcn@latest add button card table badge tabs skeleton sonner
# rodar
pnpm dev          # http://localhost:5173
pnpm build        # tsc -b && vite build
pnpm lint
```

- Tailwind v4 não usa `tailwind.config.js` por padrão: basta o plugin no Vite e `@import "tailwindcss";` como primeira linha de `src/index.css` (o `shadcn init` adiciona os tokens de tema no mesmo arquivo).
- Componentes shadcn/ui são **copiados para `src/components/ui/`** — código nosso, versionado; atualizar via CLI, nunca editar à mão sem registrar o motivo.

Estrutura alvo:

```
frontend/src/
  components/      # componentes próprios + ui/ (shadcn)
  pages/           # uma página por rota (Dashboard, Markets, Positions, Orders)
  hooks/           # hooks TanStack Query por recurso (use-markets.ts, ...)
  lib/             # api.ts (client), format.ts (dinheiro/%, datas), utils.ts (cn)
  types/           # tipos espelhando os schemas pydantic do back
```

## 2. Configs prontas

### vite.config.ts (alias `@/` + proxy para o FastAPI)

```ts
import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
})
```

- O proxy só existe em `pnpm dev`; em produção servir o build atrás do mesmo host do back (ou configurar base URL via `import.meta.env.VITE_API_URL`).

### tsconfig (estrito)

No `tsconfig.json` e `tsconfig.app.json`, dentro de `compilerOptions`:

```jsonc
{
  "baseUrl": ".",
  "paths": { "@/*": ["./src/*"] },
  // template já traz strict: true; adicionar:
  "noUncheckedIndexedAccess": true,
  "noFallthroughCasesInSwitch": true
}
```

- Proibido `any` (implícito ou explícito) e `as` para silenciar erro de tipo — modelar o tipo correto.
- ESLint: o template já vem com flat config (`eslint.config.js`); adicionar `@tanstack/eslint-plugin-query` (pega `queryKey` mal formada e closures suspeitas).

## 3. Camada de API e tipos

### Contrato com o back-end

**Dinheiro/preço chega como string** (Decimal serializado pelo pydantic — ver skill `python-backend`); datas chegam como ISO 8601 UTC. Nunca fazer aritmética monetária no front — todo cálculo (PnL, fees, exposição) é responsabilidade do back; o front só formata.

```ts
// src/types/api.ts — espelha os schemas pydantic
export interface Position {
  token_id: string
  market_condition_id: string
  qty: string        // Decimal como string
  avg_cost: string
  updated_at: string // ISO UTC
}

export interface EquitySnapshot {
  ts_utc: string
  cash: string
  equity: string
  realized_pnl: string
  unrealized_pnl: string
}
```

### Client tipado

```ts
// src/lib/api.ts
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) throw new ApiError(res.status, await res.text())
  return res.json() as Promise<T>
}
```

## 4. Estado de servidor — TanStack Query v5

```tsx
// main.tsx
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 2 } },
})
// <QueryClientProvider client={queryClient}><App /></QueryClientProvider>
```

Query keys como factory + um hook por recurso:

```ts
// src/hooks/use-positions.ts
export const positionKeys = {
  all: ["positions"] as const,
  detail: (tokenId: string) => ["positions", tokenId] as const,
}

export function usePositions() {
  return useQuery({
    queryKey: positionKeys.all,
    queryFn: () => api<Position[]>("/positions"),
    refetchInterval: 10_000,   // dados de mercado/posições: polling
  })
}
```

- **Polling** (`refetchInterval`) para preços/posições/ordens; equity e histórico podem usar `staleTime` maior. Se o back expuser WebSocket/SSE no futuro, invalidar via `queryClient.invalidateQueries`.
- Mutations (ex.: criar ordem paper) invalidam as keys afetadas no `onSuccess`; exibir motivo de rejeição do módulo de risco vindo do back (toast `sonner`).
- Estados de carregamento com `skeleton` do shadcn; erros com mensagem da `ApiError` — nunca tela em branco.

## 5. UI — exibição e gráficos

### Formatação (regras do projeto)

```ts
// src/lib/format.ts — Number() SÓ para exibição, nunca para cálculo
export function formatMoney(value: string, digits: 2 | 3 = 2): string {
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })
}

export function formatProbability(price: string): string {
  return `${(Number(price) * 100).toFixed(1)}%`   // preço 0–1 → %
}

export function formatLocalTime(isoUtc: string): string {
  return new Date(isoUtc).toLocaleString()        // armazenado UTC, exibido local
}
```

- Dinheiro: 2–3 casas decimais; probabilidade em % (preço 0–1 × 100); timestamps UTC → fuso local só na exibição.
- PnL com cor semântica (verde/vermelho) e sinal explícito (`+`/`−`).

### Recharts (curva de equity/PnL)

```tsx
const data = snapshots.map((s) => ({ ts: s.ts_utc, equity: Number(s.equity) }))
// conversão p/ number só na borda do gráfico (exibição)

<ResponsiveContainer width="100%" height={300}>
  <LineChart data={data}>
    <XAxis dataKey="ts" tickFormatter={formatLocalTime} />
    <YAxis domain={["auto", "auto"]} />
    <Tooltip formatter={(v) => formatMoney(String(v))} />
    <Line type="monotone" dataKey="equity" dot={false} />
  </LineChart>
</ResponsiveContainer>
```

### Padrões de componente

- Componentes funcionais com hooks; sem classes. Props tipadas com `interface`, sem `React.FC`.
- Estado de servidor SEMPRE no TanStack Query; `useState` só para estado de UI local (tabs, filtros, modais).
- Tabelas (ordens, posições, fills) com `table` do shadcn; status com `badge` (OPEN/FILLED/REJECTED...).
- Ícones Lucide (`lucide-react`), importação nomeada por ícone.

## 6. Armadilhas

- `parseFloat`/aritmética em valores monetários no front — só formatação; cálculo é no back (precisão de `Decimal` se perde em `number`).
- `new Date("2026-06-10")` (sem hora) é interpretado como UTC midnight e desloca o dia no fuso local — sempre receber ISO completo com hora/offset.
- `refetchInterval` curto + componentes pesados = re-render em cascata; usar `select` para recortar dados e memoizar transformações de gráfico.
- React 19 + StrictMode em dev monta efeitos 2× — efeitos precisam ser idempotentes (não é bug).
- Recharts exige `number` no `dataKey` — converter string→number apenas na montagem do `data` do gráfico.
- Esquecer que o proxy `/api` não existe no preview de build (`pnpm preview`) — configurar `VITE_API_URL` ou rodar atrás do back.
- `queryKey` instável (objeto/array recriado inline com referência nova porém igual é ok; valores não-serializáveis não são) — manter keys primitivas via factory.
