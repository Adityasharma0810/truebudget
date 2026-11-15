# Baseline audit

The existing root Vite application is the canonical web app; no `apps/web` exists.
It contains React 19, Tailwind 4, strict TypeScript, and an empty App component.
The existing entrypoint, Vite configuration, toolchain, and styling pipeline are retained.

| Surface | Baseline status |
|---|---|
| Public pages | Missing |
| Console shell and all console pages | Missing |
| Transport, types, mock engine | Missing |
| Data shapes | None |
| Motion system | Missing |
| Tests / lint | No scripts configured |

Baseline checks: `pnpm build` passed; `pnpm exec tsc --noEmit` passed.
Baseline Vite output: entry JS 117.71 kB gzip; CSS 1.86 kB gzip.

Implementation stays at the root to preserve the host-managed preview.
