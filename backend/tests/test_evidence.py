"""Evidence report tests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analysis.evidence import generate_evidence_report
from app.config import Settings
from app.db.models import (
    BacktestResult,
    BookSnapshot,
    City,
    EnsembleMember,
    Event,
    ForecastSnapshot,
    Market,
    MarketPriceSnapshot,
    Resolution,
)
from app.main import app


def _city(slug: str, now: datetime, *, needs_review: bool = False) -> City:
    return City(
        slug=slug,
        name=slug.replace("-", " ").title(),
        series_slug=f"{slug}-daily-weather",
        station_code="RKSI",
        station_name=None,
        latitude=37.4602,
        longitude=126.4407,
        timezone="Asia/Seoul",
        unit="C",
        resolution_source="wunderground",
        resolution_url=None,
        rounding="round",
        needs_review=needs_review,
        active=True,
        updated_at=now,
    )


def _event(
    event_id: str,
    slug: str,
    target_date: date,
    now: datetime,
    *,
    active: bool,
    closed: bool,
) -> Event:
    return Event(
        id=event_id,
        slug=slug,
        title=f"Highest temperature in Seoul on {target_date.isoformat()}?",
        city_slug="seoul",
        target_date=target_date,
        end_date=datetime(2026, 7, 1, 12, tzinfo=UTC),
        neg_risk_market_id=None,
        active=active,
        closed=closed,
        volume=None,
        liquidity=None,
        first_seen_at=now,
        updated_at=now,
    )


def _market(market_id: str, event_id: str, now: datetime, *, winner: bool | None) -> Market:
    return Market(
        id=market_id,
        event_id=event_id,
        condition_id=f"0x{market_id}",
        question="Will it be 25C?",
        group_item_title="25C",
        group_item_threshold=0,
        bucket_kind="exact",
        bucket_low=Decimal("25"),
        bucket_high=Decimal("25"),
        yes_token_id=f"yes-{market_id}",
        no_token_id=f"no-{market_id}",
        tick_size=Decimal("0.001"),
        min_order_size=Decimal("5"),
        closed=winner is not None,
        winner=winner,
        resolved_at=now if winner is not None else None,
        updated_at=now,
    )


async def test_evidence_report_fails_ensemble_gate_when_members_are_zero(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 10, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                _city("seoul", now),
                _city("tokyo", now),
                _city("hong-kong", now),
                _event(
                    "event-active",
                    "seoul-active",
                    date(2026, 6, 20),
                    now,
                    active=True,
                    closed=False,
                ),
                _market("market-active", "event-active", now, winner=None),
            ]
        )

    report = await generate_evidence_report(
        session_factory,
        Settings(),
        cities=["seoul", "tokyo", "hong-kong"],
        now=now,
    )
    gates = json.loads(report.gates_json)

    assert report.status == "COLLECTING"
    assert gates["ensemble_members"]["passed"] is False
    assert gates["ensemble_members"]["value"] == 0


async def test_evidence_report_passes_gates_with_forward_replay_and_city_quality(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                _city("seoul", now),
                _city("tokyo", now),
                _city("hong-kong", now),
                _event(
                    "event-active",
                    "seoul-active",
                    date(2026, 6, 30),
                    now,
                    active=True,
                    closed=False,
                ),
                _event(
                    "event-resolved",
                    "seoul-resolved",
                    date(2026, 6, 1),
                    now,
                    active=False,
                    closed=True,
                ),
                _market("market-active", "event-active", now, winner=None),
                _market("market-resolved", "event-resolved", now, winner=True),
                MarketPriceSnapshot(
                    ts=datetime(2026, 6, 1, 10, tzinfo=UTC),
                    market_id="market-resolved",
                    best_bid=Decimal("0.19"),
                    best_ask=Decimal("0.20"),
                    mid=Decimal("0.195"),
                    bid_size=Decimal("100"),
                    ask_size=Decimal("100"),
                ),
                MarketPriceSnapshot(
                    ts=datetime(2026, 6, 30, 10, tzinfo=UTC),
                    market_id="market-active",
                    best_bid=Decimal("0.19"),
                    best_ask=Decimal("0.20"),
                    mid=Decimal("0.195"),
                    bid_size=Decimal("100"),
                    ask_size=Decimal("100"),
                ),
                BookSnapshot(
                    ts=now,
                    token_id="yes-market-active",
                    bids_json='[["0.19","100"]]',
                    asks_json='[["0.20","100"]]',
                ),
                Resolution(
                    event_id="event-resolved",
                    winner_market_id="market-resolved",
                    winner_bucket="25C",
                    resolved_at=now,
                ),
                BacktestResult(
                    run_at=now,
                    profile="max_edge",
                    n_trades=50,
                    n_wins=30,
                    total_staked=Decimal("100.00"),
                    total_pnl=Decimal("10.00"),
                    win_rate=0.6,
                    profit_factor=1.5,
                    max_drawdown=Decimal("8.00"),
                    params_json=json.dumps(
                        {
                            "source": "replay_price_snapshots",
                            "execution_proxy": "best_ask_taker_no_depth_slippage",
                            "n_resolved_trades": 50,
                            "brier_model": 0.12,
                            "brier_market": 0.18,
                            "brier_delta": 0.06,
                            "roi": "0.1000",
                            "max_loss_streak": 3,
                        }
                    ),
                ),
            ]
        )
        snapshot = ForecastSnapshot(
            fetched_at=now,
            city_slug="seoul",
            source="open_meteo_ensemble",
            model="gfs",
            target_date=date(2026, 6, 30),
            lead_days=0,
            tmax_c=None,
            n_members=2,
        )
        session.add(snapshot)
        await session.flush()
        session.add_all(
            [
                EnsembleMember(snapshot_id=snapshot.id, member=0, tmax_c=25.0),
                EnsembleMember(snapshot_id=snapshot.id, member=1, tmax_c=26.0),
            ]
        )

    report = await generate_evidence_report(
        session_factory,
        Settings(),
        cities=["seoul", "tokyo", "hong-kong"],
        now=now,
    )
    data_health = json.loads(report.data_health_json)
    gates = json.loads(report.gates_json)

    assert report.status == "PROMISING"
    assert data_health["price_snapshots"] == 2
    assert data_health["book_snapshots"] == 1
    assert data_health["ensemble_members"] == 2
    assert data_health["resolutions"] == 1
    assert all(gate["passed"] for gate in gates.values())


async def test_evidence_endpoint_returns_latest_and_history(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all([_city("seoul", now), _city("tokyo", now), _city("hong-kong", now)])
        session.add(
            BacktestResult(
                run_at=now,
                profile="max_edge",
                n_trades=0,
                n_wins=0,
                total_staked=Decimal("0.00"),
                total_pnl=Decimal("0.00"),
                win_rate=0.0,
                profit_factor=None,
                max_drawdown=Decimal("0.00"),
                params_json=json.dumps({"source": "replay_price_snapshots"}),
            )
        )

    await generate_evidence_report(
        session_factory,
        Settings(),
        cities=["seoul", "tokyo", "hong-kong"],
        now=now,
    )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["run_at"] == "2026-06-30T00:00:00Z"
    assert body["latest"]["status"] == "COLLECTING"
    assert len(body["history"]) == 1
    trading = json.loads(body["latest"]["trading_json"])
    assert trading["profiles"]["max_edge"]["total_pnl"] == "0.00"
