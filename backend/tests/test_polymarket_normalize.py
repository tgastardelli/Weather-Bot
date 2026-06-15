"""Gamma normalization tests."""

from datetime import date
from decimal import Decimal

from app.polymarket.normalize import normalize_event, parse_bucket


def test_parse_bucket_shapes() -> None:
    below = parse_bucket("23°C or below")
    exact = parse_bucket("28°C")
    range_bucket = parse_bucket("84-85°F")
    above = parse_bucket("33°C or higher")

    assert below.kind == "below"
    assert below.low is None
    assert below.high == Decimal("23")
    assert exact.low == exact.high == Decimal("28")
    assert range_bucket.unit == "F"
    assert range_bucket.low == Decimal("84")
    assert range_bucket.high == Decimal("85")
    assert above.high is None


def test_normalize_highest_temperature_event() -> None:
    raw = {
        "id": "123",
        "slug": "highest-temperature-in-seoul-on-june-10-2026",
        "title": "Highest temperature in Seoul on June 10, 2026?",
        "endDate": "2026-06-11T12:00:00Z",
        "active": True,
        "closed": False,
        "volume": "1000",
        "liquidity": "500",
        "negRiskMarketID": "0xabc",
        "markets": [
            {
                "id": "m1",
                "conditionId": "0xcond",
                "question": "Will it be 23°C or below?",
                "groupItemTitle": "23°C or below",
                "groupItemThreshold": "0",
                "clobTokenIds": '["yes-token","no-token"]',
                "outcomePrices": '["0.12","0.88"]',
                "orderPriceMinTickSize": "0.001",
                "orderMinSize": "5",
                "closed": False,
                "gameStartTime": "2026-06-10 00:00:00+00",
                "description": "Resolves using wunderground.com/history/daily/kr/incheon/RKSI",
            }
        ],
    }

    event = normalize_event(raw)

    assert event is not None
    assert event.city_slug == "seoul"
    assert event.target_date == date(2026, 6, 10)
    assert event.markets[0].yes_token_id == "yes-token"
    assert event.markets[0].outcome_prices == (Decimal("0.12"), Decimal("0.88"))
