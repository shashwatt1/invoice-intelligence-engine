# Invoice Intelligence Engine — Frontend

React frontend for the Invoice Intelligence Engine. Consumes the backend
exclusively through its REST API — no business logic lives here.

## Stack

React 19, TypeScript, Vite, TailwindCSS, shadcn/ui, TanStack Query,
React Router, Axios, Framer Motion, Recharts.

## Structure

```
src/
  api/            Typed API client (client.ts, endpoints.ts, types.ts)
                    — the only place that talks to the backend
  hooks/          TanStack Query hooks — all server state lives here
  components/
    ui/            shadcn/ui primitives
    layout/        App shell, sidebar, page header
    dashboard/      Dashboard-specific components
    processing/     Upload + live processing timeline
    invoice/        Invoice detail, validation report, developer panel
    shared/         Cross-page components (status badges, pagination, ...)
  pages/          Route-level components
  lib/            Formatting utilities, status vocabulary, cn() helper
```

## Running

```bash
npm install
npm run dev       # http://localhost:5173, proxies /api to :8000
```

## Quality checks

```bash
npm run lint       # oxlint
npx tsc -b         # type-check
npm run build       # production build
```

## Conventions

- All backend communication goes through `src/api/` — components and
  hooks never call `fetch`/`axios` directly.
- Server state is managed by TanStack Query hooks in `src/hooks/`, not
  component-local state.
- The API's `{success, data, error}` envelope is unwrapped once, in the
  Axios response interceptor (`src/api/client.ts`) — callers work with
  plain typed data or a thrown `ApiError`.
