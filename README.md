# Meridian

Meridian is a local, single-user job discovery and application-tracking dashboard. It drives HiringCafe's rendered site in Chromium, extracts original ATS links, scores each description against your resume, and highlights the gaps worth addressing before you apply. It does not call HiringCafe's private API directly.

Applications are deliberately manual. Meridian opens the original posting and tracks your status; it never mass-submits forms.

## Requirements

- Python 3.11+
- Node.js 18+
- About 400 MB of free disk space for the first sentence-transformer model download

## Install and run

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
playwright install chromium
cp backend/.env.example backend/.env
cd frontend && npm install && cd ..
chmod +x run.sh
./run.sh
```

Open `http://localhost:5173`. The API and interactive docs run at `http://localhost:8000/docs`.

The default SQLite database is created inside `backend/`. On the first discovery, sentence-transformers downloads `all-mpnet-base-v2` (roughly 400 MB), so scoring begins more slowly once. Later runs reuse the local model cache.

## Workflow

1. Paste your resume as plain text and click **Save resume**, or upload a text-based PDF. PDF text is extracted locally and shown in the editor for review. Scanned image-only PDFs require OCR first.
2. Adjust the match threshold, query, date range, and maximum number of jobs.
3. Click **Discover jobs**. The backend launches headless Chromium, loads the rendered search, scrolls at polite 1.5-second intervals, captures the jobs the page renders, scores them, and deduplicates by external ID. The dashboard polls live crawler progress while it runs.
4. Expand a job to inspect missing skills and weakly supported requirements, open its original posting, apply yourself, and update its status.

The match score is a local heuristic combining requirement coverage, hard-skill overlap, and overall semantic similarity. It is not an industry ATS score. Calibrate the threshold using several roles you already consider good and poor fits rather than treating the default of 82 as universal.

If Cloudflare challenges the crawler, set `CRAWLER_HEADLESS=false` in `backend/.env`, restart Meridian, and complete the one-time challenge in the visible Chromium window. Empty searches are handled normally; broaden the query or date range.

## Manual assisted apply (optional)

Install Playwright's browser once:

```bash
source .venv/bin/activate
playwright install chromium
cd backend
python assisted_apply.py
```

This opens jobs marked `to_apply` in a visible browser and pauses after every page. You review, complete, and submit every application yourself. It does not fill or submit forms.

## Configuration

Copy `backend/.env.example` to `backend/.env`. Available values are `DEFAULT_QUERY`, `DEFAULT_DAYS`, `SCORE_THRESHOLD`, `MODEL_NAME`, `DB_URL`, and `FRONTEND_ORIGIN`.
