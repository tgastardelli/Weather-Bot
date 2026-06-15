"""Rank Polymarket weather cities by high-risk/high-reward temperature surprise.

The primary signal is historical forecast error versus observed daily maximum.
Intraday volatility is secondary and helps explain whether a city tends to have
wide daily temperature swings.
"""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import fmean
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Base, City, CityVolatilityMetric, DailyObservedMax, ForecastSnapshot
from app.db.session import create_engine, create_session_factory
from app.weather.open_meteo import OpenMeteoClient

logger = logging.getLogger(__name__)

TAIL_2C = 2.0
TAIL_3C = 3.0
TAIL_5C = 5.0
MAE_HIGH_RISK_C = 5.0
INTRADAY_HIGH_RISK_RANGE_C = 15.0
WEIGHT_MAE = 0.60
WEIGHT_TAIL = 0.25
WEIGHT_INTRADAY = 0.15
NEEDS_REVIEW_PENALTY = 0.75
OBSERVED_SOURCE_PRIORITY = {"resolution": 3, "era5": 2, "metar": 1}

DailyHourlySeries = dict[date, list[float]]


@dataclass(frozen=True)
class ForecastErrorRecord:
    target_date: date
    model: str
    lead_days: int
    forecast_tmax_c: float
    observed_tmax_c: float

    @property
    def residual_c(self) -> float:
        return self.observed_tmax_c - self.forecast_tmax_c


@dataclass(frozen=True)
class ForecastErrorMetrics:
    n_samples: int
    forecast_mae_c: float
    tail_miss_rate_2c: float
    tail_miss_rate_3c: float
    tail_miss_rate_5c: float
    upside_surprise_rate_3c: float
    downside_surprise_rate_3c: float
    lead_mae_c: dict[int, float]


@dataclass(frozen=True)
class IntradayMetrics:
    n_days: int
    avg_intraday_range_c: float
    p90_intraday_range_c: float
    max_3h_move_c: float
    max_6h_move_c: float


@dataclass(frozen=True)
class CityVolatilityRow:
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
    lead_mae_c: dict[int, float]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "city_slug": self.city_slug,
            "station_code": self.station_code,
            "n_samples": self.n_samples,
            "forecast_mae_c": _round(self.forecast_mae_c),
            "tail_miss_rate_2c": _round(self.tail_miss_rate_2c),
            "tail_miss_rate_3c": _round(self.tail_miss_rate_3c),
            "tail_miss_rate_5c": _round(self.tail_miss_rate_5c),
            "upside_surprise_rate_3c": _round(self.upside_surprise_rate_3c),
            "downside_surprise_rate_3c": _round(self.downside_surprise_rate_3c),
            "avg_intraday_range_c": _round(self.avg_intraday_range_c),
            "p90_intraday_range_c": _round(self.p90_intraday_range_c),
            "max_3h_move_c": _round(self.max_3h_move_c),
            "max_6h_move_c": _round(self.max_6h_move_c),
            "reward_volatility_score": _round(self.reward_volatility_score),
            "data_quality": self.data_quality,
            "lead_mae_c": {str(k): _round(v) for k, v in sorted(self.lead_mae_c.items())},
        }


def _round(value: float) -> float:
    return round(value, 4)


def _rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def compute_forecast_error_metrics(records: list[ForecastErrorRecord]) -> ForecastErrorMetrics:
    residuals = [record.residual_c for record in records]
    n = len(residuals)
    abs_errors = [abs(value) for value in residuals]

    lead_errors: dict[int, list[float]] = {}
    for record in records:
        lead_errors.setdefault(record.lead_days, []).append(abs(record.residual_c))

    return ForecastErrorMetrics(
        n_samples=n,
        forecast_mae_c=fmean(abs_errors) if abs_errors else 0.0,
        tail_miss_rate_2c=_rate(sum(1 for value in abs_errors if value >= TAIL_2C), n),
        tail_miss_rate_3c=_rate(sum(1 for value in abs_errors if value >= TAIL_3C), n),
        tail_miss_rate_5c=_rate(sum(1 for value in abs_errors if value >= TAIL_5C), n),
        upside_surprise_rate_3c=_rate(sum(1 for value in residuals if value >= TAIL_3C), n),
        downside_surprise_rate_3c=_rate(sum(1 for value in residuals if value <= -TAIL_3C), n),
        lead_mae_c={lead: fmean(values) for lead, values in lead_errors.items()},
    )


