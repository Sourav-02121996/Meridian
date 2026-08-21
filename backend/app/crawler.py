import argparse
import json
import logging
from typing import Any, Callable
from urllib.parse import quote

from bs4 import BeautifulSoup
from playwright.sync_api import Response, TimeoutError as PlaywrightTimeoutError, sync_playwright

from .config import get_settings
log = logging.getLogger("hirelight.crawler")


class ScrapeError(RuntimeError):
    pass


def build_search_state(query: str, days: int) -> dict[str, Any]:
    return {
        "searchQuery": query,
        "dateFetchedPastNDays": days,
        "sortBy": "date",
        "departments": ["Engineering", "Software Development", "Information Technology"],
        "seniorityLevel": ["Entry Level", "Mid Level", "No Prior Experience Required"],
    }


def _items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    hits = data.get("hits", {}).get("hits") if isinstance(data.get("hits"), dict) else None
    if isinstance(hits, list):
        return [item.get("_source", item) for item in hits if isinstance(item, dict)]
    for key in ("results", "jobs", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        nested = _items(value)
        if nested:
            return nested
    return []


def _ssr_items(html: str) -> list[dict]:
    script = BeautifulSoup(html, "html.parser").find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    try:
        hits = json.loads(script.string)["props"]["pageProps"].get("ssrHits", [])
        return [item for item in hits if isinstance(item, dict)]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def crawl(query: str, days: int, departments=None, seniority=None, target: int = 100,
          headed: bool | None = None, progress: Callable[[int], None] | None = None) -> list[dict]:
    state = build_search_state(query, days)
    if departments is not None:
        state["departments"] = departments
    if seniority is not None:
        state["seniorityLevel"] = seniority
    url = f"https://hiringcafe.com/?searchState={quote(json.dumps(state, separators=(',', ':')))}"
    collected: dict[str, dict] = {}

    def add(items):
        for item in items:
            if not isinstance(item, dict):
                continue
            identity = str(item.get("id") or item.get("objectID") or item.get("apply_url") or "")
            if identity:
                collected[identity] = item
        if progress:
            progress(min(len(collected), target))

    with sync_playwright() as playwright:
        use_headless = get_settings().crawler_headless if headed is None else not headed
        browser = playwright.chromium.launch(headless=use_headless)
        page = browser.new_page()

        def capture(response: Response):
            if "/api/search-jobs" not in response.url or response.request.method != "POST":
                return
            try:
                add(_items(response.json()))
            except Exception as exc:
                log.debug("Could not decode job response %s: %s", response.url, exc)

        page.on("response", capture)
        try:
            page.goto(url, wait_until="networkidle", timeout=60_000)
        except PlaywrightTimeoutError:
            log.warning("HiringCafe did not become network-idle; continuing with the rendered page")
        add(_ssr_items(page.content()))
        stagnant = 0
        while len(collected) < target and stagnant < 3:
            before = len(collected)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
            add(_ssr_items(page.content()))
            stagnant = stagnant + 1 if len(collected) == before else 0
        browser.close()

    if not collected:
        raise ScrapeError(
            "The browser collected zero jobs. Set CRAWLER_HEADLESS=false and retry so you can "
            "solve any one-time Cloudflare challenge in the visible window."
        )
    return list(collected.values())[:target]


def main():
    parser = argparse.ArgumentParser(description="Hirelight HiringCafe browser crawler")
    parser.add_argument("--query", default=get_settings().default_query)
    parser.add_argument("--days", type=int, default=get_settings().default_days)
    parser.add_argument("--target", type=int, default=120)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    jobs = crawl(args.query, args.days, target=args.target, headed=args.headed)
    print(f"Hirelight: collected {len(jobs)} jobs")


if __name__ == "__main__":
    main()
