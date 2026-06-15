"""Paper-only execution against captured order books."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
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
from app.strategy.edge import FEE_PRECISION, cost_per_share

MONEY_PRECISION = Decimal("0.00001")
ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class PaperExecutionStats:
    orders: int = 0
    fills: int = 0
    rejected: int = 0
    settled: int = 0

    def __add__(self, other: "PaperExecutionStats") -> "PaperExecutionStats":
        return PaperExecutionStats(
            orders=self.orders + other.orders,
            fills=self.fills + other.fills,
            rejected=self.rejected + other.rejected,
            settled=self.settled + other.settled,
        )


@dataclass(frozen=True)
class _FillPlan:
    price: Decimal
    size: Decimal
    fee_paid: Decimal
    cash_delta: Decimal

    @property
    def trade_value(self) -> Decimal:
        return (self.price * self.size).quantize(MONEY_PRECISION)


def taker_fee(price: Decimal, size: Decimal, fee_rate: Decimal) -> Decimal:
    return (size * fee_rate * price * (ONE - price)).quantize(FEE_PRECISION)


def _parse_book_side(raw: str, *, reverse: bool) -> list[tuple[Decimal, Decimal]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    levels: list[tuple[Decimal, Decimal]] = []
    for level in parsed:
        if not isinstance(level, list | tuple) or len(level) < 2:
            continue
        price = Decimal(str(level[0]))
        size = Decimal(str(level[1]))
        if ZERO < price < ONE and size > ZERO:
            levels.append((price, size))
    return sorted(levels, key=lambda item: item[0], reverse=reverse)


def _avg_price(fills: list[_FillPlan]) -> Decimal | None:
    total_size = sum((fill.size for fill in fills), ZERO)
    if total_size <= ZERO:
        return None
    total_value = sum((fill.trade_value for fill in fills), ZERO)
    return (total_value / total_size).quantize(MONEY_PRECISION)


async def _latest_book(
    session: AsyncSession, token_id: str, now: datetime
) -> BookSnapshot | None:
    return (
        await session.execute(
            select(BookSnapshot)
            .where(BookSnapshot.token_id == token_id, BookSnapshot.ts <= now)
            .order_by(BookSnapshot.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _paper_cash(session: AsyncSession, settings: Settings) -> Decimal:
    cash_delta = (
        await session.execute(select(func.coalesce(func.sum(PaperFill.cash_delta), "0")))
    ).scalar_one()
    return (settings.paper_initial_cash + Decimal(str(cash_delta))).quantize(MONEY_PRECISION)


async def _latest_mark(session: AsyncSession, market_id: str) -> Decimal:
    snapshot = (
        await session.execute(
            select(MarketPriceSnapshot)
            .where(MarketPriceSnapshot.market_id == market_id)
            .order_by(MarketPriceSnapshot.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snapshot is None:
        return ZERO
    return snapshot.best_bid or snapshot.mid or ZERO


async def snapshot_equity(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> PaperEquitySnapshot:
    ts = now or datetime.now(UTC)
    await session.flush()
    cash = await _paper_cash(session, settings)
    positions = (
        await session.execute(select(PaperPosition).where(PaperPosition.qty > ZERO))
    ).scalars().all()
    position_value = ZERO
    unrealized = ZERO
    for position in positions:
        mark = await _latest_mark(session, position.market_id)
        position_value += position.qty * mark
        unrealized += position.qty * (mark - position.avg_cost)
    realized = (
        await session.execute(select(func.coalesce(func.sum(PaperPosition.realized_pnl), "0")))
    ).scalar_one()
    snapshot = PaperEquitySnapshot(
        ts=ts,
        cash=cash,
        equity=(cash + position_value).quantize(MONEY_PRECISION),
        realized_pnl=Decimal(str(realized)).quantize(MONEY_PRECISION),
        unrealized_pnl=unrealized.quantize(MONEY_PRECISION),
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


class PaperEngine:
    """Simulates BUY YES taker FAK fills against captured books."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def submit_signal(
        self, session: AsyncSession, signal: Signal, now: datetime | None = None
    ) -> PaperExecutionStats:
        ts = now or signal.ts
        if not self.settings.paper_trading_enabled:
            return PaperExecutionStats()
        existing = (
            await session.execute(
                select(PaperOrder).where(PaperOrder.signal_id == signal.id).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return PaperExecutionStats()

        market = await session.get(Market, signal.market_id)
        if market is None:
            return await self._reject(session, signal, ts, "market_not_found")
        event = await session.get(Event, market.event_id)
        if event is None:
            return await self._reject(session, signal, ts, "event_not_found", market=market)
        city = await session.get(City, event.city_slug)
        if city is None or city.needs_review:
            return await self._reject(session, signal, ts, "city_needs_review", market=market)
        if signal.status != "PROPOSED" or signal.stake <= ZERO:
            return await self._reject(session, signal, ts, "invalid_signal", market=market)
        if market.closed or event.closed or (event.end_date is not None and event.end_date <= ts):
            return await self._reject(session, signal, ts, "market_closed", market=market)

        book = await _latest_book(session, signal.token_id, ts)
        if book is None:
            return await self._reject(session, signal, ts, "book_missing", market=market)
        if (ts - book.ts).total_seconds() > self.settings.paper_book_stale_seconds:
            return await self._reject(
                session, signal, ts, "book_stale", market=market, book=book
            )

        asks = _parse_book_side(book.asks_json, reverse=False)
        if not asks:
            return await self._reject(session, signal, ts, "empty_book", market=market, book=book)

        fills = self._plan_buy_fak(signal.stake, asks)
        total_size = sum((fill.size for fill in fills), ZERO)
        if total_size < market.min_order_size:
            return await self._reject(
                session, signal, ts, "insufficient_liquidity", market=market, book=book
            )

        avg_price = _avg_price(fills)
        if avg_price is None:
            return await self._reject(
                session, signal, ts, "insufficient_liquidity", market=market, book=book
            )
        fee_paid = sum((fill.fee_paid for fill in fills), ZERO).quantize(MONEY_PRECISION)
        spent = abs(sum((fill.cash_delta for fill in fills), ZERO)).quantize(MONEY_PRECISION)
        status = "FILLED" if signal.stake - spent <= MONEY_PRECISION else "PARTIAL"
        requested_size = (
            signal.stake
            / cost_per_share(signal.market_price, self.settings.taker_fee_rate)
        ).quantize(
            MONEY_PRECISION
        )
        order = PaperOrder(
            ts=ts,
            signal_id=signal.id,
            market_id=market.id,
            condition_id=market.condition_id,
            token_id=signal.token_id,
            side="BUY",
            order_type="FAK",
            expected_price=signal.market_price,
            max_spend=signal.stake,
            requested_size=requested_size,
            filled_size=total_size.quantize(MONEY_PRECISION),
            avg_fill_price=avg_price,
            fee_paid=fee_paid,
            slippage=(avg_price - signal.market_price).quantize(MONEY_PRECISION),
            status=status,
            reject_reason=None,
            book_snapshot_id=book.id,
        )
        session.add(order)
        await session.flush()
        for fill in fills:
            session.add(
                PaperFill(
                    order_id=order.id,
                    signal_id=signal.id,
                    market_id=market.id,
                    token_id=signal.token_id,
                    book_snapshot_id=book.id,
                    ts=ts,
                    price=fill.price,
                    size=fill.size.quantize(MONEY_PRECISION),
                    fee_paid=fill.fee_paid,
                    cash_delta=fill.cash_delta,
                    liquidity="TAKER",
                )
            )
        await self._apply_buy_position(session, market, signal.token_id, fills, ts)
        await snapshot_equity(session, self.settings, ts)
        return PaperExecutionStats(orders=1, fills=len(fills))

    def _plan_buy_fak(
        self, max_spend: Decimal, asks: list[tuple[Decimal, Decimal]]
    ) -> list[_FillPlan]:
        fills: list[_FillPlan] = []
        remaining = max_spend
        for price, available_size in asks:
            effective_cost = cost_per_share(price, self.settings.taker_fee_rate)
            if effective_cost <= ZERO or remaining <= MONEY_PRECISION:
                break
            affordable_size = remaining / effective_cost
            size = min(available_size, affordable_size)
            if size <= ZERO:
                continue
            fee_paid = taker_fee(price, size, self.settings.taker_fee_rate)
            cash_delta = -((price * size) + fee_paid).quantize(MONEY_PRECISION)
            if abs(cash_delta) - remaining > MONEY_PRECISION:
                continue
            fills.append(
                _FillPlan(
                    price=price.quantize(MONEY_PRECISION),
                    size=size,
                    fee_paid=fee_paid,
                    cash_delta=cash_delta,
                )
            )
            remaining += cash_delta
        return fills

    async def _apply_buy_position(
        self,
        session: AsyncSession,
        market: Market,
        token_id: str,
        fills: list[_FillPlan],
        ts: datetime,
    ) -> None:
        size = sum((fill.size for fill in fills), ZERO)
        total_cost = sum((fill.trade_value + fill.fee_paid for fill in fills), ZERO)
        position = await session.get(PaperPosition, token_id)
        if position is None:
            position = PaperPosition(
                token_id=token_id,
                market_id=market.id,
                condition_id=market.condition_id,
                qty=size.quantize(MONEY_PRECISION),
                avg_cost=(total_cost / size).quantize(MONEY_PRECISION),
                realized_pnl=ZERO,
                settled=False,
                updated_at=ts,
            )
            session.add(position)
            return
        old_cost = position.qty * position.avg_cost
        new_qty = position.qty + size
        position.qty = new_qty.quantize(MONEY_PRECISION)
        position.avg_cost = ((old_cost + total_cost) / new_qty).quantize(MONEY_PRECISION)
        position.updated_at = ts
        position.settled = False

    async def _reject(
        self,
        session: AsyncSession,
        signal: Signal,
        ts: datetime,
        reason: str,
        *,
        market: Market | None = None,
        book: BookSnapshot | None = None,
    ) -> PaperExecutionStats:
        requested_size = ZERO
        if signal.stake > ZERO and signal.market_price > ZERO:
            requested_size = (
                signal.stake / cost_per_share(signal.market_price, self.settings.taker_fee_rate)
            ).quantize(MONEY_PRECISION)
        order = PaperOrder(
            ts=ts,
            signal_id=signal.id,
            market_id=signal.market_id,
            condition_id=market.condition_id if market else "",
            token_id=signal.token_id,
            side="BUY",
            order_type="FAK",
            expected_price=signal.market_price,
            max_spend=max(signal.stake, ZERO),
            requested_size=requested_size,
            filled_size=ZERO,
            avg_fill_price=None,
            fee_paid=ZERO,
            slippage=None,
            status="REJECTED",
            reject_reason=reason,
            book_snapshot_id=book.id if book else None,
        )
        session.add(order)
        await session.flush()
        return PaperExecutionStats(orders=1, rejected=1)


async def submit_proposed_signals(
    session: AsyncSession,
    settings: Settings,
    signals: list[Signal] | None = None,
    now: datetime | None = None,
) -> PaperExecutionStats:
    if not settings.paper_trading_enabled:
        return PaperExecutionStats()
    candidates = signals
    if candidates is None:
        candidates = list(
            (
                await session.execute(
                    select(Signal)
                    .where(Signal.status == "PROPOSED")
                    .order_by(Signal.ts, Signal.id)
                )
            )
            .scalars()
            .all()
        )
    engine = PaperEngine(settings)
    stats = PaperExecutionStats()
    for signal in candidates:
        stats += await engine.submit_signal(session, signal, now=now)
    return stats


async def settle_resolved_positions(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> PaperExecutionStats:
    ts = now or datetime.now(UTC)
    positions = (
        await session.execute(
            select(PaperPosition, Market)
            .join(Market, PaperPosition.market_id == Market.id)
            .where(
                PaperPosition.qty > ZERO,
                PaperPosition.settled.is_(False),
                Market.winner.is_not(None),
            )
        )
    ).all()
    settled = 0
    for position, market in positions:
        order = (
            await session.execute(
                select(PaperOrder)
                .where(
                    PaperOrder.token_id == position.token_id,
                    PaperOrder.status.in_(["FILLED", "PARTIAL"]),
                )
                .order_by(PaperOrder.ts)
                .limit(1)
            )
        ).scalar_one_or_none()
        if order is None:
            continue
        settlement_price = ONE if market.winner else ZERO
        qty = position.qty
        cash_delta = (qty * settlement_price).quantize(MONEY_PRECISION)
        realized = (qty * (settlement_price - position.avg_cost)).quantize(MONEY_PRECISION)
        session.add(
            PaperFill(
                order_id=order.id,
                signal_id=order.signal_id,
                market_id=market.id,
                token_id=position.token_id,
                book_snapshot_id=None,
                ts=ts,
                price=settlement_price,
                size=qty,
                fee_paid=ZERO,
                cash_delta=cash_delta,
                liquidity="SETTLEMENT",
            )
        )
        position.qty = ZERO
        position.avg_cost = ZERO
        position.realized_pnl = (position.realized_pnl + realized).quantize(MONEY_PRECISION)
        position.settled = True
        position.updated_at = ts
        settled += 1
    if settled:
        await snapshot_equity(session, settings, ts)
    return PaperExecutionStats(settled=settled, fills=settled)