def _max_window_move(values: list[float], hours: int) -> float:
    if len(values) <= hours:
        return 0.0
    return max(abs(values[i] - values[i - hours]) for i in range(hours, len(values)))


def compute_intraday_metrics(series: DailyHourlySeries) -> IntradayMetrics:
    ranges: list[float] = []
    max_3h = 0.0
    max_6h = 0.0
    for values in series.values():
        if len(values) < 2:
            continue
        ranges.append(max(values) - min(values))
        max_3h = max(max_3h, _max_window_move(values, 3))
        max_6h = max(max_6h, _max_window_move(values, 6))

    return IntradayMetrics(
        n_days=len(ranges),
        avg_intraday_range_c=fmean(ranges) if ranges else 0.0,
        p90_intraday_range_c=percentile(ranges, 0.90),
        max_3h_move_c=max_3h,
        max_6h_move_c=max_6h,
    )


def reward_volatility_score(
    forecast: ForecastErrorMetrics,
    intraday: IntradayMetrics,
    *,
    min_samples: int,
    needs_review: bool,
) -> float:
    if forecast.n_samples <= 0:
        return 0.0

    mae_component = min(forecast.forecast_mae_c / MAE_HIGH_RISK_C, 1.0)
    tail_component = forecast.tail_miss_rate_3c
    intraday_component = min(intraday.p90_intraday_range_c / INTRADAY_HIGH_RISK_RANGE_C, 1.0)
    score = 100.0 * (
        (WEIGHT_MAE * mae_component)
        + (WEIGHT_TAIL * tail_component)
        + (WEIGHT_INTRADAY * intraday_component)
    )

    if forecast.n_samples < min_samples:
        score *= forecast.n_samples / min_samples
    if needs_review:
        score *= NEEDS_REVIEW_PENALTY
    return score


def data_quality(
    city: City,
    forecast: ForecastErrorMetrics,
    intraday: IntradayMetrics,
    min_samples: int,
) -> str:
    issues: list[str] = []
    if city.station_code is None or city.latitude is None or city.longitude is None:
        issues.append("missing_station")
    if city.needs_review:
        issues.append("needs_review")
    if forecast.n_samples == 0:
        issues.append("no_forecast_pairs")
    elif forecast.n_samples < min_samples:
        issues.append("low_samples")
    if intraday.n_days == 0:
        issues.append("no_intraday")
    return "ok" if not issues else ",".join(issues)


def build_city_volatility_row(
    city: City,
    records: list[ForecastErrorRecord],
    hourly_series: DailyHourlySeries,
    *,
    min_samples: int,
) -> CityVolatilityRow:
    forecast = compute_forecast_error_metrics(records)
    intraday = compute_intraday_metrics(hourly_series)
    score = reward_volatility_score(
        forecast,
        intraday,
        min_samples=min_samples,
        needs_review=city.needs_review,
    )
    return CityVolatilityRow(
        city_slug=city.slug,
        station_code=city.station_code,
        n_samples=forecast.n_samples,
        forecast_mae_c=forecast.forecast_mae_c,
        tail_miss_rate_2c=forecast.tail_miss_rate_2c,
        tail_miss_rate_3c=forecast.tail_miss_rate_3c,
        tail_miss_rate_5c=forecast.tail_miss_rate_5c,
        upside_surprise_rate_3c=forecast.upside_surprise_rate_3c,
        downside_surprise_rate_3c=forecast.downside_surprise_rate_3c,
        avg_intraday_range_c=intraday.avg_intraday_range_c,
        p90_intraday_range_c=intraday.p90_intraday_range_c,
        max_3h_move_c=intraday.max_3h_move_c,
        max_6h_move_c=intraday.max_6h_move_c,
        reward_volatility_score=score,
        data_quality=data_quality(city, forecast, intraday, min_samples),
        lead_mae_c=forecast.lead_mae_c,
    )


