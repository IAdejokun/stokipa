from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import engine
from app.jobs import scheduler as jobs
from app.routers.shop import router as shop_router
from app.routers.webhook import router as webhook_router
from app.whatsapp.client import wa

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", env=settings.ENV)
    jobs.start()
    yield
    jobs.stop()
    await wa.aclose()
    await engine.dispose()
    log.info("shutdown")


app = FastAPI(title="Stokipa", version="0.1.0", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(shop_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # public read-only catalog; webhook unaffected
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}