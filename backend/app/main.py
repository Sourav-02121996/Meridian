import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import scheduler
from .config import get_settings
from .db import engine
from .migrations import run_migrations
from .routes import batches, dashboard, jobs, scrape, settings, stats, workspaces

logging.basicConfig(
    level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
    format="%(asctime)s | Meridian | %(levelname)s | %(message)s",
)
run_migrations(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Meridian", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(workspaces.router)
app.include_router(settings.router)
app.include_router(jobs.router)
app.include_router(scrape.router)
app.include_router(stats.router)
app.include_router(dashboard.router)
app.include_router(batches.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Meridian"}
