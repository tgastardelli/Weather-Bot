"""Modelos do schema (Etapa 4 do plano).

Preços/dinheiro: Decimal (DecimalText). Temperaturas: float (não são dinheiro).
Datas: UTC tz-aware (UTCDateTime); dias-alvo de mercado: Date (dia local da cidade).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import DecimalText, UTCDateTime


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012
        Decimal: DecimalText,
        datetime: UTCDateTime,
    }


class City(Base):
    """Registry cidade → estação de resolução (skill polymarket-api §7)."""

    __tablename__ = "city_registry"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    series_slug: Mapped[str | None] = mapped_column(String(96))
    station_code: Mapped[str | None] = mapped_column(String(8), index=True)
    station_name: Mapped[str | None] = mapped_column(String(96))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    timezone: Mapped[str | None] = mapped_column(String(48))
    unit: Mapped[str] = mapped_column(String(1), default="C")  # C | F
    resolution_source: Mapped[str | None] = mapped_column(String(64))
    resolution_url: Mapped[str | None] = mapped_column(Text)
    rounding: Mapped[str] = mapped_column(String(8), default="round")  # round | floor
    needs_review: Mapped[bool] = mapped_column(default=True)
    active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime]


class Event(Base):
    """Evento Gamma negRisk: um dia/cidade de 'Highest temperature'."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # gamma event id
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    city_slug: Mapped[str] = mapped_column(ForeignKey("city_registry.slug"), index=True)
    target_date: Mapped[date] = mapped_column(Date, index=True)  # dia local da medição
    end_date: Mapped[datetime | None]  # fim de trading (12:00 UTC do dia seguinte)
    neg_risk_market_id: Mapped[str | None] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(default=True)
    closed: Mapped[bool] = mapped_column(default=False, index=True)
    volume: Mapped[float | None]
    liquidity: Mapped[float | None]
    first_seen_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class Market(Base):
    """Bucket binário (Yes/No) dentro de um evento."""

    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)  # gamma market id
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    question: Mapped[str] = mapped_column(Text)
    group_item_title: Mapped[str] = mapped_column(String(48))
    group_item_threshold: Mapped[int] = mapped_column(default=0)
    bucket_kind: Mapped[str] = mapped_column(String(8))  # below | exact | range | above
    bucket_low: Mapped[Decimal | None]  # em unidade do mercado (°C ou °F)
    bucket_high: Mapped[Decimal | None]
    yes_token_id: Mapped[str] = mapped_column(String(96), unique=True)
    no_token_id: Mapped[str] = mapped_column(String(96))
    tick_size: Mapped[Decimal] = mapped_column(default=Decimal("0.001"))
    min_order_size: Mapped[Decimal] = mapped_column(default=Decimal("5"))
    closed: Mapped[bool] = mapped_column(default=False)
    winner: Mapped[bool | None] = mapped_column(default=None)
    resolved_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]


class MarketPriceSnapshot(Base):
    __tablename__ = "market_price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    best_bid: Mapped[Decimal | None]
    best_ask: Mapped[Decimal | None]
    mid: Mapped[Decimal | None]
    bid_size: Mapped[Decimal | None]  # tamanho no melhor bid (shares)
    ask_size: Mapped[Decimal | None]

    __table_args__ = (Index("ix_price_market_ts", "market_id", "ts"),)


class MarketPriceHistoryPoint(Base):
    """Ponto historico do CLOB prices-history; nao representa book nem best ask."""

    __tablename__ = "market_price_history_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    token_id: Mapped[str] = mapped_column(String(96), index=True)
    price: Mapped[Decimal]
    interval: Mapped[str] = mapped_column(String(16), default="1d")
    source: Mapped[str] = mapped_column(String(32), default="clob_prices_history")

    __table_args__ = (
        UniqueConstraint("token_id", "interval", "ts"),
        Index("ix_price_history_market_ts", "market_id", "ts"),
    )


class MarketTradeHistoryPoint(Base):
    """Trade publico historico validado; nao representa book nem best ask."""

    __tablename__ = "market_trade_history_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    token_id: Mapped[str] = mapped_column(String(96), index=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    price: Mapped[Decimal]
    size: Mapped[Decimal]
    side: Mapped[str | None] = mapped_column(String(8))
    transaction_hash: Mapped[str | None] = mapped_column(String(96), index=True)
    source: Mapped[str] = mapped_column(String(32), default="data_api_trades")

    __table_args__ = (
        UniqueConstraint("token_id", "ts", "price", "size", "side"),
        Index("ix_trade_history_market_ts", "market_id", "ts"),
    )