async def candidate_cities(
    session: AsyncSession, settings: Settings, cities: list[str] | None
) -> list[City]:
    query = select(City).where(City.active.is_(True))
    rows = list((await session.execute(query)).scalars().all())
    selected = cities if cities is not None else settings.cities
    if selected is not None:
        selected_set = set(selected)
        rows = [city for city in rows if city.slug in selected_set]
    return rows


async def load_forecast_error_records(
    session: AsyncSession, city_slug: str, start: date, end: date
) -> list[ForecastErrorRecord]:
    observed_rows = (
        await session.execute(
            select(DailyObservedMax).where(
                DailyObservedMax.city_slug == city_slug,
                DailyObservedMax.target_date >= start,
                DailyObservedMax.target_date <= end,
            )
        )
    ).scalars().all()

    observed_by_date: dict[date, DailyObservedMax] = {}
    for row in observed_rows:
        existing_observed = observed_by_date.get(row.target_date)
        existing_priority = (
            OBSERVED_SOURCE_PRIORITY.get(existing_observed.source, 0)
            if existing_observed
            else -1
        )
        if OBSERVED_SOURCE_PRIORITY.get(row.source, 0) > existing_priority:
            observed_by_date[row.target_date] = row

    forecast_rows = (
        await session.execute(
            select(ForecastSnapshot).where(
                ForecastSnapshot.city_slug == city_slug,
                ForecastSnapshot.target_date >= start,
                ForecastSnapshot.target_date <= end,
                ForecastSnapshot.tmax_c.is_not(None),
            )
        )
    ).scalars().all()

    latest_by_key: dict[tuple[date, str, int], ForecastSnapshot] = {}
    for forecast in forecast_rows:
        key = (forecast.target_date, forecast.model, forecast.lead_days)
        existing_forecast = latest_by_key.get(key)
        if existing_forecast is None or forecast.fetched_at > existing_forecast.fetched_at:
            latest_by_key[key] = forecast

    records: list[ForecastErrorRecord] = []
    for forecast in latest_by_key.values():
        observed = observed_by_date.get(forecast.target_date)
        if observed is None or forecast.tmax_c is None:
            continue
        records.append(
            ForecastErrorRecord(
                target_date=forecast.target_date,
                model=forecast.model,
                lead_days=forecast.lead_days,
                forecast_tmax_c=forecast.tmax_c,
                observed_tmax_c=observed.tmax_c,
            )
        )
    return records


async def compute_city_volatility(
    session: AsyncSession,
    cities: list[City],
    *,
    start: date,
    end: date,
    min_samples: int,
    intraday_by_city: dict[str, DailyHourlySeries] | None = None,
) -> list[CityVolatilityRow]:
    intraday_by_city = intraday_by_city or {}
    rows: list[CityVolatilityRow] = []
    for city in cities:
        records = await load_forecast_error_records(session, city.slug, start, end)
        rows.append(
            build_city_volatility_row(
                city,
                records,
                intraday_by_city.get(city.slug, {}),
                min_samples=min_samples,
            )
        )
    return sorted(rows, key=lambda row: row.reward_volatility_score, reverse=True)


def city_volatility_params_json(
    *,
    days: int,
    min_samples: int,
    cities: list[str] | None,
    start: date,
    end: date,
) -> str:
    return json.dumps(
        {
            "cities": cities,
            "days": days,
            "end": end.isoformat(),
            "min_samples": min_samples,
            "score": {
                "intraday_high_risk_range_c": INTRADAY_HIGH_RISK_RANGE_C,
                "mae_high_risk_c": MAE_HIGH_RISK_C,
                "needs_review_penalty": NEEDS_REVIEW_PENALTY,
                "tail_threshold_c": TAIL_3C,
                "weight_intraday": WEIGHT_INTRADAY,
                "weight_mae": WEIGHT_MAE,
                "weight_tail": WEIGHT_TAIL,
            },
            "start": start.isoformat(),
        },
        sort_keys=True,
    )


def lead_mae_json(row: CityVolatilityRow) -> str:
    return json.dumps(
        {str(lead): _round(value) for lead, value in sorted(row.lead_mae_c.items())},
        sort_keys=True,
    )


