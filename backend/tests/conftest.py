import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="module")
def browser():
    """One browser instance per test module — launch cost is amortized across every
    test in a file rather than paid per test."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    page = browser.new_page()
    yield page
    page.close()
