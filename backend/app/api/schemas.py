"""Schemas de resposta (pydantic v2).

Contrato com o front (skill react-frontend): dinheiro/preço sai como STRING
no JSON (`Money`), datas em ISO UTC; o front apenas formata.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

Money = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="json")]


class CityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    station_code: str | None
    unit: str
    resolution_source: str | None
    rounding: str
    needs_review: bool
    active: bool


class BucketOut(BaseModel):
    market_id: str
    label: str
    kind: str
    low: Money | None
    high: Money | None
    yes_token_id: str
    best_bid: Money | None
    best_ask: Money | None
    mid: Money | None
    model_prob: float | None
    edge_net: Money | None
    winner: bool | None


class EventOut(BaseModel):
    id: str
    slug: str
    title: str
    city_slug: str
    target_date: date
    end_date: datetime | None
    closed: bool
    volume: float | None
    liquidity: float | None
    buckets: list[BucketOut]


class PricePoint(BaseModel):
    ts: datetime
    market_id: str
    label: str
    mid: Money | None


class ForecastPoint(BaseModel):
    fetched_at: datetime
    model: str
    source: str
    target_date: date
    tmax_c: float | None
    p10: float | None
    p50: float | None
    p90: float | None


class ObservationPoint(BaseModel):
    observed_at: datetime
    temp_c: float


class EventDetailOut(BaseModel):
    event: EventOut
    prices: list[PricePoint]
    forecasts: list[ForecastPoint]
    observations: list[ObservationPoint]


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    market_id: str
    side: str
    profile: str
    model_prob: float
    market_price: Money
    edge_gross: Money
    edge_net: Money
    stake: Money
    status: str
    reason: str | None


class SignalRowOut(SignalOut):
    """Sinal enriquecido com contexto do mercado/evento para a tabela do front."""

    bucket_label: str
    event_slug: str
    city_slug: str


class CalibrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    city_slug: str
    model: str
    lead_days: int
    bias_c: float
    mae_c: float
    residual_std_c: float
    n_samples: int
    computed_at: datetime


class BacktestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_at: datetime
    profile: str
    n_trades: int
    n_wins: int
    total_staked: Money
    total_pnl: Money
    win_rate: float
    profit_factor: float | None
    max_drawdown: Money
    params_json: str


class CityVolatilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    computed_at: datetime
    city_slug: str
    station_code: str | None
    n_samples: int
    forecast_mae_c: float
    tail_miss_rate_2c: float
    tail_miss_rate_3c: float
    tail_miss_rate_5c: float
    upside_surprise_rate_3c: float
    downside_surprise_rate_3c: float
    avg_intraday_range_c: float
    p90_intraday_range_c: float
    max_3h_move_c: float
    max_6h_move_c: float
    reward_volatility_score: float
    data_quality: str
    lead_mae_json: str
    params_json: str


class EvidenceRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_at: datetime
    status: str
    window_start: date | None
    window_end: date | None
    cities_json: str
    data_health_json: str
    model_health_json: str
    trading_json: str
    gates_json: str


class EvidenceResponse(BaseModel):
    latest: EvidenceRunOut | None
    history: list[EvidenceRunOut]


class MeasurementRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_at: datetime
    status: str
    window_start: date | None
    window_end: date | None
    summary_json: str
    metrics_json: str
    checks_json: str


class MeasurementResponse(BaseModel):
    latest: MeasurementRunOut | None
    history: list[MeasurementRunOut]


class HistoricalValidationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_at: datetime
    status: str
    window_start: date | None
    window_end: date | None
    cities_json: str
    data_health_json: str
    model_health_json: str
    trading_json: str
    gates_json: str


class HistoricalValidationResponse(BaseModel):
    latest: HistoricalValidationRunOut | None
    history: list[HistoricalValidationRunOut]


class HistoryBackfillRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_at: datetime
    completed_at: datetime | None
    status: str
    window_start: date
    window_end: date
    cities_json: str
    interval: str
    probe_trades: bool
    events_seen: int
    markets_upserted: int
    history_points: int
    trade_history_points: int
    rejected_trade_sources: int
    source_status_json: str
    errors_json: str
    params_json: str


class HistoryBackfillResponse(BaseModel):
    latest: HistoryBackfillRunOut | None
    history: list[HistoryBackfillRunOut]


class LiveReadinessResponse(BaseModel):
    status: str
    mode: str
    ready_for_live_review: bool
    checks: dict[str, object]
    blockers: list[str]
    risk_limits: dict[str, str]
    geoblock: dict[str, object]
    last_error: str | None


class HealthOut(BaseModel):
    status: str
    mode: str
    time: datetime
