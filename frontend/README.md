# Meridian Frontend

The dashboard UI for Meridian — a React + TypeScript single-page app for managing job-search workspaces, reviewing scored job postings, configuring scheduled discovery/auto-apply batches, and resolving application questions the automated apply engine couldn't answer on its own. It is a pure client of the [backend](../backend/README.md) FastAPI service and holds no business logic of its own beyond presentation and client-side state.

## Contents

- [1. What this app does](#1-what-this-app-does)
- [2. Tech stack](#2-tech-stack)
- [3. Project layout](#3-project-layout)
- [4. Setup and running](#4-setup-and-running)
- [5. Screens and user flows](#5-screens-and-user-flows)
- [6. Talking to the backend](#6-talking-to-the-backend)
- [7. State management](#7-state-management)
- [8. Styling and design tokens](#8-styling-and-design-tokens)
- [9. Linting, formatting, and CI](#9-linting-formatting-and-ci)

## 1. What this app does

A user creates one or more **workspaces**, each holding a résumé, an applicant profile, and score thresholds. From a workspace they can trigger a live job discovery run, review the resulting scored pipeline of postings, and inspect why the automated apply engine flagged a specific job for human review. From the **Batches** screen they can schedule unattended discovery-and-apply runs (or import a spreadsheet of jobs to re-apply against), pause/resume/run them on demand, and work through a cross-batch "needs your review" queue. A **Global dashboard** rolls all of that up across every workspace.

## 2. Tech stack

| Concern                      | Library                                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------- |
| Framework                    | React 18.3                                                                                              |
| Language                     | TypeScript 5.5 (strict mode)                                                                            |
| Build tool                   | Vite 8 with `@vitejs/plugin-react`                                                                      |
| Styling                      | Tailwind CSS 3.4 + PostCSS/Autoprefixer                                                                 |
| Server-state / data fetching | TanStack React Query 5                                                                                  |
| Charts                       | Recharts                                                                                                |
| Icons                        | lucide-react                                                                                            |
| Formatting                   | Prettier (no ESLint is configured — type safety and Prettier are the only automated code-quality gates) |

There is no client-side router (navigation is a small hand-rolled view state machine — see §7) and no test framework configured.

## 3. Project layout

```
frontend/
├── index.html                    # HTML shell; pre-mount theme script, font preconnects
├── vite.config.ts                 # Vite config: dev server on :5173, /api proxied to http://localhost:8000
├── tailwind.config.js               # Dark-mode via class, CSS-variable-backed color tokens, tightened border radii
└── src/
    ├── main.tsx                        # Bootstraps React, QueryClientProvider, mounts <Site/>
    ├── Site.tsx                         # Root shell: marketing page vs. logged-in app, login stub, top-level view routing
    ├── Sidebar.tsx                       # Collapsible in-app navigation (Workspaces / Batches / Dashboard)
    ├── WorkspaceGrid.tsx                  # "Your workspaces" grid, create/delete workspace
    ├── WorkspaceView.tsx                   # Résumé, applicant profile, thresholds, job discovery, stats, job pipeline table
    ├── BatchesPage.tsx                      # Batch list/management, run history, per-batch review queue
    ├── GlobalDashboard.tsx                   # Cross-workspace aggregate metrics and charts
    ├── BlockedQuestionsPanel.tsx               # Review/answer/dismiss questions the auto-apply engine couldn't resolve
    ├── MultiSelectChips.tsx                     # Reusable multi-select chip control (department/seniority filters)
    ├── api.ts                                    # Every backend endpoint call, in one place, with request/response types
    ├── reviewReasons.ts                            # Maps backend review_reason codes to human-readable labels
    ├── theme.ts                                     # Light/dark theme helpers (localStorage-backed)
    └── index.css                                     # Tailwind directives, CSS custom properties, component classes
```

## 4. Setup and running

Requirements: **Node.js 18+**, and the [backend](../backend/README.md) running on `http://localhost:8000` (all API calls are relative `/api/...` paths, proxied by Vite in dev).

```bash
cd frontend
npm install

npm run dev        # starts the dev server at http://localhost:5173, proxying /api to :8000
```

Other scripts:

```bash
npm run build         # tsc -b (type-check) && vite build → dist/
npm run preview       # serve the production build locally
npm run format         # prettier --write .
npm run format:check   # prettier --check . (used in CI)
```

No environment variables are required — there is no `VITE_API_URL` or `.env` file; the API base is always the relative path `/api`, resolved via Vite's dev proxy locally or by serving the built app behind the same origin as the backend in production.

## 5. Screens and user flows

**Home / marketing page** (`Site.tsx`) — shown to a signed-out visitor: hero, a three-step "how it works" explainer, and a login dialog. The login is a client-side prototype: any syntactically valid email is accepted and stored in `localStorage`, with no backend verification — this is explicitly a stand-in for real authentication, not a security boundary.

**Workspaces** (`WorkspaceGrid.tsx`) — a card grid of every workspace with its job/applied/above-threshold counts, a "New workspace" tile, and per-card delete (with confirmation).

**Workspace detail** (`WorkspaceView.tsx`) — the main working screen:

- **Résumé** — paste text or upload a PDF (extracted server-side); the uploaded file itself is what gets attached to automated applications later.
- **Applicant profile** — the structured form (contact info, location, work eligibility, EEO self-identification, compliance fields, cover letter) that the backend's automated apply engine matches form questions against. Fields left blank are skipped by automation, never guessed.
- **Thresholds** — two independent sliders: the general match threshold (affects "strong match" highlighting only) and the auto-apply threshold (actually gates automated submission).
- **Discover** — a search form (query, date range, job count, optional department/seniority/title filters) that starts a live crawl; progress is polled once per second and shown as a running "collected N" counter.
- **Stats and charts** — score histogram, jobs-by-ATS breakdown, pipeline status pie chart, applications-over-time line chart.
- **Job pipeline table** — filterable/sortable list of every discovered job. Each row expands to show requirement/skill coverage, missing skills, a link to the original posting, and status controls. Jobs the automation flagged `needs_review` show a `BlockedQuestionsPanel` and a "Retry auto-apply" action, distinct from "Mark applied" (which only updates the tracked status and never launches a browser).

**Batches** (`BatchesPage.tsx`) — every scheduled batch across all workspaces, with status, schedule summary, and next-run time. Actions: pause/resume, run now, delete, and — per batch — an expandable run history and a "needs your review" queue scoped to that batch's own jobs. Creating a batch supports two sources: a live HiringCafe search (one-time or recurring) or an uploaded job spreadsheet (matching the layout produced by "Download Excel" on the job table), each with its own auto-apply threshold.

**Blocked-questions review** (`BlockedQuestionsPanel.tsx`, shared by the workspace and batches screens) — for a job stuck in review, lists each unresolved required form question with the right input control for its type (text, select, radio, checkbox), an optional AI-drafted suggestion the user can accept or edit, and Approve/Dismiss actions. This is the human-in-the-loop side of the backend's automated apply engine — see [backend/README.md §8](../backend/README.md#8-automated-apply-what-actually-happens) for what happens on the other side of an approval.

**Global dashboard** (`GlobalDashboard.tsx`) — cross-workspace totals (workspaces, jobs, applications, auto-applied count, needs-review count, active batches) and a per-workspace breakdown chart.

## 6. Talking to the backend

Every backend call lives in `src/api.ts`, built on two small `fetch` wrappers (JSON and multipart upload). All requests use **relative paths** (`/api/...`) — in development, `vite.config.ts` proxies `/api/*` to `http://localhost:8000`; in production the app expects to be served from the same origin as the API (or behind a reverse proxy that routes `/api` to the backend). Endpoints cover workspaces, settings/profile/résumé, discovery and its live status, the job pipeline (list/patch/delete/retry-apply/export), blocked questions (list/answer/dismiss), batches (create/list/patch/delete/run-now/runs), and the cross-workspace dashboard. See the backend's [API reference](../backend/README.md#7-api-reference) for the full route list these calls map to.

## 7. State management

- **Server state** is entirely TanStack React Query — each screen owns its own queries/mutations, keyed by resource and workspace/batch id, with mutation success invalidating the related queries (e.g. saving a threshold invalidates both the settings and stats queries).
- **Live progress polling**: job discovery status, jobs currently mid-auto-apply, and a single job being retried are each polled every second while in progress, then stop automatically once resolved.
- **Optimistic updates**: job status changes update the cached list immediately and roll back on failure.
- **Local UI state** (dialogs, form fields, sidebar collapse, theme, the prototype login email) is plain `useState`, with a few values persisted to `localStorage`.
- **Navigation** is a manual view state machine in `Site.tsx`/`Sidebar.tsx` — there is no React Router or similar library, and no global store beyond React Query's cache.

## 8. Styling and design tokens

Tailwind is configured with `darkMode: 'class'`; light/dark values are CSS custom properties defined in `src/index.css` and consumed through Tailwind's `rgb(var(--x) / <alpha-value>)` pattern so opacity modifiers keep working across themes. A small inline script in `index.html` applies the saved theme before first paint to avoid a flash of the wrong theme. Border radii are intentionally tightened (3–14px) rather than using Tailwind's default large rounded corners. Body copy uses DM Sans, headings use Manrope, and numeric/tabular content (scores, stats) uses JetBrains Mono — all loaded via `<link>` tags in `index.html` rather than CSS `@import`, to start the font fetch earlier. Shared component classes (`.card`, `.field`, `.btn`, `.sidebar-item`, `.dropdown-panel`, etc.) live in `src/index.css`; chart colors in Recharts match the same token palette so charts also respect the active theme.

## 9. Linting, formatting, and CI

There is no ESLint configuration in this project — code quality is enforced by the TypeScript compiler (via `npm run build`) and Prettier formatting. CI (`.github/workflows/ci.yml`, job `frontend-checks`) runs on every PR/push to `main`:

```bash
npm ci
npm run format:check   # Prettier
npm run build            # tsc -b type-check + vite build
```

No frontend test suite is currently configured.
