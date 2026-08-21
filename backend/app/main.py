import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import Base, engine
from .routes import jobs, scrape, settings, stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s | Hirelight | %(levelname)s | %(message)s")
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Hirelight", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[get_settings().frontend_origin], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(jobs.router)
app.include_router(scrape.router)
app.include_router(stats.router)
app.include_router(settings.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Hirelight"}