class HistoryBackfillRun(Base):
    """Auditoria de janelas do backfill historico de mercado."""

    __tablename__ = "history_backfill_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(index=True)
    completed_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16), index=True)
    window_start: Mapped[date] = mapped_column(Date, index=True)
    window_end: Mapped[date] = mapped_column(Date, index=True)
    cities_json: Mapped[str] = mapped_column(String(256))
    interval: Mapped[str] = mapped_column(String(16), default="1d")
    probe_trades: Mapped[bool] = mapped_column(default=False)
    events_seen: Mapped[int] = mapped_column(default=0)
    markets_upserted: Mapped[int] = mapped_column(default=0)
    history_points: Mapped[int] = mapped_column(default=0)
    trade_history_points: Mapped[int] = mapped_column(default=0)
    rejected_trade_sources: Mapped[int] = mapped_column(default=0)
    source_status_json: Mapped[str] = mapped_column(Text, default="{}")
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    params_json: Mapped[str] = mapped_column(Text, default="{}")

    __table_args__ = (
        Index(
            "ix_history_backfill_window",
            "window_start",
            "window_end",
            "status",
        ),
    )


class BookSnapshot(Base):
    """Book efêmero — gravado para auditoria/backtest de execução."""

    __tablename__ = "book_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    token_id: Mapped[str] = mapped_column(String(96), index=True)
    bids_json: Mapped[str] = mapped_column(Text)  # [[price, size], ...] topo do book
    asks_json: Mapped[str] = mapped_column(Text)


class ForecastSnapshot(Base):
    """Previsão de tmax para um dia-alvo, por modelo/fonte, no momento da coleta."""

    __tablename__ = "forecast_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(index=True)
    city_slug: Mapped[str] = mapped_column(ForeignKey("city_registry.slug"), index=True)
    source: Mapped[str] = mapped_column(String(24))  # open_meteo | open_meteo_ensemble | historical
    model: Mapped[str] = mapped_column(String(32))
    target_date: Mapped[date] = mapped_column(Date, index=True)
    lead_days: Mapped[int] = mapped_column(default=0)
    tmax_c: Mapped[float | None]  # determinístico (None p/ ensemble)
    n_members: Mapped[int] = mapped_column(default=0)

    __table_args__ = (Index("ix_forecast_city_date", "city_slug", "target_date"),)


class EnsembleMember(Base):
    __tablename__ = "ensemble_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshots.id"), index=True)
    member: Mapped[int]
    tmax_c: Mapped[float]


class Observation(Base):
    """Observação intradiária (METAR) na estação de resolução."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    city_slug: Mapped[str] = mapped_column(ForeignKey("city_registry.slug"), index=True)
    station_code: Mapped[str] = mapped_column(String(8))
    observed_at: Mapped[datetime]
    temp_c: Mapped[float]
    source: Mapped[str] = mapped_column(String(16), default="metar")

    __table_args__ = (UniqueConstraint("station_code", "observed_at", "source"),)


class DailyObservedMax(Base):
    """Máxima diária consolidada (verdade para calibração)."""

    __tablename__ = "daily_observed_max"

    id: Mapped[int] = mapped_column(primary_key=True)
    city_slug: Mapped[str] = mapped_column(ForeignKey("city_registry.slug"), index=True)
    target_date: Mapped[date] = mapped_column(Date)
    tmax_c: Mapped[float]
    source: Mapped[str] = mapped_column(String(16))  # era5 | metar | resolution

    __table_args__ = (UniqueConstraint("city_slug", "target_date", "source"),)


class Resolution(Base):
    __tablename__ = "resolutions"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    winner_market_id: Mapped[str | None] = mapped_column(ForeignKey("markets.id"))
    winner_bucket: Mapped[str | None] = mapped_column(String(48))
    resolved_at: Mapped[datetime]


class Signal(Base):
    """Sinal gerado pelo strategy engine (sem ordens nesta fase)."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    token_id: Mapped[str] = mapped_column(String(96))
    side: Mapped[str] = mapped_column(String(4), default="BUY")
    profile: Mapped[str] = mapped_column(String(12))  # longshot | max_edge
    model_prob: Mapped[float]
    market_price: Mapped[Decimal]
    edge_gross: Mapped[Decimal]
    edge_net: Mapped[Decimal]
    stake: Mapped[Decimal]
    status: Mapped[str] = mapped_column(String(12), index=True)  # PROPOSED | SKIPPED
    reason: Mapped[str | None] = mapped_column(Text)


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), unique=True, index=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    token_id: Mapped[str] = mapped_column(String(96), index=True)
    side: Mapped[str] = mapped_column(String(4), default="BUY")
    order_type: Mapped[str] = mapped_column(String(8), default="FAK")
    expected_price: Mapped[Decimal]
    max_spend: Mapped[Decimal]
    requested_size: Mapped[Decimal]
    filled_size: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    avg_fill_price: Mapped[Decimal | None]
    fee_paid: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    slippage: Mapped[Decimal | None]
    status: Mapped[str] = mapped_column(String(16), index=True)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    book_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("book_snapshots.id"))


