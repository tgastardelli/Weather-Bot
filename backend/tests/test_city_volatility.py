"""City high-risk/high-reward volatility analysis tests."""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.city_volatility import (
    ForecastErrorRecord,
    build_city_volatility_row,
    build_parser,
    compute_city_volatility,
    compute_forecast_error_metrics,
    compute_intraday_metrics,
    parse_cities,
    percentile,
    persist_city_volatility,
)
from app.config import Settings
from app.db.models import City, CityVolatilityMetric, DailyObservedMax, ForecastSnapshot


def _city(slug: str, *, needs_review: bool = False, station_code: str | None = "RKSI") -> City:
    return City(
        slug=slug,
        name=slug.replace("-", " ").title(),
        series_slug=f"{slug}-daily-weather",
        station_code=station_code,
        station_name=None,
        latitude=1.0 if station_code is not None else None,
        longitude=2.0 if station_code is not None else None,
        timezone="UTC" if station_code is not None else None,
        unit="C",
        resolution_source="test",
        resolution_url=None,
        rounding="round",
        needs_review=needs_review,
        active=True,
        updated_at=datetime(2026, 6, 11, tzinfo=UTC),
    )


def test_parser_accepts_public_city_volatility_options() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--cities", "seoul,hong-kong,nyc", "--days", "730", "--min-samples", "120", "--json"]
    )

    assert parse_cities(args.cities) == ["seoul", "hong-kong", "nyc"]
    assert args.days == 730
    assert args.min_samples == 120
    assert args.json is True


def test_forecast_error_metrics_measure_tail_and_direction() -> None:
    records = [
        ForecastErrorRecord(date(2026, 6, 1), "gfs", 1, 20.0, 23.0),
        ForecastErrorRecord(date(2026, 6, 2), "gfs", 1, 30.0, 26.0),
        ForecastErrorRecord(date(2026, 6, 3), "ecmwf", 2, 18.0, 19.0),
    ]

    metrics = compute_forecast_error_metrics(records)

    assert metrics.n_samples == 3
    assert round(metrics.forecast_mae_c, 4) == 2.6667
    assert round(metrics.tail_miss_rate_2c, 4) == 0.6667
    assert round(metrics.tail_miss_rate_3c, 4) == 0.6667
    assert metrics.tail_miss_rate_5c == 0.0
    assert round(metrics.upside_surprise_rate_3c, 4) == 0.3333
    assert round(metrics.downside_surprise_rate_3c, 4) == 0.3333
    assert metrics.lead_mae_c == {1: 3.5, 2: 1.0}


def test_intraday_metrics_capture_range_and_fast_moves() -> None:
    metrics = compute_intraday_metrics(
        {
            date(2026, 6, 1): [10.0, 12.0, 14.0, 20.0, 17.0, 16.0, 15.0],
            date(2026, 6, 2): [5.0, 6.0],
        }
    )

    assert percentile([1.0, 10.0], 0.90) == 9.1
    assert metrics.n_days == 2
    assert metrics.avg_intraday_range_c == 5.5
    assert metrics.p90_intraday_range_c == 9.1
    assert metrics.max_3h_move_c == 10.0
    assert metrics.max_6h_move_c == 5.0


def test_build_city_row_penalizes_missing_station_and_review() -> None:
    row = build_city_volatility_row(
        _city("unknown", needs_review=True, station_code=None),
        [],
        {},
        min_samples=120,
    )

    assert row.reward_volatility_score == 0.0
    assert row.data_quality == "missing_station,needs_review,no_forecast_pairs,no_intraday"


async def test_persist_city_volatility_writes_latest_ranking(session: AsyncSession) -> None:
    row = build_city_volatility_row(
        _city("seoul"),
        [ForecastErrorRecord(date(2026, 6, 1), "gfs", 1, 20.0, 25.0)],
        {date(2026, 6, 1): [15.0, 20.0, 28.0]},
        min_samples=1,
    )
    computed_at = datetime(2026, 6, 11, tzinfo=UTC)

    written = await persist_city_volatility(
        session,
        [row],
        computed_at=computed_at,
        params_json='{"days": 730}',
    )
    saved = (
        await session.execute(select(CityVolatilityMetric))
    ).scalar_one()

    assert written == 1
    assert saved.city_slug == "seoul"
    assert saved.computed_at == computed_at
    assert saved.n_samples == 1
    assert saved.tail_miss_rate_3c == 1.0
    assert saved.lead_mae_json == '{"1": 5.0}'
    assert saved.params_json == '{"days": 730}'


async def test_city_volatility_ranks_forecast_surprise_above_stability(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 6, 11, tzinfo=UTC)
    stable = _city("stable")
    wild = _city("wild")
    session.add_all([stable, wild])

    for index, target_date in enumerate(
        [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    ):
        session.add_all(
            [
                ForecastSnapshot(
                    fetched_at=now,
                    city_slug="stable",
                    source="historical",
                    model="gfs",
                    target_date=target_date,
                    lead_days=1,
                    tmax_c=20.0,
                    n_members=0,
                ),
                DailyObservedMax(
                    city_slug="stable",
                    target_date=target_date,
                    tmax_c=20.0 + (0.2 * index),
                    source="era5",
                ),
                ForecastSnapshot(
                    fetched_at=now,
                    city_slug="wild",
                    source="historical",
                    model="gfs",
                    target_date=target_date,
                    lead_days=1,
                    tmax_c=20.0,
                    n_members=0,
                ),
                DailyObservedMax(
                    city_slug="wild",
                    target_date=target_date,
                    tmax_c=[25.0, 16.0, 26.0][index],
                    source="era5",
                ),
            ]
        )

    rows = await compute_city_volatility(
        session,
        list((await session.execute(select(City))).scalars().all()),
        start=date(2026, 6, 1),
        end=date(2026, 6, 3),
        min_samples=2,
        intraday_by_city={
            "stable": {date(2026, 6, 1): [18.0, 20.0, 21.0]},
            "wild": {date(2026, 6, 1): [8.0, 12.0, 25.0, 18.0]},
        },
    )

    assert [row.city_slug for row in rows] == ["wild", "stable"]
    assert rows[0].forecast_mae_c > rows[1].forecast_mae_c
    assert rows[0].tail_miss_rate_3c == 1.0
    assert rows[1].data_quality == "ok"


async def test_duplicate_forecasts_use_latest_snapshot(session: AsyncSession) -> None:
    city = _city("dupe")
    session.add(city)
    session.add(
        DailyObservedMax(
            city_slug="dupe",
            target_date=date(2026, 6, 1),
            tmax_c=25.0,
            source="era5",
        )
    )
    session.add_all(
        [
            ForecastSnapshot(
                fetched_at=datetime(2026, 6, 10, tzinfo=UTC),
                city_slug="dupe",
                source="historical",
                model="gfs",
                target_date=date(2026, 6, 1),
                lead_days=1,
                tmax_c=10.0,
                n_members=0,
            ),
            ForecastSnapshot(
                fetched_at=datetime(2026, 6, 11, tzinfo=UTC),
                city_slug="dupe",
                source="historical",
                model="gfs",
                target_date=date(2026, 6, 1),
                lead_days=1,
                tmax_c=24.0,
                n_members=0,
            ),
        ]
    )

    rows = await compute_city_volatility(
        session,
        [city],
        start=date(2026, 6, 1),
        end=date(2026, 6, 1),
        min_samples=1,
    )

    assert rows[0].n_samples == 1
    assert rows[0].forecast_mae_c == 1.0


def test_default_settings_keep_city_universe_small() -> None:
    assert Settings().cities == ["seoul", "tokyo", "hong-kong"]
