# Meridian Backend

FastAPI service that powers Meridian: it discovers job postings, scores them against a candidate's résumé, and — for postings that clear a configurable confidence bar — fills in and submits the real ATS application form on the candidate's behalf. It also exposes the REST API the [frontend](../frontend/README.md) talks to.

> **This backend can submit real job applications with no human in the loop.** Read [§8. Automated apply: what actually happens](#8-automated-apply-what-actually-happens) before pointing it at a workspace with a résumé and a batch schedule turned on.

## Contents

- [1. What this service does](#1-what-this-service-does)
- [2. Tech stack](#2-tech-stack)
- [3. Project layout](#3-project-layout)
- [4. Setup](#4-setup)
- [5. Running the service](#5-running-the-service)
- [6. Configuration reference](#6-configuration-reference)
- [7. API reference](#7-api-reference)
- [8. Automated apply: what actually happens](#8-automated-apply-what-actually-happens)
- [9. Data model](#9-data-model)
- [10. Testing](#10-testing)
- [11. Debugging a batch run](#11-debugging-a-batch-run)
- [12. CI](#12-ci)

## 1. What this service does

Meridian organizes work into **workspaces** (one résumé, one applicant profile, one set of score thresholds per workspace). For each workspace it can:

1. **Discover** — drive a headless Chromium browser against HiringCafe's rendered search results, capture the jobs it renders, extract the original employer/ATS application link, and de-duplicate against what's already stored.
2. **Score** — embed each job description and the candidate's résumé with a sentence-transformer model and compute a 0–100 match score from requirement coverage, hard-skill overlap, and overall semantic similarity.
3. **Track** — hold every discovered job in a pipeline (`discovered → to_apply → applied / skipped`) that a human can review and move manually at any time.
4. **Automate** — for jobs that score at or above a workspace's *auto-apply* threshold, launch a real browser, fill in the actual application form field-by-field, and submit it — falling back to a human-review queue for anything it cannot resolve with confidence (see §8).
5. **Schedule** — run discovery-and-apply on a recurring cadence via an in-process scheduler (**batches**), or re-run apply against a previously exported/edited spreadsheet of jobs (**upload batches**).

## 2. Tech stack

| Concern | Library |
|---|---|
| Web framework | [FastAPI](https://fastapi.tiangolo.com/) `>=0.115,<1`, served by `uvicorn[standard]` |
| ORM / database | SQLAlchemy `>=2.0,<3` (declarative `Mapped`/`mapped_column` style), SQLite by default |
| Settings | `pydantic-settings` `>=2.4,<3`, loaded from `backend/.env` |
| Browser automation | Playwright `>=1.46,<2` — used both for the discovery crawler and the auto-apply engine |
| Embeddings / scoring | `sentence-transformers` `>=3.0,<4` (`all-mpnet-base-v2` by default) + `numpy` |
| HTML parsing | `beautifulsoup4` |
| Résumé PDF extraction | `pypdf` |
| Excel import/export | `openpyxl` |
| Scheduling | `APScheduler` `>=3.10,<4` (in-process `BackgroundScheduler`) |
| Local LLM client | `httpx`, talking to a local [Ollama](https://ollama.com/) server (no cloud LLM, no API key) |
| Lint / test (dev only) | `ruff`, `pytest` |

There is no Alembic and no external vector database — schema evolution is a small hand-rolled routine (`app/migrations.py`), and embeddings are computed on the fly against an in-process model cache.

## 3. Project layout

```
backend/
├── app/
│   ├── main.py              # FastAPI app, CORS, router registration, scheduler lifespan, /api/health
│   ├── config.py             # Settings (env-driven), cached with lru_cache
│   ├── db.py                 # SQLAlchemy engine / session / Base
│   ├── models.py             # ORM models: Workspace, Job, JobBlockedQuestion, Batch, BatchRun, Setting
│   ├── schemas.py             # Pydantic request/response DTOs
│   ├── migrations.py          # Idempotent, Alembic-free schema evolution
│   ├── scheduler.py            # Batch orchestration, auto-apply decision gate, answer-lookup tier chain
│   ├── crawler.py              # Playwright scraper for HiringCafe's rendered search results
│   ├── discovery.py            # crawl → extract → score → upsert Job rows, per workspace
│   ├── extractor.py             # Normalizes a raw HiringCafe payload; detects ATS platform from apply URL
│   ├── scorer.py                 # Résumé/JD matching → 0–100 score
│   ├── stats.py                  # Per-workspace and cross-workspace aggregate stats
│   ├── export.py                  # Builds a jobs .xlsx workbook
│   ├── sheet_import.py             # Parses an uploaded .xlsx/.csv into Job rows for an upload batch
│   ├── blocked_questions.py         # Persists unresolved application questions for human review
│   ├── embeddings.py                 # Shared cached sentence-transformer wrapper
│   ├── llm_drafting.py                # Local-LLM answer drafting (grounded + educated-guess tiers)
│   ├── resume_rag.py                   # Résumé chunking + retrieval for grounded LLM drafting
│   ├── apply_adapters/                  # The auto-apply engine — see §8
│   │   ├── engine.py                     # attempt_apply / fill_and_submit orchestration
│   │   ├── fields.py                      # Field discovery, labeling, filling (~1.5k lines)
│   │   ├── platforms.py                    # Per-ATS tuning (Greenhouse, Lever, Ashby, Workday exclusion, …)
│   │   ├── submit.py                        # Submit-button detection + post-submit response monitoring
│   │   ├── reveal.py                         # Clicks through "Apply" gate pages before the form exists
│   │   ├── profile_similarity.py              # Tier B: embedding match against the applicant profile
│   │   └── types.py                            # Shared dataclasses (QuestionDescriptor, AnswerLookup, …)
│   └── routes/                                  # One router per resource — see §7
├── assisted_apply.py           # Standalone, fully manual script — opens jobs in a visible browser, human submits
├── tests/                        # pytest suite + local ATS HTML fixtures (see §10)
├── requirements.txt               # Runtime dependencies
├── requirements-dev.txt            # + ruff, pytest
├── .env.example                     # Sample configuration (see §6 for the full variable list)
└── DEBUGGING.md                       # How to trace a real or fixture-driven auto-apply run
```

## 4. Setup

Requirements: **Python 3.11+**, plus a working internet connection for Playwright's first browser download and the embedding model's first download (~400 MB, cached afterward).

```bash
# from the repo root
python3.11 -m venv .venv
source .venv/bin/activate

python -m pip install -r backend/requirements-dev.txt   # or requirements.txt for a runtime-only install
python -m playwright install chromium                     # Playwright's browser binary isn't pulled in by pip

cp backend/.env.example backend/.env                        # then edit as needed, see §6
```

No manual database setup is required — `main.py` runs `run_migrations(engine)` automatically at startup, creating tables and applying incremental `ALTER TABLE`s idempotently against the SQLite file at `db_url`.

## 5. Running the service

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Or start both backend and frontend together from the repo root with `./run.sh`.

- API root: `http://localhost:8000`
- Interactive docs (Swagger UI): `http://localhost:8000/docs`
- Health check: `GET /api/health` → `{"status": "ok", "app": "Meridian"}`

The batch **scheduler runs in-process** — it starts and stops with the FastAPI app's lifespan (an `APScheduler` `BackgroundScheduler`, pinned to UTC). There is no separate worker process or external cron; batches only fire while this process is running. On startup it reloads every `active` batch from the database and marks any one-off batch that was missed by more than a 5-minute misfire window as `completed` rather than silently firing late.

To enable the optional local-LLM answer-drafting tiers (§8), run [Ollama](https://ollama.com/) locally and set `LLM_ANSWER_DRAFTING_ENABLED=true` / `LLM_EDUCATED_GUESS_ENABLED=true` in `backend/.env`.

## 6. Configuration reference

All settings are read from `backend/.env` by `app/config.py` (`pydantic-settings`, unknown keys ignored). `backend/.env.example` currently ships only the first seven of these — the rest are newer additions worth adding to your own `.env` as needed:

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_QUERY` | `Software Engineer` | Default crawler search query. |
| `DEFAULT_DAYS` | `2` | Default "posted within N days" filter. |
| `SCORE_THRESHOLD` | `82` | Default "above threshold" match score for new workspaces. |
| `MODEL_NAME` | `sentence-transformers/all-mpnet-base-v2` | Embedding model for scoring and field matching. |
| `DB_URL` | `sqlite:///./meridian.db` | SQLAlchemy connection string. |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | Sole allowed CORS origin. |
| `CRAWLER_HEADLESS` | `true` | Headless vs. visible Chromium for both crawling and auto-apply. Set to `false` if HiringCafe or an ATS presents a Cloudflare/interactive challenge. |
| `AUTO_APPLY_THRESHOLD` | `95` | Default score gate for automated submission on new workspaces. |
| `CRAWLER_SLOW_MO_MS` | `0` | Artificial per-action delay (ms), for watching a live run. |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` to trace per-field fill/skip decisions during auto-apply. |
| `PROFILE_MATCH_THRESHOLD` | `0.60` | Minimum cosine similarity for Tier B (profile-field match) to accept an answer. |
| `PROFILE_MATCH_AMBIGUITY_MARGIN` | `0.05` | Minimum gap between the best and second-best Tier B match; below this, Tier B refuses rather than guesses. |
| `RESUME_RAG_TOP_K` | `3` | Number of résumé chunks retrieved per question for Tier C. |
| `RESUME_RAG_MATCH_THRESHOLD` | `0.35` | Minimum relevance score for a retrieved résumé chunk to be used by Tier C. |
| `LLM_ANSWER_DRAFTING_ENABLED` | `false` | Opt-in for Tier C — grounded LLM answers drafted strictly from résumé excerpts. |
| `LLM_EDUCATED_GUESS_ENABLED` | `false` | Separate opt-in for Tier D — permissive LLM answers for subjective/motivational questions. Not implied by the flag above. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama server address. |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model used for both LLM tiers. |

## 7. API reference

All routes are mounted under `/api`. Full request/response schemas are in `app/schemas.py` and browsable live at `/docs`.

**Workspaces** — `app/routes/workspaces.py`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/workspaces` | Create a workspace, seeded with the global default thresholds. |
| `GET` | `/api/workspaces` | List workspaces with job/applied/above-threshold counts. |
| `GET` | `/api/workspaces/{id}` | Fetch one workspace summary. |
| `PATCH` | `/api/workspaces/{id}` | Rename a workspace. |
| `DELETE` | `/api/workspaces/{id}` | Delete a workspace and cascade-delete its batches, batch runs, and jobs. |

**Settings** — `app/routes/settings.py` (`/api/workspaces/{id}/settings`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/settings` | Résumé text/filename, thresholds, ~25 applicant-profile fields, cover letter. |
| `POST` | `/settings/resume` | Save raw résumé text. |
| `POST` | `/settings/resume/pdf` | Upload a résumé PDF; text is extracted for scoring and the raw file is kept for auto-apply attachments. |
| `PUT` | `/settings/threshold` | Update the general match-score threshold. |
| `PUT` | `/settings/auto-apply-threshold` | Update the score gate for automated submission. |
| `PUT` | `/settings/profile` | Save the full applicant profile used to fill application forms. |

**Discovery** — `app/routes/scrape.py` (`/api/workspaces/{id}/scrape`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scrape` | Start an async discovery run (crawl → extract → score → store). 400 without a saved résumé, 409 if one is already running. |
| `GET` | `/scrape/status` | Poll live progress: `{running, collected, done, error, result}`. |

**Jobs** — `app/routes/jobs.py` (`/api/workspaces/{id}/jobs`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/jobs` | Filtered/sorted job list (`status`, `min_score`, `q`, `sort`, `order`, `auto_apply_state`, `batch_id`). |
| `GET` | `/jobs/export` | Download the workspace's jobs as an `.xlsx` workbook. |
| `GET` | `/jobs/{job_id}` | Fetch one job. |
| `PATCH` | `/jobs/{job_id}` | Manually change a job's status; clears any pending auto-apply review state. |
| `POST` | `/jobs/{job_id}/retry-apply` | Re-run a real automated apply attempt for a job stuck in `needs_review`. |
| `DELETE` | `/jobs/{job_id}` | Delete a job and its blocked questions. |
| `GET` | `/jobs/{job_id}/blocked-questions` | List a job's unresolved application questions. |
| `POST` | `/jobs/{job_id}/blocked-questions/{bq_id}/answer` | Human-approve an answer to a blocked question. |
| `POST` | `/jobs/{job_id}/blocked-questions/{bq_id}/dismiss` | Dismiss a blocked question without answering it. |

**Batches** — `app/routes/batches.py` (`/api/batches`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/batches` | Create a recurring or one-off search-based discovery+auto-apply schedule. |
| `POST` | `/batches/upload` | Create a one-shot batch from an uploaded spreadsheet of jobs. |
| `GET` | `/batches` | List all batches across all workspaces. |
| `GET` | `/batches/{id}` | Fetch one batch. |
| `PATCH` | `/batches/{id}` | Activate or pause a batch. |
| `DELETE` | `/batches/{id}` | Delete a batch and its run history. |
| `GET` | `/batches/{id}/runs` | Run history for a batch. |
| `POST` | `/batches/{id}/run-now` | Trigger a batch run immediately. |

**Stats & dashboard**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/workspaces/{id}/stats` | Per-workspace totals, score histogram, by-ATS breakdown, applications over time. |
| `GET` | `/api/dashboard` | Cross-workspace aggregate summary. |

## 8. Automated apply: what actually happens

This is the part of the backend most worth reading carefully before you use it.

### It really does submit real applications

For a batch run, or a "Retry auto-apply" request, Meridian will **launch a real Chromium browser, navigate to the real `apply_url`, fill in the actual form fields, and click the real submit button — with no confirmation step** — whenever *all* of the following hold:

- the job's score is at or above the workspace's/batch's `auto_apply_threshold`;
- the ATS is not Workday (excluded outright — see below);
- the workspace has an uploaded résumé **file** (not just pasted résumé text);
- every required field on the form can be resolved with confidence (see the tier chain below).

This is intentional — it's what the batch scheduler exists to do. If you only want to review-and-click-apply yourself, keep `auto_apply_threshold` above any score you expect to reach, or use `assisted_apply.py` (§1) instead of batches.

### Where forms are filled: `app/apply_adapters/`

A single, ATS-agnostic engine (`engine.py`, `fields.py`, `submit.py`) matches form fields by their accessible label text rather than hardcoded element IDs, so an ATS with no dedicated tuning still gets full support through a generic fallback. `platforms.py` adds light per-platform tuning for Greenhouse, Lever, Ashby, Workable, BambooHR, iCIMS, Jobvite, SmartRecruiters, and Recruitee (extra label aliases, platform-specific quirks). **Workday is excluded outright** (`review_reason: unsupported_multi_step`) because it requires a multi-page account-creation flow before any form field is reachable.

A single attempt, in order:

1. Refuse immediately if the ATS is Workday or no résumé file is attached.
2. Navigate to the posting; if the form isn't in the DOM yet, click through an "Apply" gate page (`reveal.py`).
3. Refuse if a **visible** CAPTCHA is present (`captcha_protected`); an invisible reCAPTCHA badge (common on Greenhouse) is allowed to proceed.
4. **Tier A** — fill every field that matches a known applicant-profile alias (name, contact info, location, work authorization Yes/No, EEO fields, cover letter), across native inputs, `<select>`, and react-select-style comboboxes.
5. Attach the résumé file to the form's file input.
6. Enumerate every remaining required, unfilled field and resolve each one through the **answer-lookup tier chain** below.
7. **If any required field is still unresolved after every tier, the entire attempt aborts (`custom_questions`) and the form is never submitted** — it's all-or-nothing, never a partial submit.
8. Re-validate every filled value against the page's own `:invalid` state (retrying up to 3 times) before allowing a submit click.
9. Click the submit control (ranked by accessible name; refuses on a genuine tie between candidates), then inspect the actual network response and on-page confirmation to determine the real outcome — success, a rejected/still-invalid form, an expired listing, or an undetected confirmation.

### The answer-lookup tier chain

Tried in order for each field Tier A didn't already fill:

| Tier | Source | Behavior |
|---|---|---|
| **0** | Human-approved answer | An answer you previously approved for *this exact job* on a prior attempt. |
| **B** | Profile similarity (`profile_similarity.py`) | Embeds the question label and matches it against your typed applicant-profile fields. Requires a confident top match (`PROFILE_MATCH_THRESHOLD`) with a clear margin over the runner-up (`PROFILE_MATCH_AMBIGUITY_MARGIN`); for select/radio fields the matched value must also be one of the form's live options. This is "answering with your own stated data," not a guess. |
| **C** | Grounded LLM (`resume_rag.py` + `llm_drafting.py`) | Only if `LLM_ANSWER_DRAFTING_ENABLED=true`. Retrieves the most relevant résumé excerpts and asks a local Ollama model to answer strictly from that context, or return nothing if it can't. |
| **D** | Educated-guess LLM (`llm_drafting.py`) | Only if `LLM_EDUCATED_GUESS_ENABLED=true`. A deliberately more permissive drafter for subjective/motivational prose questions. Never fabricates a checkable fact, and for select/radio/checkbox fields is constrained to pick one of the form's real offered options. |

**Hard exclusions that apply regardless of settings:** EEO self-identification questions and legal/compliance-sensitive questions (work authorization, visa sponsorship, security clearance, background/drug-test consent, criminal history, citizenship) are **never** answered by either LLM tier — only Tier 0 or Tier B (i.e., your own explicit prior approval or your own typed profile value) can resolve them.

By default (`LLM_ANSWER_DRAFTING_ENABLED=false`, `LLM_EDUCATED_GUESS_ENABLED=false`), only Tiers 0 and B are active, so out of the box no AI-authored text is ever submitted unreviewed — only values you've explicitly provided.

There is also a UI-only suggestion path (`llm_drafting.draft_pending_drafts_for_job`) that pre-fills a *draft* answer for a job already sitting in the review queue, shown to the human in the frontend's blocked-questions panel for editing before approval. That suggestion is never itself submitted automatically.

### What still requires you

- Any required field the tier chain can't resolve routes the whole job to `needs_review` with a specific `review_reason` (e.g. `custom_questions`, `captcha_protected`, `submission_rejected`, `unsupported_multi_step`, `no_resume_file`) and the unresolved questions are shown in the frontend for you to answer or dismiss. Approving an answer there and clicking "Retry auto-apply" re-attempts the exact same job with the threshold check bypassed.
- Manually setting a job's status via `PATCH /jobs/{id}` (e.g. `applied` or `skipped`) always clears any pending auto-apply state — that's how you opt a specific job out of automation.
- `assisted_apply.py` is a separate, fully manual script (not wired into the API or scheduler) that opens each `to_apply` job in a visible browser and waits for you to review and submit it yourself, one at a time.

## 9. Data model

Key tables (`app/models.py`, SQLAlchemy 2.0):

- **`Workspace`** — résumé text and raw résumé file, score/auto-apply thresholds, and the full applicant profile (contact info, location, work eligibility, EEO self-identification, compliance-sensitive fields, cover letter).
- **`Job`** — one discovered posting, unique per `(workspace_id, external_id)`; scoring fields (`score`, coverage breakdowns, matched/missing skills); pipeline `status`; and auto-apply diagnostics (`auto_apply_state`, `review_reason`, timestamps, sanitized `last_apply_detail`, and links back to the `Batch`/`BatchRun` that produced it).
- **`JobBlockedQuestion`** — one unresolved required question for one specific job (not a reusable, workspace-wide answer bank), including any LLM-drafted suggestion and the human's approved answer.
- **`Batch`** — a recurring or one-off discovery+apply schedule: search filters or an uploaded-file source, interval/repeat configuration, its own `auto_apply_threshold`, and status.
- **`BatchRun`** — one execution's log: counts fetched/new/updated/auto-applied/needs-review, and any error.

## 10. Testing

```bash
cd backend
pytest tests/ -v
```

The suite (111 tests across 10 files) drives the auto-apply engine against **local static HTML fixtures** (`tests/fixtures/`, ~26 files reproducing real ATS DOM quirks — Ashby's malformed `label[for]` patterns, Greenhouse's react-select comboboxes, CAPTCHA variants, checkbox groups) rather than live job postings, so it never touches the network or risks a real submission. LLM-drafting tests mock `httpx.post`, so no local Ollama server is required to run the suite. Some tests use a real headless Chromium via shared fixtures in `conftest.py`, so Playwright's browser must be installed first:

```bash
python -m playwright install chromium
```

`requirements-dev.txt` adds `ruff` and `pytest` on top of the runtime dependencies.

## 11. Debugging a batch run

See [DEBUGGING.md](DEBUGGING.md) for two workflows: watching a real batch run against live job URLs with `CRAWLER_HEADLESS=false`, `CRAWLER_SLOW_MO_MS`, and `LOG_LEVEL=DEBUG`; and stepping through the field-filling logic one action at a time against a local fixture with `PWDEBUG=1 pytest tests/test_manual_walkthrough.py -s -v`.

## 12. CI

`.github/workflows/ci.yml` runs on every PR/push to `main`:

- **`backend-lint`** — `ruff check backend/ --select E4,E7,E9,F`
- **`backend-tests`** — installs `requirements-dev.txt`, installs Playwright's Chromium (`playwright install --with-deps chromium`), runs `pytest tests/ -v`
- **`secret-scan`** — `gitleaks` across the whole repository

Ruff formatting rules (line length 100, double quotes) are configured in the repo-root `pyproject.toml`.
