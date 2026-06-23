"""Paper execution engine tests."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    BookSnapshot,
    City,
    Event,
    Market,
    MarketPriceSnapshot,
    PaperEquitySnapshot,
    PaperFill,
    PaperOrder,
    PaperPosition,
    Signal,
)
from app.execution.paper import PaperEngine, settle_resolved_positions, taker_fee


def _city(now: datetime, *, slug: str = "seoul", needs_review: bool = False) -> City:
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
    now: datetime,
    *,
    event_id: str = "event-1",
    city_slug: str = "seoul",
    closed: bool = False,
) -> Event:
    return Event(
        id=event_id,
        slug=f"highest-temperature-in-{city_slug}-on-june-10-2026",
        title="Highest temperature in Seoul on June 10, 2026?",
        city_slug=city_slug,
        target_date=date(2026, 6, 10),
        end_date=datetime(2026, 6, 11, 12, tzinfo=UTC),
        neg_risk_market_id=None,
        active=not closed,
        closed=closed,
        volume=None,
        liquidity=None,
        first_seen_at=now,
        updated_at=now,
    )


def _market(
    now: datetime,
    *,
    market_id: str = "market-1",
    event_id: str = "event-1",
    token_id: str = "yes-token",
    closed: bool = False,
    winner: bool | None = None,
) -> Market:
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
        yes_token_id=token_id,
        no_token_id=f"no-{token_id}",
        tick_size=Decimal("0.001"),
        min_order_size=Decimal("5"),
        closed=closed,
        winner=winner,
        resolved_at=now if winner is not None else None,
        updated_at=now,
    )


def _signal(
    now: datetime,
    *,
    market_id: str = "market-1",
    token_id: str = "yes-token",
    stake: Decimal = Decimal("10"),
) -> Signal:
    return Signal(
        ts=now,
        market_id=market_id,
        token_id=token_id,
        side="BUY",
        profile="max_edge",
        model_prob=0.70,
        market_price=Decimal("0.20"),
        edge_gross=Decimal("0.50000"),
        edge_net=Decimal("0.49200"),
        stake=stake,
        status="PROPOSED",
        reason=None,
    )


def _book(
    now: datetime,
    asks: list[list[str]],
    *,
    token_id: str = "yes-token",
    ts: datetime | None = None,
) -> BookSnapshot:
    return BookSnapshot(
        ts=ts or now,
        token_id=token_id,
        bids_json=json.dumps([["0.19", "100"]]),
        asks_json=json.dumps(asks),
    )


async def _seed_base(
    session: AsyncSession,
    now: datetime,
    *,
    asks: list[list[str]],
    book_ts: datetime | None = None,
    market_closed: bool = False,
    city_needs_review: bool = False,
    prefix: str = "",
) -> Signal:
    city_slug = f"seoul-{prefix}" if prefix else "seoul"
    event_id = f"event-{prefix}" if prefix else "event-1"
    market_id = f"market-{prefix}" if prefix else "market-1"
    token_id = f"yes-token-{prefix}" if prefix else "yes-token"
    session.add(_city(now, slug=city_slug, needs_review=city_needs_review))
    session.add(_event(now, event_id=event_id, city_slug=city_slug, closed=market_closed))
    session.add(
        _market(
            now,
            market_id=market_id,
            event_id=event_id,
            token_id=token_id,
            closed=market_closed,
        )
    )
    session.add(_book(now, asks, token_id=token_id, ts=book_ts))
    session.add(
        MarketPriceSnapshot(
            ts=now,
            market_id=market_id,
            best_bid=Decimal("0.19"),
            best_ask=Decimal("0.20"),
            mid=Decimal("0.195"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        )
    )
    signal = _signal(now, market_id=market_id, token_id=token_id)
    session.add(signal)
    await session.flush()
    return signal


def test_taker_fee_matches_official_weather_formula() -> None:
    assert taker_fee(Decimal("0.20"), Decimal("1"), Decimal("0.05")) == Decimal("0.00800")
    assert taker_fee(Decimal("0.50"), Decimal("1"), Decimal("0.05")) == Decimal("0.01250")
    assert taker_fee(Decimal("0.95"), Decimal("1"), Decimal("0.05")) == Decimal("0.00238")


async def test_paper_buy_fak_consumes_multiple_book_levels(session: AsyncSession) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    signal = await _seed_base(
        session,
        now,
        asks=[["0.20", "5"], ["0.25", "100"]],
    )

    stats = await PaperEngine(Settings()).submit_signal(session, signal)
    fills = (await session.execute(select(PaperFill).order_by(PaperFill.price))).scalars().all()
    position = await session.get(PaperPosition, "yes-token")

    assert stats.orders == 1
    assert stats.fills == 2
    assert [fill.price for fill in fills] == [Decimal("0.20000"), Decimal("0.25000")]
    assert position is not None
    assert position.qty > Decimal("5")
    assert position.avg_cost > Decimal("0.20")


async def test_paper_buy_no_fills_against_no_token_book(session: AsyncSession) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    await _seed_base(
        session,
        now,
        asks=[["0.20", "100"]],
    )
    no_signal = _signal(
        now,
        market_id="market-1",
        token_id="no-yes-token",
        stake=Decimal("10"),
    )
    no_signal.market_price = Decimal("0.04")
    session.add(no_signal)
    session.add(_book(now, [["0.04", "100"]], token_id="no-yes-token"))
    await session.flush()

    stats = await PaperEngine(Settings()).submit_signal(session, no_signal)
    order = (
        await session.execute(select(PaperOrder).where(PaperOrder.signal_id == no_signal.id))
    ).scalar_one()
    position = await session.get(PaperPosition, "no-yes-token")
    equity = (
        await session.execute(
            select(PaperEquitySnapshot).order_by(PaperEquitySnapshot.ts.desc())
        )
    ).scalars().first()

    assert stats.orders == 1
    assert stats.fills == 1
    assert order.token_id == "no-yes-token"
    assert order.avg_fill_price == Decimal("0.04000")
    assert position is not None
    assert position.qty > Decimal("5")
    assert equity is not None
    assert equity.unrealized_pnl > Decimal("0")


async def test_paper_rejects_empty_stale_insufficient_and_closed_books(
    session_factory,
) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    cases = [
        ("empty_book", [], None, False, False),
        ("book_stale", [["0.20", "100"]], now - timedelta(hours=2), False, False),
        ("insufficient_liquidity", [["0.20", "1"]], None, False, False),
        ("market_closed", [["0.20", "100"]], None, True, False),
        ("city_needs_review", [["0.20", "100"]], None, False, True),
    ]
    for index, (reason, asks, book_ts, market_closed, city_needs_review) in enumerate(cases):
        async with session_factory() as session, session.begin():
            signal = await _seed_base(
                session,
                now,
                asks=asks,
                book_ts=book_ts,
                market_closed=market_closed,
                city_needs_review=city_needs_review,
                prefix=str(index),
            )
            stats = await PaperEngine(
                Settings(paper_book_stale_seconds=60)
            ).submit_signal(session, signal)
            assert stats.rejected == 1
            fill = (
                await session.execute(select(PaperFill).where(PaperFill.signal_id == signal.id))
            ).scalars().first()
            paper_order = (
                await session.execute(select(PaperOrder).where(PaperOrder.signal_id == signal.id))
            ).scalar_one()
            assert fill is None
            assert paper_order.reject_reason == reason


async def test_paper_settlement_moves_position_to_realized_pnl(session: AsyncSession) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    signal = await _seed_base(
        session,
        now,
        asks=[["0.20", "100"]],
    )
    await PaperEngine(Settings()).submit_signal(session, signal)
    market = await session.get(Market, "market-1")
    assert market is not None
    market.closed = True
    market.winner = True
    market.resolved_at = now
    await session.flush()

    stats = await settle_resolved_positions(session, Settings(), now=now)
    position = await session.get(PaperPosition, "yes-token")
    settlement_fills = (
        await session.execute(select(PaperFill).where(PaperFill.liquidity == "SETTLEMENT"))
    ).scalars().all()

    assert stats.settled == 1
    assert position is not None
    assert position.qty == Decimal("0")
    assert position.realized_pnl > Decimal("0")
    assert settlement_fills[0].price == Decimal("1")


async def test_paper_no_settlement_wins_when_yes_loses(session: AsyncSession) -> None:
    now = datetime(2026, 6, 10, 10, tzinfo=UTC)
    await _seed_base(
        session,
        now,
        asks=[["0.20", "100"]],
    )
    no_signal = _signal(now, token_id="no-yes-token", stake=Decimal("10"))
    no_signal.market_price = Decimal("0.04")
    session.add(no_signal)
    session.add(_book(now, [["0.04", "100"]], token_id="no-yes-token"))
    await session.flush()
    await PaperEngine(Settings()).submit_signal(session, no_signal)
    market = await session.get(Market, "market-1")
    assert market is not None
    market.closed = True
    market.winner = False
    market.resolved_at = now
    await session.flush()

    stats = await settle_resolved_positions(session, Settings(), now=now)
    position = await session.get(PaperPosition, "no-yes-token")
    settlement_fill = (
        await session.execute(
            select(PaperFill)
            .where(PaperFill.token_id == "no-yes-token", PaperFill.liquidity == "SETTLEMENT")
            .order_by(PaperFill.id)
        )
    ).scalars().one()

    assert stats.settled == 1
    assert position is not None
    assert position.qty == Decimal("0")
    assert position.realized_pnl > Decimal("0")
    assert settlement_fill.price == Decimal("1")
