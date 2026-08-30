# Meridian

![CI](https://github.com/Sourav-02121996/Meridian/actions/workflows/ci.yml/badge.svg)

Meridian is a local, self-hosted job-search assistant. It discovers job postings, scores each one against your résumé, and — for postings that clear a confidence bar you set — fills in and submits the real application form on your behalf. Anything it can't answer with confidence is routed to you for a quick review instead of being guessed.

> **Read this before you turn on automation.** Meridian's batch scheduler can submit real applications to real employers with no human click in between. Score thresholds, per-field confidence checks, and a human-review queue exist to keep that safe — but you should understand them before enabling recurring batches. See [How auto-apply decides what to do](#how-auto-apply-decides-what-to-do) below.

## Contents

- [What Meridian does](#what-meridian-does)
- [Why it exists](#why-it-exists)
- [Architecture](#architecture)
- [How auto-apply decides what to do](#how-auto-apply-decides-what-to-do)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running Meridian](#running-meridian)
- [Using Meridian](#using-meridian)
- [Configuration](#configuration)
- [Testing](#testing)
- [CI](#ci)
- [Project status and scope](#project-status-and-scope)

## What Meridian does

1. **Discover** — drives a real, headless Chromium browser against [HiringCafe](https://hiring.cafe)'s rendered search results (it does not call HiringCafe's private API directly), scrolls through results at a polite pace, and extracts each posting's original employer/ATS application link.
2. **Score** — embeds your résumé and each job description with a sentence-transformer model, then combines requirement coverage, hard-skill overlap, and overall semantic similarity into a single 0–100 match score.
3. **Track** — every discovered job lands in a per-workspace pipeline (`discovered → to_apply → applied / skipped`) that you can filter, sort, export to Excel, and edit by hand at any time.
4. **Apply automatically, within limits** — for jobs at or above your auto-apply threshold, on a supported ATS, with a complete-enough profile, Meridian opens the real application page, fills in the form field-by-field (matched by label text, not guesswork), attaches your résumé, and submits it. If even one required field can't be answered with confidence, the whole submission is refused and the job goes to a review queue instead — it never partially submits or guesses on sensitive questions.
5. **Schedule** — run discovery-and-apply on a recurring cadence (hourly/daily/weekly, N times or indefinitely) via **batches**, or point a batch at a spreadsheet you've already reviewed to re-apply against exactly those jobs.
6. **Review what it couldn't do** — any question the automation couldn't confidently answer is shown to you, optionally alongside an AI-drafted suggestion, so you approve or correct it once and can retry the submission.

## Why it exists

Job searching at scale is mostly repetitive data entry: the same name, contact details, work-authorization answers, and résumé, retyped into a slightly different form on every ATS. Meridian's goal is to remove that repetition without removing your judgment from decisions that matter — it only automates a submission when it's confident the form is filled with values you actually provided (your typed profile fields, your résumé, or an answer you personally approved), and it draws a hard line around anything else: EEO/self-identification and legal/compliance questions are never left to an AI guess, multi-step account-creation ATS platforms (Workday) are excluded entirely, and CAPTCHA-protected forms are skipped rather than worked around.

## Architecture

Meridian is two processes talking over HTTP, plus a headless browser the backend drives directly:

```
┌───────────────────────────┐        HTTP (/api/*)        ┌──────────────────────────────────────┐
│   Frontend (React + Vite) │ ───────────────────────────▶ │        Backend (FastAPI)              │
│   http://localhost:5173   │ ◀─────────────────────────── │        http://localhost:8000          │
│                            │        JSON responses        │                                        │
│  Workspaces · Batches ·    │                              │  ┌──────────────┐  ┌────────────────┐  │
│  Job pipeline · Blocked-   │                              │  │  Discovery   │  │  Scoring        │  │
│  question review ·        │                              │  │  (crawler +  │  │  (sentence-     │  │
│  Dashboard                │                              │  │  extractor)  │  │  transformers)  │  │
└───────────────────────────┘                              │  └──────┬───────┘  └────────┬────────┘  │
                                                             │         │                   │           │
                                                             │         ▼                   ▼           │
                                                             │  ┌────────────────────────────────────┐ │
                                                             │  │     SQLite (workspaces, jobs,      │ │
                                                             │  │  blocked questions, batches, runs) │ │
                                                             │  └────────────────────────────────────┘ │
                                                             │         ▲                   ▲           │
                                                             │         │                   │           │
                                                             │  ┌──────┴───────┐  ┌────────┴────────┐  │
                                                             │  │  Scheduler   │  │  Apply adapters  │  │
                                                             │  │ (APScheduler,│─▶│  engine (Playwright│ │
                                                             │  │  runs batches)│  │  fills & submits  │ │
                                                             │  └──────────────┘  │  real ATS forms)  │  │
                                                             │                    └────────┬──────────┘ │
                                                             └─────────────────────────────┼────────────┘
                                                                                            │
                                                                     ┌──────────────────────┼─────────────────────┐
                                                                     ▼                       ▼                     ▼
                                                              Greenhouse / Lever /    Workday (excluded,   Local Ollama server
                                                              Ashby / Workable / ...   unsupported flow)   (optional LLM tiers,
                                                              real employer forms                          answers grounded in
                                                                                                            your résumé only)
```

**Backend** ([backend/README.md](backend/README.md)) — a FastAPI service that owns all business logic: the Playwright-driven discovery crawler, the résumé/job-description scorer, the SQLite-backed data model, the APScheduler-driven batch runner, and the auto-apply engine (`backend/app/apply_adapters/`) that actually opens and submits real ATS forms. A four-tier confidence chain resolves each application question — your own explicit answer, a semantic match to your typed profile, and (only if you opt in) two local-LLM tiers that never see or answer EEO/compliance-sensitive questions and never run without a local Ollama server.

**Frontend** ([frontend/README.md](frontend/README.md)) — a React + TypeScript dashboard that is a pure client of the backend's REST API: workspace and batch management, the live job pipeline with score breakdowns, and the human-review UI for questions the automation couldn't resolve.

**No cloud dependency.** Everything — the database, the browser automation, the embedding model, and (if enabled) the LLM — runs on your own machine. The only outbound network calls are to HiringCafe (discovery) and to the real employer/ATS site you're applying to.

## How auto-apply decides what to do

For every job that reaches or exceeds a workspace's (or batch's) auto-apply threshold, on an ATS other than Workday, with a résumé file on file, Meridian attempts a real submission. Each required field is resolved in this order, and the **first tier that produces a confident answer wins**:

| Tier | What it does | Can it touch EEO / compliance questions? |
|---|---|---|
| **0 — Your prior approval** | Reuses an answer you explicitly approved for this exact job on an earlier attempt. | Yes — it's your own answer. |
| **B — Profile match** | Embeds the question and matches it against the fields you typed into your applicant profile, requiring a confident, unambiguous match. | Yes — it's still your own stated data, just matched by meaning instead of exact label text. |
| **C — Grounded LLM** *(opt-in, off by default)* | Asks a local Ollama model to answer strictly from retrieved excerpts of your résumé, or admit it doesn't know. | **Never.** |
| **D — Educated-guess LLM** *(opt-in, off by default)* | A more permissive drafter for subjective/motivational prose questions only; for choice-based fields it must pick one of the form's real options. | **Never.** |

If **any** required field is left unresolved after all enabled tiers, the entire submission is aborted — nothing is partially submitted — and the job is routed to a **needs-review** queue in the dashboard, where you answer or dismiss each question (optionally starting from an AI-drafted suggestion you can edit) and then retry. Visible CAPTCHAs, closed listings, and forms that fail their own validation are likewise refused rather than forced through. Full detail, including which ATS platforms have dedicated support, lives in [backend/README.md §8](backend/README.md#8-automated-apply-what-actually-happens).

If you'd rather never automate submission at all, leave the LLM tiers off, set a workspace's auto-apply threshold above any score you expect a job to reach, and use the standalone `backend/assisted_apply.py` script instead — it opens each job you've marked `to_apply` in a visible browser and waits for you to review and submit it yourself.

## Repository layout

```
Meridian/
├── backend/           FastAPI service: discovery, scoring, scheduling, and the auto-apply engine
│   └── README.md      Full backend documentation — API reference, config, auto-apply internals
├── frontend/          React + TypeScript dashboard
│   └── README.md      Full frontend documentation — screens, API integration, styling
├── run.sh             Starts backend and frontend together for local development
├── pyproject.toml     Shared ruff (Python lint/format) configuration
└── .github/workflows/ci.yml   Lint, test, build, and secret-scan checks run on every PR
```

## Requirements

- Python 3.11+
- Node.js 18+
- ~400 MB free disk space for the first sentence-transformer model download (cached afterward)
- Optional: a local [Ollama](https://ollama.com/) install, only if you enable the LLM answer-drafting tiers

## Installation

```bash
git clone git@github.com:Sourav-02121996/Meridian.git
cd Meridian

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m playwright install chromium

cp backend/.env.example backend/.env

cd frontend && npm install && cd ..
chmod +x run.sh
```

## Running Meridian

```bash
./run.sh
```

This starts the backend (`uvicorn app.main:app --reload --port 8000`) and the frontend dev server (`npm run dev`) together and stops both on exit.

- App: `http://localhost:5173`
- API + interactive docs: `http://localhost:8000/docs`

The SQLite database is created automatically inside `backend/` on first run (no manual migration step). The first discovery run downloads the `all-mpnet-base-v2` embedding model (~400 MB); later runs reuse the local cache.

## Using Meridian

1. **Create a workspace.** Each workspace is an isolated résumé, applicant profile, and set of thresholds — use separate workspaces for different roles or career tracks.
2. **Add your résumé and profile.** Paste résumé text or upload a text-based PDF (scanned image-only PDFs need OCR first). Fill in the applicant profile fields that apply to you — contact details, location, work eligibility, EEO self-identification (optional, defaults to "decline to self-identify"), and a cover letter. Blank fields are skipped by automation, never guessed.
3. **Set your thresholds.** The match threshold controls what counts as a "strong match" in the dashboard; the (typically higher) auto-apply threshold controls what Meridian is allowed to submit automatically. Calibrate both against a handful of jobs you already consider good or poor fits — the defaults (82 / 95) are starting points, not universal answers.
4. **Discover jobs.** Set a search query, date range, and job count, then run discovery. Progress streams live; results are scored and deduplicated automatically.
5. **Review the pipeline.** Expand a job to see its score breakdown, missing skills, and weak requirements. Move jobs between statuses by hand at any time — doing so always overrides any pending automation state for that job.
6. **Optionally, set up a batch** (Batches screen) to run discovery-and-apply on a schedule, or to re-apply against a spreadsheet of jobs you've already reviewed and exported. Batches are where real, unattended submissions happen — read [How auto-apply decides what to do](#how-auto-apply-decides-what-to-do) before turning one on.
7. **Resolve anything flagged for review.** Jobs the automation couldn't confidently finish appear in a review queue with the specific question(s) it got stuck on. Answer or dismiss each one, then retry — Meridian will re-attempt the exact same job with your new answer available.
8. **Check the global dashboard** for a cross-workspace view of totals, applications, and active batches.

If HiringCafe or a specific ATS presents a Cloudflare/interactive challenge, set `CRAWLER_HEADLESS=false` in `backend/.env`, restart, and complete the one-time challenge in the visible browser window that opens.

## Configuration

Copy `backend/.env.example` to `backend/.env` and adjust as needed. The full variable reference — discovery defaults, score thresholds, browser behavior, the confidence-matching thresholds for automated answers, and the optional local-LLM settings — is documented in [backend/README.md §6](backend/README.md#6-configuration-reference).

## Testing

```bash
# Backend — 111 tests, driven against local ATS HTML fixtures (no live network calls, nothing is ever really submitted)
cd backend && pytest tests/ -v

# Frontend — type-check and production build
cd frontend && npm run build
```

See each subproject's README for full detail: [backend testing](backend/README.md#10-testing), [frontend checks](frontend/README.md#9-linting-formatting-and-ci).

## CI

Every pull request and push to `main` runs, via `.github/workflows/ci.yml`:

- **Backend lint** — `ruff check`
- **Backend tests** — `pytest`, with Playwright's Chromium installed in CI
- **Frontend checks** — Prettier format check + a full TypeScript build
- **Secret scan** — `gitleaks` across the whole repository

## Project status and scope

Meridian is a single-user, locally-run tool — there is no multi-tenant auth (the in-app "login" is a client-side placeholder), no cloud deployment target, and no hosted database. It's built to run on your own machine, under your own control, applying to jobs on your behalf using only data you've explicitly given it. If you extend it to a shared or hosted environment, add real authentication and review the auto-apply safeguards in [backend/README.md](backend/README.md) before doing so — they were designed around a trusted, single-operator setup.
