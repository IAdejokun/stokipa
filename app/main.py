from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import settings
from app.db import engine

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", env=settings.ENV)
    yield
    await engine.dispose()
    log.info("shutdown")


app = FastAPI(title="Stokipa", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}