async def persist_city_volatility(
    session: AsyncSession,
    rows: list[CityVolatilityRow],
    *,
    computed_at: datetime,
    params_json: str,
) -> int:
    for row in rows:
        session.add(
            CityVolatilityMetric(
                computed_at=computed_at,
                city_slug=row.city_slug,
                station_code=row.station_code,
                n_samples=row.n_samples,
                forecast_mae_c=row.forecast_mae_c,
                tail_miss_rate_2c=row.tail_miss_rate_2c,
                tail_miss_rate_3c=row.tail_miss_rate_3c,
                tail_miss_rate_5c=row.tail_miss_rate_5c,
                upside_surprise_rate_3c=row.upside_surprise_rate_3c,
                downside_surprise_rate_3c=row.downside_surprise_rate_3c,
                avg_intraday_range_c=row.avg_intraday_range_c,
                p90_intraday_range_c=row.p90_intraday_range_c,
                max_3h_move_c=row.max_3h_move_c,
                max_6h_move_c=row.max_6h_move_c,
                reward_volatility_score=row.reward_volatility_score,
                data_quality=row.data_quality,
                lead_mae_json=lead_mae_json(row),
                params_json=params_json,
            )
        )
    await session.flush()
    return len(rows)


async def _fetch_intraday_by_city(
    client: OpenMeteoClient, cities: list[City], start: date, end: date
) -> dict[str, DailyHourlySeries]:
    intraday: dict[str, DailyHourlySeries] = {}
    for city in cities:
        if city.latitude is None or city.longitude is None:
            intraday[city.slug] = {}
            continue
        intraday[city.slug] = await client.era5_hourly_temperature(
            city.latitude, city.longitude, start, end
        )
    return intraday


async def run_city_volatility(
    settings: Settings,
    *,
    days: int,
    cities: list[str] | None,
    min_samples: int,
) -> list[CityVolatilityRow]:
    engine = create_engine(settings.db_url)
    session_factory = create_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    end = datetime.now(UTC).date() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    computed_at = datetime.now(UTC)
    params_json = city_volatility_params_json(
        days=days,
        min_samples=min_samples,
        cities=cities,
        start=start,
        end=end,
    )
    try:
        async with session_factory() as session:
            city_rows = await candidate_cities(session, settings, cities)

        async with httpx.AsyncClient(timeout=60.0) as http:
            client = OpenMeteoClient(http)
            intraday = await _fetch_intraday_by_city(client, city_rows, start, end)

        async with session_factory() as session:
            rows = await compute_city_volatility(
                session,
                city_rows,
                start=start,
                end=end,
                min_samples=min_samples,
                intraday_by_city=intraday,
            )
        async with session_factory() as session, session.begin():
            await persist_city_volatility(
                session,
                rows,
                computed_at=computed_at,
                params_json=params_json,
            )
        logger.info("city volatility rows persisted: %d", len(rows))
        return rows
    finally:
        await engine.dispose()


def parse_cities(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    parsed = [part.strip() for part in raw.split(",") if part.strip()]
    return parsed or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank Polymarket weather cities by forecast surprise and intraday volatility."
    )
    parser.add_argument("--cities", help="Comma-separated city slugs, e.g. seoul,hong-kong,nyc.")
    parser.add_argument("--days", type=int, choices=(365, 730, 1095), default=730)
    parser.add_argument("--min-samples", type=int, default=120)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _print_table(rows: list[CityVolatilityRow]) -> None:
    print("city\tstation\tsamples\tmae_c\ttail_3c\trange_p90_c\tscore\tquality")
    for row in rows:
        print(
            "\t".join(
                [
                    row.city_slug,
                    row.station_code or "-",
                    str(row.n_samples),
                    f"{row.forecast_mae_c:.2f}",
                    f"{row.tail_miss_rate_3c:.2%}",
                    f"{row.p90_intraday_range_c:.2f}",
                    f"{row.reward_volatility_score:.2f}",
                    row.data_quality,
                ]
            )
        )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.json else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    rows = asyncio.run(
        run_city_volatility(
            get_settings(),
            days=args.days,
            cities=parse_cities(args.cities),
            min_samples=args.min_samples,
        )
    )
    if args.json:
        print(json.dumps([row.to_jsonable() for row in rows], ensure_ascii=False, sort_keys=True))
    else:
        _print_table(rows)


if __name__ == "__main__":
    main()