class PaperFill(Base):
    __tablename__ = "paper_fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_orders.id"), index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), index=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    token_id: Mapped[str] = mapped_column(String(96), index=True)
    book_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("book_snapshots.id"))
    ts: Mapped[datetime] = mapped_column(index=True)
    price: Mapped[Decimal]
    size: Mapped[Decimal]
    fee_paid: Mapped[Decimal]
    cash_delta: Mapped[Decimal]
    liquidity: Mapped[str] = mapped_column(String(12))  # TAKER | SETTLEMENT

    __table_args__ = (Index("ix_paper_fill_token_ts", "token_id", "ts"),)


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    token_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), index=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    qty: Mapped[Decimal]
    avg_cost: Mapped[Decimal]
    realized_pnl: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    settled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime]


class PaperEquitySnapshot(Base):
    __tablename__ = "paper_equity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(index=True)
    cash: Mapped[Decimal]
    equity: Mapped[Decimal]
    realized_pnl: Mapped[Decimal]
    unrealized_pnl: Mapped[Decimal]


class CalibrationMetric(Base):
    """Erro de previsão por cidade/lead (análise — Etapa 5)."""

    __tablename__ = "calibration_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    computed_at: Mapped[datetime]
    city_slug: Mapped[str] = mapped_column(ForeignKey("city_registry.slug"), index=True)
    model: Mapped[str] = mapped_column(String(32))
    lead_days: Mapped[int]
    bias_c: Mapped[float]
    mae_c: Mapped[float]
    residual_std_c: Mapped[float]
    n_samples: Mapped[int]

    __table_args__ = (UniqueConstraint("city_slug", "model", "lead_days"),)


class CityVolatilityMetric(Base):
    """Ranking alto risco/alta recompensa por surpresa historica da cidade."""

    __tablename__ = "city_volatility_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(index=True)
    city_slug: Mapped[str] = mapped_column(ForeignKey("city_registry.slug"), index=True)
    station_code: Mapped[str | None] = mapped_column(String(8))
    n_samples: Mapped[int]
    forecast_mae_c: Mapped[float]
    tail_miss_rate_2c: Mapped[float]
    tail_miss_rate_3c: Mapped[float]
    tail_miss_rate_5c: Mapped[float]
    upside_surprise_rate_3c: Mapped[float]
    downside_surprise_rate_3c: Mapped[float]
    avg_intraday_range_c: Mapped[float]
    p90_intraday_range_c: Mapped[float]
    max_3h_move_c: Mapped[float]
    max_6h_move_c: Mapped[float]
    reward_volatility_score: Mapped[float]
    data_quality: Mapped[str] = mapped_column(Text)
    lead_mae_json: Mapped[str] = mapped_column(Text)
    params_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("computed_at", "city_slug"),
        Index("ix_city_volatility_run_score", "computed_at", "reward_volatility_score"),
    )


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime]
    profile: Mapped[str] = mapped_column(String(12))
    n_trades: Mapped[int]
    n_wins: Mapped[int]
    total_staked: Mapped[Decimal]
    total_pnl: Mapped[Decimal]
    win_rate: Mapped[float]
    profit_factor: Mapped[float | None]
    max_drawdown: Mapped[Decimal]
    params_json: Mapped[str] = mapped_column(Text)


class EvidenceRun(Base):
    __tablename__ = "evidence_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    cities_json: Mapped[str] = mapped_column(Text)
    data_health_json: Mapped[str] = mapped_column(Text)
    model_health_json: Mapped[str] = mapped_column(Text)
    trading_json: Mapped[str] = mapped_column(Text)
    gates_json: Mapped[str] = mapped_column(Text)


class MeasurementRun(Base):
    __tablename__ = "measurement_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    summary_json: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str] = mapped_column(Text)
    checks_json: Mapped[str] = mapped_column(Text)


class HistoricalValidationRun(Base):
    __tablename__ = "historical_validation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    cities_json: Mapped[str] = mapped_column(Text)
    data_health_json: Mapped[str] = mapped_column(Text)
    model_health_json: Mapped[str] = mapped_column(Text)
    trading_json: Mapped[str] = mapped_column(Text)
    gates_json: Mapped[str] = mapped_column(Text)
