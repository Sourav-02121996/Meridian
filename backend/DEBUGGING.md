# Debugging the auto-apply flow

Two different things usually need debugging, and they need different tools:

1. **"Did this specific real job get applied to, and why/why not?"** — a live batch
   run against real job URLs. See [Debugging a real batch run](#debugging-a-real-batch-run).
2. **"How exactly does the field-filling logic work?"** — best understood against a
   local fixture page, with nothing going out over the network. See
   [Watching the engine work step-by-step](#watching-the-engine-work-step-by-step).

## Debugging a real batch run

All of this is driven by `backend/.env` (create it if it doesn't exist yet — it's
read by `config.py`'s `Settings`, gitignored, and only applies to your own machine).

```env
CRAWLER_HEADLESS=false
CRAWLER_SLOW_MO_MS=250
LOG_LEVEL=DEBUG
```

- `CRAWLER_HEADLESS=false` makes `attempt_apply` (`apply_adapters/engine.py`) launch
  a visible Chromium window instead of a headless one, so you watch the real
  navigation/fill/submit happen live.
- `CRAWLER_SLOW_MO_MS=250` adds a delay between Playwright actions — without it,
  the fill happens fast enough that there's nothing to actually watch.
- `LOG_LEVEL=DEBUG` turns on per-field tracing already built into `engine.py`:
  which fields got filled (by id/name), which required fields were left over, and
  why each one wasn't resolved (no `answer_lookup` given, vs. no confident match
  in any tier). Every non-success outcome is also logged at INFO regardless of this
  setting (`engine.py::_give_up`), so the final `review_reason` is always visible.

Then restart the backend (`./run.sh`, or `uvicorn app.main:app --reload --port 8000`
from `backend/`) and trigger a run — either "Run now" on a batch in the UI, or
`POST /api/batches/{id}/run-now` directly. Logs print to whichever terminal is
running the backend process.

**After the run, inspect what actually happened** rather than guessing from the UI
alone:

```bash
# Every job the scheduler touched, and why:
sqlite3 backend/meridian.db \
  "SELECT id, title, auto_apply_state, review_reason FROM jobs WHERE last_batch_run_id = <run_id>;"

# For a custom_questions job, the actual blocked question text (this is the whole
# point of Tier B — it used to just be thrown away):
sqlite3 backend/meridian.db \
  "SELECT question_text, field_type, options, drafted_answer, status FROM job_blocked_questions WHERE job_id = <job_id>;"
```

Or the same thing via the API: `GET /api/workspaces/{workspace_id}/jobs?auto_apply_state=needs_review`
and `GET /api/workspaces/{workspace_id}/jobs/{job_id}/blocked-questions`.

`meridian.db`'s path comes from `config.py`'s `db_url` (`sqlite:///./meridian.db` by
default, relative to wherever the backend process's cwd is — `backend/` per `run.sh`).

## Watching the engine work step-by-step

`tests/test_manual_walkthrough.py` calls the exact same functions `attempt_apply`
does (`ensure_form_present`, `fill_known_fields`, `find_unhandled_required_fields`,
`_fill_answer`, `find_submit_control`) one at a time against the local fixture
pages in `tests/fixtures/`, printing what happened at each stage — instead of it
all happening silently inside one `fill_and_submit()` call.

Run it normally and it's just another passing test:

```bash
pytest tests/test_manual_walkthrough.py -v
```

Run it with output visible to read the narration without a browser:

```bash
pytest tests/test_manual_walkthrough.py -s -v
```

Run it with a real, visible, step-through browser — `PWDEBUG=1` is a
**Playwright-native** environment variable (nothing in this repo implements it): it
forces headed mode and attaches the Playwright Inspector, letting you click through
each action one at a time, on *any* Playwright script including this one:

```bash
PWDEBUG=1 pytest tests/test_manual_walkthrough.py -s -v
```

Don't set `PWDEBUG=1` on the actual backend/`run.sh` process — it pauses on every
single Playwright action waiting for a human to click "resume" in the Inspector,
which would hang a real scheduled batch run indefinitely. It's for the test file
above (or any one-off debug script you point directly at `sync_playwright()`),
never for the long-running server process. Use `CRAWLER_HEADLESS=false` +
`CRAWLER_SLOW_MO_MS` (previous section) for watching the real thing instead.

### Reproducing a specific company's form locally

If a specific real ATS form is misbehaving and you don't want to keep hitting the
live network (or risk actually submitting) to iterate on a fix:

1. Open the real application page, view source (or `page.content()` in a quick
   Playwright script), and save the relevant HTML into a new file under
   `tests/fixtures/`.
2. Point a copy of one of `test_manual_walkthrough.py`'s test functions (or
   `test_fill_and_submit.py`'s) at that fixture instead.
3. Iterate against the saved copy — deterministic, offline, and safe to run as many
   times as you want without ever submitting a real application.
