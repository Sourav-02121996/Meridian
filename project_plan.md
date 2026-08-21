# Codex Build Prompt — Hirelight (hiring.cafe Job Pipeline + Dashboard)

> Paste everything below the line into Codex / Copilot Agent / Claude Code in an empty VS Code workspace.
> It is written so an autonomous coding agent can scaffold, build, and run the whole thing.

---

Build a local, single-user web application that discovers software-engineering jobs from
hiring.cafe, scores how well my resume matches each job, and lets me track which ones to
apply to and which I've applied to — with a dashboard for statistics and match scores.

This is a personal job-search tool. It automates **discovery, extraction, and scoring**.
It does **NOT** auto-submit applications: applying is a human-in-the-loop action (open the
job's original page, apply myself, mark it applied). Do not build unattended mass submission —
it violates the target ATS platforms' terms and produces low-quality applications. An optional
"assisted apply" helper that merely opens a job URL in a browser and waits for me is fine.

## Project name
Name the project **Hirelight** (hire + highlight — it surfaces and highlights the best-fit roles).
Use `hirelight` as the repo/root folder name and the Python package name, and show "Hirelight"
as the app title in the frontend top bar, the README title, and any CLI banner/log header.

## Tech stack (use exactly this)
- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.x, SQLite, pydantic-settings, Uvicorn.
- **Excel export:** openpyxl (generate `.xlsx` downloads of the job list).
- **Discovery/crawling:** Playwright (Chromium). Drive the RENDERED hiring.cafe page in a
  real browser — this passes Cloudflare automatically (raw HTTP requests get 403/502-blocked).
  Also beautifulsoup4 for stripping HTML out of job descriptions.
- **Scoring:** sentence-transformers (model `sentence-transformers/all-mpnet-base-v2`), numpy.
- **Frontend:** Vite + React + TypeScript + Tailwind CSS + Recharts + TanStack Query (react-query).
- No auth, no cloud services — this runs on my machine.

## Project structure
```
hirelight/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, CORS, route registration
│   │   ├── config.py        # Settings from .env (query defaults, threshold, model name)
│   │   ├── db.py            # engine + session
│   │   ├── models.py        # SQLAlchemy models
│   │   ├── schemas.py       # Pydantic request/response models
│   │   ├── crawler.py       # Playwright browser crawler (renders page, captures job JSON)
│   │   ├── extractor.py     # apply-URL + description + ATS-name extraction
│   │   ├── scorer.py        # embedding match scorer
│   │   ├── stats.py         # aggregate statistics
│   │   ├── export.py        # build .xlsx of the job list (openpyxl)
│   │   └── routes/
│   │       ├── jobs.py
│   │       ├── scrape.py
│   │       ├── stats.py
│   │       ├── export.py
│   │       └── settings.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/            # Vite React TS app
├── README.md
└── run.sh              # starts backend + frontend together
```

## Discovery — browser crawler (implement in crawler.py)
Do NOT use raw HTTP requests against hiring.cafe: it's a JavaScript-rendered app behind
Cloudflare, so plain requests return an empty page or get 403/502-blocked. Instead, drive
the RENDERED page with **Playwright (Chromium)**. A real browser executes the page's JS and
passes Cloudflare automatically. The browser itself fetches the site's internal
`/api/search-jobs` endpoint to populate the page — the crawler's job is to load that page and
**capture the JSON responses the browser receives** (this is far more robust than scraping the
hashed-CSS job cards, and it includes the apply URL + full description per job).

Implement a `crawl(query, days, departments, seniority, target)` function that:
1. **Builds the search URL** from the filters by URL-encoding a `searchState` JSON object into
   `https://hiring.cafe/?searchState=<url-encoded-json>`. The `searchState` sets:
   `searchQuery`, `dateFetchedPastNDays`, `sortBy: "date"`,
   `departments: ["Engineering","Software Development","Information Technology"]`,
   `seniorityLevel: ["Entry Level","Mid Level","No Prior Experience Required"]`.
   (Encode with `urllib.parse.quote`. These are the only filters that matter; do not send a
   partial API payload — the URL carries the state and the site fills the rest.)
2. Launches Chromium (`headless=True` by default; expose a `headed` flag for Cloudflare trouble).
3. Registers a `page.on("response")` handler that, for any response whose URL contains
   `/api/search-jobs` and method is POST, calls `response.json()` and collects job objects from
   `data["hits"]["hits"][i]["_source"]` (fall back to `results`/`jobs`/`data` lists or a top-level
   list). Dedupe as they arrive.
4. `page.goto(search_url, wait_until="networkidle", timeout=60000)`, then **scroll to load more**:
   `page.mouse.wheel(0, 4000)` in a loop with a ~1.5s wait between scrolls (polite + lets the next
   batch load), until the collected count reaches `target` or the count stops growing for 3 rounds.
5. Returns the list of raw job objects; close the browser.

Also implement a small standalone `crawler.py` CLI (`python -m app.crawler --target 120 [--headed]`)
that runs `crawl` and prints the count — used for debugging outside the web app.

If, after loading, zero jobs were captured, raise a clear error telling the user to re-run with
the `headed` flag and solve any one-time Cloudflare challenge in the visible window.

## Extraction (extractor.py)
Job field names on hiring.cafe drift, so extract defensively:
- **apply_url:** search candidate keys (`apply_url`, `applyUrl`, `url`, `job_url`, `source_url`, `link`)
  recursively; fall back to the first ATS-looking URL found anywhere in the object (prefer domains
  containing greenhouse, lever.co, myworkdayjobs, ashbyhq, workable, bamboohr, icims, jobvite,
  smartrecruiters, recruitee over hiring.cafe internal links).
- **description:** candidate keys (`description`, `job_description`, `jobDescription`, `text`),
  then strip HTML with BeautifulSoup `.get_text()`.
- **title / company:** candidate keys (`title`,`job_title`,`core_job_title`) / (`company_name`,`companyName`,`company`).
- **ats_platform:** derive from the apply URL domain (greenhouse/lever/workday/ashby/…; else "career-page").
- **external_id:** the job's own id field if present, else a hash of (title+company+apply_url) for dedupe.

## Scoring (scorer.py) — accuracy is the priority
Return a dict, and persist all parts. Formula (0–100):
- **requirement_coverage (weight 0.55):** split the JD into requirement lines (prefer lines after a
  header matching requirements/qualifications/what you'll need/must have/skills/responsibilities;
  keep lines of 3–40 words). Embed each requirement and each resume line with the sentence-transformer;
  for each requirement take the MAX cosine similarity vs resume lines; average them.
- **skill_coverage (weight 0.35):** extract hard skills from the JD (a seed tech-skill set plus
  CamelCase/dotted/`C++`/`C#`-style tokens), check literal presence in the resume, coverage = matched/total.
- **global_similarity (weight 0.10):** doc-level cosine similarity of the first ~5k chars of each.
- `score = (0.55*req_cov + 0.35*skill_cov + 0.10*global) * 100`, rounded to 1 dp.
- Also return **matched_skills**, **missing_skills** (JD skills not in resume — the actionable list),
  and **weak_requirements** (requirements whose best resume match < 0.45).
- Cache the model with `lru_cache`; model name comes from config. Encode with `normalize_embeddings=True`.

## Data model (models.py)
`Job`: id (pk), external_id (unique), title, company, ats_platform, apply_url, description (text),
score (float), requirement_coverage, skill_coverage, global_similarity (floats),
matched_skills, missing_skills, weak_requirements (JSON columns),
status (enum: `discovered`, `to_apply`, `applied`, `skipped`; default `discovered`),
date_fetched, date_scored, date_applied (nullable datetimes), created_at, updated_at.
`Setting`: key (pk), value (text) — used to store the current resume text and the score threshold.
Upsert on `external_id` when scraping so re-runs update instead of duplicating.

## API (FastAPI)
- `POST /api/scrape` body `{query, days, max_jobs}` → run the Playwright crawler → extract → score
  against stored resume → upsert Jobs → return counts `{fetched, new, updated, above_threshold}`.
  Because the crawler drives a browser (seconds, not milliseconds), run it in a FastAPI
  BackgroundTask and expose `GET /api/scrape/status` (`{running, collected, done, error}`) that the
  frontend polls for live progress.
- `GET /api/jobs?status=&min_score=&sort=score|date&order=asc|desc&q=` → filtered, sorted list.
- `GET /api/jobs/{id}` → full job incl. description, missing_skills, weak_requirements.
- `PATCH /api/jobs/{id}` body `{status}` → update status; set date_applied when status→applied.
- `GET /api/stats` → `{total, by_status:{...}, above_threshold, avg_score, median_score,
  score_histogram:[{bucket,count}], by_ats:[{ats,count,avg_score}], applied_over_time:[{date,count}]}`.
- `POST /api/settings/resume` body `{text}` → store resume; `GET /api/settings` → resume + threshold.
- `PUT /api/settings/threshold` body `{value}`.
- `GET /api/jobs/export?status=&min_score=&q=` → build a `.xlsx` from the currently-filtered jobs
  (same filter params as `GET /api/jobs`, so the download matches what's shown in the table) and
  return it as a `StreamingResponse` with
  `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` and a
  `Content-Disposition: attachment; filename="hirelight_jobs_<YYYY-MM-DD>.xlsx"` header.
  Build the workbook in `export.py` with openpyxl: one "Jobs" sheet with a bold, frozen header row,
  auto-sized columns, and these columns — Score, Title, Company, ATS Platform, Status, Apply URL
  (as a clickable hyperlink), Missing Skills (comma-joined), Weak Requirements (semicolon-joined),
  Date Fetched, Date Applied. Sort rows by score descending.
- Enable CORS for the Vite dev origin.

## Frontend (Vite + React + TS + Tailwind + Recharts)
A single-page dashboard with a clean, modern look (cards, subtle shadows, a coherent color scheme —
not stock Bootstrap). Use TanStack Query for all data fetching. Sections:

1. **Top bar / controls:** resume textarea (saved via the settings endpoint), a threshold slider,
   and a "Discover jobs" form (query, days, max jobs) that calls `/api/scrape` and shows progress.
2. **Stat cards:** Total discovered · Above threshold · Applied · Avg score. Live from `/api/stats`.
3. **Charts:** (a) score-distribution histogram, (b) jobs-by-ATS bar chart with avg score,
   (c) status breakdown donut, (d) applications-over-time line.
4. **Job table (the core view):** sortable columns (Score, Title, Company, ATS, Status) plus an
   **Apply link** column with an "Open" button that opens the original posting's `apply_url` in a
   new tab. Rows above the current threshold are visually highlighted. Filters: status, min score,
   text search. Each row also has:
   - a colored score badge,
   - expandable detail showing **missing_skills** (chips) and **weak_requirements** (list) — the
     "what to fix before applying" panel,
   - buttons to mark **Applied** / **Skip** (PATCH status) with optimistic UI updates.
   Above the table, a **"Download Excel"** button that calls `GET /api/jobs/export` with the table's
   current filter params and triggers a file download (fetch the response as a blob and save it, e.g.
   via an object URL on a temporary `<a download>`; do not use a plain link so the active filters are
   included). Show a small hint that the export reflects the current filters.

## Optional assisted-apply helper (separate, off by default)
Add `backend/assisted_apply.py`: a Playwright script that reads jobs with status `to_apply`,
opens each `apply_url` in a **visible** (non-headless) browser, and waits for me to review and
submit manually before continuing to the next. No form auto-submission. Document it in the README
as manual-assist only.

## Non-functional requirements
- `.env.example` with `DEFAULT_QUERY`, `DEFAULT_DAYS`, `SCORE_THRESHOLD=82`, `MODEL_NAME`,
  `DB_URL`, `CRAWLER_HEADLESS=true`.
- `requirements.txt` (include `playwright` and `openpyxl`) and a working `frontend/package.json` with scripts.
- `run.sh` that starts uvicorn and the Vite dev server together.
- README: setup, `pip install -r requirements.txt`, **`playwright install chromium`** (required),
  first-run embedding-model download note (~400MB), how to set the resume, how to run discovery,
  the headed-mode fallback if Cloudflare challenges the crawler, and a clear note that applying is
  manual by design and that the score threshold must be calibrated against known good/bad jobs
  (it is a heuristic, not an industry ATS score).
- Handle Cloudflare challenges and empty results gracefully: if the crawler collects zero jobs,
  return an actionable message telling the user to re-run in headed mode.
- Keep the crawler polite: ~1.5s between scrolls, headless single browser instance per run.

## Acceptance criteria
1. `./run.sh` boots backend (`:8000`) and frontend (`:5173`) with no errors, and
   `python -m app.crawler --target 20` prints ~20 jobs on its own.
2. I can paste my resume, click "Discover jobs", watch live progress from the crawler, and then
   see ~100+ scored jobs appear in the table.
3. Each job shows its original ATS apply URL, a match score, missing skills, and weak requirements.
4. The dashboard stats and charts reflect the data and update when I mark jobs applied/skipped.
5. Re-running discovery updates existing jobs instead of duplicating them.
6. The crawler survives Cloudflare (works headless normally; headed mode recovers if challenged).
7. Clicking "Download Excel" downloads a valid `.xlsx` of the currently-filtered jobs, with a
   clickable apply-link column, that opens cleanly in Excel / Google Sheets / LibreOffice.

Build it end to end, then print the exact commands to install and run.