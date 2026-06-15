"""Strategy math tests."""

from decimal import Decimal

from app.polymarket.normalize import Bucket
from app.strategy.edge import cost_per_share, net_edge, taker_fee_per_share
from app.strategy.probability import bucket_probabilities
from app.strategy.sizing import kelly_fraction_raw, kelly_stake


def test_bucket_probabilities_from_synthetic_ensemble() -> None:
    buckets = [
        Bucket("below", "C", None, Decimal("23")),
        Bucket("exact", "C", Decimal("24"), Decimal("24")),
        Bucket("above", "C", Decimal("25"), None),
    ]

    probs = bucket_probabilities(
        [22.8, 24.2, 25.1, 27.0],
        buckets,
        unit="C",
        rounding="round",
    )

    assert probs == [0.25, 0.25, 0.5]


def test_edge_includes_weather_taker_fee() -> None:
    price = Decimal("0.20")
    fee = taker_fee_per_share(price, Decimal("0.05"))

    assert fee == Decimal("0.00800")
    assert cost_per_share(price, Decimal("0.05")) == Decimal("0.20800")
    assert net_edge(0.31, price, Decimal("0.05")) == Decimal("0.10200")


def test_weather_taker_fee_uses_polymarket_probability_formula() -> None:
    fee_rate = Decimal("0.05")

    assert taker_fee_per_share(Decimal("0.20"), fee_rate) == Decimal("0.00800")
    assert taker_fee_per_share(Decimal("0.50"), fee_rate) == Decimal("0.01250")
    assert taker_fee_per_share(Decimal("0.95"), fee_rate) == Decimal("0.00238")


def test_kelly_stake_is_capped_and_quantized() -> None:
    raw = kelly_fraction_raw(0.35, Decimal("0.20800"))
    stake = kelly_stake(
        0.35,
        Decimal("0.20800"),
        bankroll=Decimal("1000"),
        kelly_multiplier=Decimal("0.15"),
        max_stake_per_order=Decimal("10"),
    )

    assert raw > 0
    assert stake == Decimal("10.00")
