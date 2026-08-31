from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    default_query: str = "Software Engineer"
    default_days: int = 2
    score_threshold: float = 82
    auto_apply_threshold: float = 95
    model_name: str = "sentence-transformers/all-mpnet-base-v2"
    db_url: str = "sqlite:///./meridian.db"
    frontend_origin: str = "http://localhost:5173"
    crawler_headless: bool = True
    # Milliseconds of artificial delay Playwright inserts between actions — 0 (off)
    # by default. Set e.g. CRAWLER_SLOW_MO_MS=250 alongside CRAWLER_HEADLESS=false to
    # visually follow a real auto-apply attempt field-by-field instead of it
    # happening too fast to see.
    crawler_slow_mo_ms: int = 0
    # Standard logging level name (DEBUG/INFO/WARNING/...). DEBUG surfaces per-field
    # fill/skip tracing from apply_adapters — see fields.py/engine.py — useful for
    # diagnosing why a specific field was or wasn't filled without opening a browser.
    log_level: str = "INFO"
    # Tier-B profile-similarity matching (see apply_adapters/profile_similarity.py)
    # — starting defaults with no labeled tuning data yet; both tunable without a
    # redeploy. Threshold is lower than the old bank matcher's 0.80 default: this
    # corpus is a small, fixed set of generic field-concept phrasings (see
    # fields.py's alias tables), not literal previously-seen questions, so a
    # genuine match naturally scores a bit lower even when it's correct.
    profile_match_threshold: float = 0.60
    profile_match_ambiguity_margin: float = 0.05
    # Tier-C résumé RAG (see resume_rag.py) — same reasoning on the threshold as
    # above: retrieval is comparing a question against arbitrary résumé prose, not
    # a curated alias list, so it's tuned looser again.
    resume_rag_top_k: int = 3
    resume_rag_match_threshold: float = 0.35
    # Tier-C/grounded LLM-drafted answers (see llm_drafting.py), via a locally-
    # running Ollama instance — free, no API key/budget needed. Off by default so a
    # fresh clone never silently calls out to a local service that may not be
    # running.
    llm_answer_drafting_enabled: bool = False
    # Tier D — llm_drafting.draft_educated_guess. A materially bigger risk decision
    # than the grounded tiers above (see that function's own docstring), so it's a
    # separate opt-in rather than folded into llm_answer_drafting_enabled — turning
    # on grounded drafting must never silently turn this on too.
    llm_educated_guess_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
