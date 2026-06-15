"""FastAPI app, lifespan and shared runtime resources."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analysis, cities, events, health, markets, signals
from app.collectors.scheduler import build_scheduler
from app.config import get_settings
from app.db.models import Base
from app.db.session import create_engine, create_session_factory
from app.polymarket.client import PolymarketPublicClient
from app.weather.metar import MetarClient
from app.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    http = httpx.AsyncClient(timeout=30.0)
    pm_client = PolymarketPublicClient(http)
    om_client = OpenMeteoClient(http)
    metar_client = MetarClient(http)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.http = http
    app.state.polymarket = pm_client
    app.state.open_meteo = om_client
    app.state.metar = metar_client

    scheduler = None
    if settings.collectors_enabled:
        scheduler = build_scheduler(session_factory, pm_client, om_client, metar_client, settings)
        scheduler.start()
        logger.info("collectors started")
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await pm_client.aclose()
        await http.aclose()
        await engine.dispose()


app = FastAPI(title="Weather Bot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(cities.router)
app.include_router(markets.router)
app.include_router(events.router)
app.include_router(signals.router)
app.include_router(analysis.router)
