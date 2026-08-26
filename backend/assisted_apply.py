"""Manual-only helper: opens queued jobs and waits for the user between each one."""

from playwright.sync_api import sync_playwright
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job, JobStatus


def main():
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.status == JobStatus.to_apply)).all())
    if not jobs:
        print("Meridian: no jobs marked To apply.")
        return
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        for job in jobs:
            print(f"Opening {job.title} at {job.company}")
            page.goto(job.apply_url)
            input("Review and submit manually, then press Enter for the next job...")
        browser.close()


if __name__ == "__main__":
    main()
