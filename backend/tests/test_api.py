"""API tests using ASGITransport, no real server."""

from datetime import UTC, date, datetime
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    City,
    CityEdgeRankingRun,
    CityOnboardingRun,
    CityPromotionApplyRun,
    CityResearchAuditRun,
    CityResolutionPromotionAuditRun,
    CityVolatilityMetric,
    DiscoveryCandidateAuditRun,
    Event,
    FeatureCandidateAuditRun,
    FeatureDiscoveryRun,
    HighRewardCityHuntRun,
    HistoricalValidationRun,
    HistoryBackfillRun,
    Market,
    MarketPriceSnapshot,
    StrategyDiscoveryRun,
    StrategyExperimentRun,
    StrategyHypothesisAuditRun,
    StrategyRepairRun,
    StrategyShadowDecision,
    WeatherCityDiscoveryRun,
)
from app.main import app


async def test_markets_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="seoul",
                name="Seoul",
                series_slug="seoul-daily-weather",
                station_code="RKSI",
                station_name=None,
                latitude=37.4602,
                longitude=126.4407,
                timezone="Asia/Seoul",
                unit="C",
                resolution_source="wunderground",
                resolution_url=None,
                rounding="round",
                needs_review=False,
                active=True,
                updated_at=now,
            )
        )
        session.add(
            Event(
                id="123",
                slug="highest-temperature-in-seoul-on-june-10-2026",
                title="Highest temperature in Seoul on June 10, 2026?",
                city_slug="seoul",
                target_date=date(2026, 6, 10),
                end_date=datetime(2026, 6, 11, 12, tzinfo=UTC),
                neg_risk_market_id=None,
                active=True,
                closed=False,
                volume=None,
                liquidity=None,
                first_seen_at=now,
                updated_at=now,
            )
        )
        session.add(
            Market(
                id="m1",
                event_id="123",
                condition_id="0xcond",
                question="Will it be 23°C or below?",
                group_item_title="23°C or below",
                group_item_threshold=0,
                bucket_kind="below",
                bucket_low=None,
                bucket_high=Decimal("23"),
                yes_token_id="yes-token",
                no_token_id="no-token",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=False,
                winner=None,
                resolved_at=None,
                updated_at=now,
            )
        )
        session.add(
            MarketPriceSnapshot(
                ts=now,
                market_id="m1",
                best_bid=Decimal("0.10"),
                best_ask=Decimal("0.12"),
                mid=Decimal("0.11"),
                bid_size=Decimal("100"),
                ask_size=Decimal("50"),
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/markets")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["city_slug"] == "seoul"
    assert body[0]["buckets"][0]["best_ask"] == "0.12"


async def test_city_volatility_endpoint_returns_empty_without_saved_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-volatility")

    assert response.status_code == 200
    assert response.json() == []


async def test_city_edge_ranking_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 18, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            CityEdgeRankingRun(
                run_at=run_at,
                status="READY_FOR_TARGETED_DISCOVERY",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 18),
                summary_json='{"best_live_city": "seoul", "cannot_approve_live": true}',
                cities_json='[{"city_slug": "seoul", "profile": {"total_pnl": "1.23"}}]',
                research_json='[{"city_slug": "nyc", "needs_review": true}]',
                gates_json='{"live_release": {"passed": false, "value": "diagnostic_only"}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-edge-ranking")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["run_at"] == "2026-06-18T00:00:00Z"
    assert body["latest"]["status"] == "READY_FOR_TARGETED_DISCOVERY"
    assert '"total_pnl": "1.23"' in body["latest"]["cities_json"]


async def test_weather_city_discovery_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 19, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            WeatherCityDiscoveryRun(
                run_at=run_at,
                status="DISCOVERED_NEW_CITIES",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 19),
                summary_json='{"new_cities_registered": 1}',
                cities_json='[{"city_slug": "chicago", "registered_as_needs_review": true}]',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/weather-city-discovery")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "DISCOVERED_NEW_CITIES"
    assert '"city_slug": "chicago"' in body["latest"]["cities_json"]


async def test_city_resolution_promotion_audit_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 19, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            CityResolutionPromotionAuditRun(
                run_at=run_at,
                status="READY_FOR_EXPANDED_DISCOVERY",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 19),
                cities_json='["chicago"]',
                summary_json='{"promotable_cities": ["chicago"]}',
                resolution_json='{"cities": [{"city_slug": "chicago"}]}',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-resolution-promotion-audit")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "READY_FOR_EXPANDED_DISCOVERY"
    assert '"promotable_cities": ["chicago"]' in body["latest"]["summary_json"]


async def test_city_promotion_apply_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 19, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            CityPromotionApplyRun(
                run_at=run_at,
                status="PROMOTED",
                requested_cities_json='["london"]',
                promoted_cities_json='[{"city_slug": "london", "mismatch_rate": "0.0000"}]',
                blocked_json="[]",
                summary_json='{"promoted_cities": ["london"], "cannot_approve_live": true}',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-promotion-apply")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["run_at"] == "2026-06-19T00:00:00Z"
    assert body["latest"]["status"] == "PROMOTED"
    assert '"promoted_cities": ["london"]' in body["latest"]["summary_json"]


async def test_city_volatility_endpoint_returns_latest_saved_ranking(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_run = datetime(2026, 6, 10, tzinfo=UTC)
    latest_run = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add_all(
            [
                CityVolatilityMetric(
                    computed_at=first_run,
                    city_slug="old-city",
                    station_code="KOLD",
                    n_samples=10,
                    forecast_mae_c=1.0,
                    tail_miss_rate_2c=0.1,
                    tail_miss_rate_3c=0.0,
                    tail_miss_rate_5c=0.0,
                    upside_surprise_rate_3c=0.0,
                    downside_surprise_rate_3c=0.0,
                    avg_intraday_range_c=5.0,
                    p90_intraday_range_c=6.0,
                    max_3h_move_c=2.0,
                    max_6h_move_c=3.0,
                    reward_volatility_score=10.0,
                    data_quality="ok",
                    lead_mae_json="{}",
                    params_json="{}",
                ),
                CityVolatilityMetric(
                    computed_at=latest_run,
                    city_slug="wild",
                    station_code="KWLD",
                    n_samples=100,
                    forecast_mae_c=4.0,
                    tail_miss_rate_2c=0.6,
                    tail_miss_rate_3c=0.4,
                    tail_miss_rate_5c=0.1,
                    upside_surprise_rate_3c=0.3,
                    downside_surprise_rate_3c=0.1,
                    avg_intraday_range_c=12.0,
                    p90_intraday_range_c=18.0,
                    max_3h_move_c=7.0,
                    max_6h_move_c=9.0,
                    reward_volatility_score=85.0,
                    data_quality="ok",
                    lead_mae_json='{"1": 4.0}',
                    params_json='{"days": 730}',
                ),
                CityVolatilityMetric(
                    computed_at=latest_run,
                    city_slug="stable",
                    station_code="KSTB",
                    n_samples=100,
                    forecast_mae_c=1.0,
                    tail_miss_rate_2c=0.05,
                    tail_miss_rate_3c=0.01,
                    tail_miss_rate_5c=0.0,
                    upside_surprise_rate_3c=0.01,
                    downside_surprise_rate_3c=0.0,
                    avg_intraday_range_c=4.0,
                    p90_intraday_range_c=5.0,
                    max_3h_move_c=2.0,
                    max_6h_move_c=3.0,
                    reward_volatility_score=20.0,
                    data_quality="low_samples",
                    lead_mae_json='{"1": 1.0}',
                    params_json='{"days": 730}',
                ),
            ]
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-volatility")

    assert response.status_code == 200
    body = response.json()
    assert [row["city_slug"] for row in body] == ["wild", "stable"]
    assert body[0]["computed_at"] == "2026-06-11T00:00:00Z"
    assert body[0]["reward_volatility_score"] == 85.0
    assert body[0]["lead_mae_json"] == '{"1": 4.0}'


async def test_historical_validation_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            HistoricalValidationRun(
                run_at=run_at,
                status="FAILED",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["seoul"]',
                data_health_json='{"market_price_history_points": 10}',
                model_health_json='{"min_forecast_observed_pairs": 120}',
                trading_json=(
                    '{"execution_proxy": "polymarket_prices_history_last_price_no_book_depth", '
                    '"profiles": {"max_edge": {"total_pnl": "-1.23"}}}'
                ),
                gates_json='{"historical_pnl": {"passed": false, "value": "-1.23"}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/historical-validation")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["run_at"] == "2026-06-11T00:00:00Z"
    assert body["latest"]["status"] == "FAILED"
    assert '"total_pnl": "-1.23"' in body["latest"]["trading_json"]
    assert body["history"][0]["data_health_json"] == '{"market_price_history_points": 10}'


async def test_history_backfill_endpoint_returns_latest_windows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            HistoryBackfillRun(
                run_at=run_at,
                completed_at=run_at,
                status="COMPLETED",
                window_start=date(2026, 6, 1),
                window_end=date(2026, 6, 7),
                cities_json='["seoul"]',
                interval="1d",
                probe_trades=False,
                events_seen=1,
                markets_upserted=11,
                history_points=0,
                trade_history_points=100,
                rejected_trade_sources=0,
                source_status_json='{"accepted": 11}',
                errors_json="[]",
                params_json='{"chunk_days": 7}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/history-backfill")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "COMPLETED"
    assert body["latest"]["trade_history_points"] == 100
    assert body["latest"]["completed_at"] == "2026-06-11T00:00:00Z"


async def test_strategy_repair_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            StrategyRepairRun(
                run_at=run_at,
                status="NEEDS_MODEL_REPAIR",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["seoul"]',
                summary_json=(
                    '{"best_variant": "calibrated_cap", "best_variant_pnl": "12.34", '
                    '"best_variant_brier_delta": -0.01}'
                ),
                baseline_json='{"profiles": {"max_edge": {"total_pnl": "-9.99"}}}',
                variants_json=(
                    '[{"name": "baseline", "profiles": {"max_edge": '
                    '{"total_pnl": "-9.99"}}}]'
                ),
                best_variant_json=(
                    '{"name": "calibrated_cap", "profiles": {"max_edge": '
                    '{"total_pnl": "12.34"}}}'
                ),
                gates_json='{"max_edge_brier": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/strategy-repair")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "NEEDS_MODEL_REPAIR"
    assert body["latest"]["run_at"] == "2026-06-11T00:00:00Z"
    assert '"best_variant_pnl": "12.34"' in body["latest"]["summary_json"]
    assert body["history"][0]["best_variant_json"].startswith('{"name": "calibrated_cap"')


async def test_strategy_hypothesis_audit_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            StrategyHypothesisAuditRun(
                run_at=run_at,
                status="NO_STABLE_HISTORICAL_EDGE",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["seoul"]',
                summary_json='{"next_action": "review_model_hypothesis"}',
                blockers_json='["no_oos_segment_recurrence"]',
                timing_json='{"valid": true}',
                bucket_audit_json='{"valid": true}',
                stability_json='{"oos_trades_in_selected_policy": 0}',
                segments_json='{"worst_segments": []}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/strategy-hypothesis-audit")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "NO_STABLE_HISTORICAL_EDGE"
    assert body["latest"]["run_at"] == "2026-06-11T00:00:00Z"
    assert '"no_oos_segment_recurrence"' in body["latest"]["blockers_json"]


async def test_strategy_experiments_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            StrategyExperimentRun(
                run_at=run_at,
                status="READY_FOR_SHADOW_PAPER",
                experiment_set="flexible_validation_v1",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["seoul"]',
                summary_json=(
                    '{"diagnostic_only": true, "best_variant": "flex_v1", '
                    '"cannot_approve_live": true}'
                ),
                variants_json='[{"name": "flex_v1"}]',
                best_variant_json='{"name": "flex_v1"}',
                gates_json='{"live_release": {"passed": false}}',
                shadow_json='{"forward_shadow_enabled": false}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/strategy-experiments")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "READY_FOR_SHADOW_PAPER"
    assert body["latest"]["experiment_set"] == "flexible_validation_v1"
    assert '"cannot_approve_live": true' in body["latest"]["summary_json"]


async def test_strategy_discovery_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            StrategyDiscoveryRun(
                run_at=run_at,
                status="NO_EDGE_FOUND",
                universe="research",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["seoul"]',
                summary_json='{"diagnostic_only": true, "cannot_approve_live": true}',
                families_json='{"tested": ["model_value"]}',
                best_family_json='{"family": "model_value"}',
                folds_json='[]',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/strategy-discovery")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "NO_EDGE_FOUND"
    assert body["latest"]["universe"] == "research"
    assert '"cannot_approve_live": true' in body["latest"]["summary_json"]


async def test_feature_discovery_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            FeatureDiscoveryRun(
                run_at=run_at,
                status="NO_FEATURE_EDGE",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["dallas"]',
                summary_json='{"cannot_approve_live": true, "best_family_pnl": "-1.23"}',
                families_json='{"tested": ["ensemble_confidence_value"]}',
                best_family_json='{"family": "ensemble_confidence_value"}',
                folds_json="[]",
                gates_json="{}",
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/feature-discovery")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "NO_FEATURE_EDGE"
    assert body["latest"]["run_at"] == "2026-06-11T00:00:00Z"
    assert '"best_family_pnl": "-1.23"' in body["latest"]["summary_json"]


async def test_feature_candidate_audit_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 22, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            FeatureCandidateAuditRun(
                run_at=run_at,
                status="CANDIDATE_REVIEW",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 22),
                feature_discovery_run_id=7,
                cities_json='["dallas"]',
                summary_json='{"explanation": "positive_pnl_with_negative_brier"}',
                profile_json='{"total_pnl": "1.23"}',
                segments_json='{"by_segment": []}',
                decision_trace_json='{"samples": []}',
                gates_json="{}",
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/feature-candidate-audit")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "CANDIDATE_REVIEW"
    assert body["latest"]["feature_discovery_run_id"] == 7
    assert "positive_pnl_with_negative_brier" in body["latest"]["summary_json"]


async def test_discovery_candidate_audit_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            DiscoveryCandidateAuditRun(
                run_at=run_at,
                status="CANDIDATE_REVIEW",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                discovery_run_id=1,
                cities_json='["nyc"]',
                summary_json='{"diagnostic_only": true, "cannot_approve_live": true}',
                concentration_json='{"top_city": "nyc"}',
                folds_json="[]",
                city_resolution_json='{"valid": false}',
                timing_json='{"valid": true}',
                segments_json='{"blocked_counts": {}}',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/discovery-candidate-audit")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "CANDIDATE_REVIEW"


async def test_high_reward_city_hunt_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 22, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            HighRewardCityHuntRun(
                run_at=run_at,
                status="READY_FOR_SHADOW_FAST_LANE",
                window_start=date(2025, 1, 1),
                window_end=date(2026, 6, 22),
                cities_json='["dallas", "seattle", "tokyo"]',
                summary_json='{"diagnostic_only": true, "approved_city_count": 3}',
                rankings_json='{"best_per_city": []}',
                candidates_json='{"approved": []}',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/high-reward-city-hunt")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "READY_FOR_SHADOW_FAST_LANE"
    assert body["latest"]["summary_json"] == '{"diagnostic_only": true, "approved_city_count": 3}'


async def test_strategy_shadow_endpoint_returns_money_as_strings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            City(
                slug="dallas",
                name="Dallas",
                series_slug="dallas-daily-weather",
                station_code="KDAL",
                station_name=None,
                latitude=32.8,
                longitude=-96.8,
                timezone="America/Chicago",
                unit="F",
                resolution_source="wunderground",
                resolution_url=None,
                rounding="round",
                needs_review=False,
                active=True,
                updated_at=now,
            )
        )
        session.add(
            Event(
                id="event-shadow",
                slug="highest-temperature-in-dallas-on-june-11-2026",
                title="Highest temperature in Dallas on June 11?",
                city_slug="dallas",
                target_date=date(2026, 6, 11),
                end_date=now,
                neg_risk_market_id=None,
                active=False,
                closed=True,
                volume=None,
                liquidity=None,
                first_seen_at=now,
                updated_at=now,
            )
        )
        session.add(
            Market(
                id="market-shadow",
                event_id="event-shadow",
                condition_id="0xshadow",
                question="Will it be hot?",
                group_item_title="90°F",
                group_item_threshold=1,
                bucket_kind="exact",
                bucket_low=Decimal("90"),
                bucket_high=Decimal("90"),
                yes_token_id="yes-shadow",
                no_token_id="no-shadow",
                tick_size=Decimal("0.001"),
                min_order_size=Decimal("5"),
                closed=True,
                winner=False,
                resolved_at=now,
                updated_at=now,
            )
        )
        session.add(
            StrategyShadowDecision(
                ts=now,
                policy_name="discovery_v4_shadow",
                market_id="market-shadow",
                event_id="event-shadow",
                city_slug="dallas",
                target_date=date(2026, 6, 11),
                raw_prob=0.20,
                calibrated_prob=0.30,
                market_price=Decimal("0.40000"),
                edge_net=Decimal("0.12000"),
                reason=None,
                would_trade=True,
                segment_key="segment",
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/strategy-shadow")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"][0]["market_price"] == "0.40000"
    assert body["latest"][0]["edge_net"] == "0.12000"


async def test_city_research_audit_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            CityResearchAuditRun(
                run_at=run_at,
                status="READY_FOR_RESEARCH",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                summary_json=(
                    '{"diagnostic_only": true, "cannot_approve_live": true, '
                    '"live_eligible": 1, "research_only": 1}'
                ),
                cities_json='[{"city_slug": "seoul", "classification": "live_eligible"}]',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-research-audit")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "READY_FOR_RESEARCH"
    assert '"cannot_approve_live": true' in body["latest"]["summary_json"]


async def test_city_onboarding_endpoint_returns_latest_report(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    run_at = datetime(2026, 6, 11, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            CityOnboardingRun(
                run_at=run_at,
                status="DATA_REVIEW",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 11),
                cities_json='["nyc"]',
                summary_json='{"diagnostic_only": true, "cannot_approve_live": true}',
                checks_json='[{"city_slug": "nyc", "classification": "excluded"}]',
                gates_json='{"live_release": {"passed": false}}',
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/city-onboarding")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"]["status"] == "DATA_REVIEW"
    assert body["latest"]["cities_json"] == '["nyc"]'


async def test_live_readiness_endpoint_blocks_by_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/live-readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "BLOCKED"
    assert body["mode"] == "paper"
    assert "mode_live" in body["blockers"]
    assert body["ready_for_live_review"] is False


async def test_high_reward_paper_status_endpoint_returns_current_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 6, 14, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        session.add(
            StrategyRepairRun(
                run_at=now,
                status="PROMISING",
                window_start=date(2026, 1, 1),
                window_end=date(2026, 6, 14),
                cities_json='["atlanta","seattle","toronto"]',
                summary_json="{}",
                baseline_json="{}",
                variants_json="[]",
                best_variant_json=(
                    '{"policy_name":"repair_v5_high_reward_v1",'
                    '"policy_version":"repair_v5_high_reward",'
                    '"active_cities":["atlanta","seattle","toronto"],'
                    '"side_by_city":{"atlanta":"YES","seattle":"YES","toronto":"NO"}}'
                ),
                gates_json="{}",
            )
        )

    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/analysis/high-reward-paper-status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PAPER_NOT_STARTED"
    assert body["policy_name"] == "repair_v5_high_reward_v1"
    assert body["side_by_city"]["toronto"] == "NO"
    assert body["live_release"] is False
