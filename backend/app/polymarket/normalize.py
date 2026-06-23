"""Normalização dos payloads Gamma de mercados de clima.

Campos como `clobTokenIds`/`outcomes`/`outcomePrices` chegam como STRING JSON
(skill polymarket-api §9) — o parse é explícito e validado aqui.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

BucketKind = Literal["below", "exact", "range", "above"]

_EVENT_SLUG_RE = re.compile(r"^highest-temperature-in-(?P<city>[a-z0-9-]+)-on-")
_EVENT_DATE_SLUG_RE = re.compile(
    r"^highest-temperature-in-[a-z0-9-]+-on-"
    r"(?P<month>january|february|march|april|may|june|july|august|"
    r"september|october|november|december)-"
    r"(?P<day>\d{1,2})(?:-(?P<year>\d{4}))?$"
)
_BELOW_RE = re.compile(r"^(?P<v>-?\d+(?:\.\d+)?)°(?P<u>[CF]) or below$")
_ABOVE_RE = re.compile(r"^(?P<v>-?\d+(?:\.\d+)?)°(?P<u>[CF]) or (?:higher|above)$")
_EXACT_RE = re.compile(r"^(?P<v>-?\d+(?:\.\d+)?)°(?P<u>[CF])$")
_RANGE_RE = re.compile(r"^(?P<lo>-?\d+(?:\.\d+)?)-(?P<hi>-?\d+(?:\.\d+)?)°(?P<u>[CF])$")
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class Bucket:
    kind: BucketKind
    unit: Literal["C", "F"]
    low: Decimal | None  # None = aberto para baixo
    high: Decimal | None  # None = aberto para cima

    def contains(self, value: Decimal) -> bool:
        if self.low is not None and value < self.low:
            return False
        return not (self.high is not None and value > self.high)


def parse_bucket(group_item_title: str) -> Bucket:
    """Converte `groupItemTitle` (ex.: '28°C', '23°C or below', '84-85°F') em Bucket."""
    title = group_item_title.strip().replace("\u00b0", "°")
    if m := _BELOW_RE.match(title):
        return Bucket("below", m["u"], None, Decimal(m["v"]))  # type: ignore[arg-type]
    if m := _ABOVE_RE.match(title):
        return Bucket("above", m["u"], Decimal(m["v"]), None)  # type: ignore[arg-type]
    if m := _RANGE_RE.match(title):
        return Bucket("range", m["u"], Decimal(m["lo"]), Decimal(m["hi"]))  # type: ignore[arg-type]
    if m := _EXACT_RE.match(title):
        value = Decimal(m["v"])
        return Bucket("exact", m["u"], value, value)  # type: ignore[arg-type]
    raise ValueError(f"groupItemTitle não reconhecido: {group_item_title!r}")


@dataclass(frozen=True)
class NormalizedMarket:
    id: str
    condition_id: str
    question: str
    group_item_title: str
    group_item_threshold: int
    bucket: Bucket
    yes_token_id: str
    no_token_id: str
    tick_size: Decimal
    min_order_size: Decimal
    closed: bool
    outcome_prices: tuple[Decimal, Decimal] | None  # (yes, no) — indicativo, não executável
    description: str


@dataclass(frozen=True)
class NormalizedEvent:
    id: str
    slug: str
    title: str
    city_slug: str
    target_date: date
    end_date: datetime | None
    neg_risk_market_id: str | None
    active: bool
    closed: bool
    volume: float | None
    liquidity: float | None
    markets: list[NormalizedMarket]


def _parse_json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return list(parsed) if isinstance(parsed, list) else []
    if isinstance(raw, list):
        return list(raw)
    return []


def _parse_end_date(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def city_slug_from_event_slug(event_slug: str) -> str | None:
    m = _EVENT_SLUG_RE.match(event_slug)
    return m["city"] if m else None


def target_date_from_event_slug(event_slug: str, end_date: datetime | None) -> date | None:
    """Infer target day from Gamma weather event slugs when available."""
    match = _EVENT_DATE_SLUG_RE.match(event_slug)
    if match is None:
        return None
    month = _MONTHS[match["month"]]
    day = int(match["day"])
    if match["year"]:
        return date(int(match["year"]), month, day)
    if end_date is None:
        return None
    end_day = end_date.date()
    candidates = [
        date(year, month, day) for year in (end_day.year - 1, end_day.year, end_day.year + 1)
    ]
    return min(candidates, key=lambda candidate: abs((candidate - end_day).days))


def _target_date_from_markets(
    markets: list[dict[str, Any]], end_date: datetime | None, event_slug: str
) -> date:
    """Dia-alvo local: gameStartTime quando presente; senão endDate - 1 dia."""
    if slug_date := target_date_from_event_slug(event_slug, end_date):
        return slug_date
    for market in markets:
        raw = market.get("gameStartTime")
        if raw:
            text = str(raw).replace(" ", "T").replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(text).date()
            except ValueError:
                continue
    if end_date is not None:
        return date.fromordinal(end_date.date().toordinal() - 1)
    raise ValueError("evento sem gameStartTime e sem endDate — target_date indeterminado")


def normalize_market(raw: dict[str, Any]) -> NormalizedMarket:
    token_ids = [str(token) for token in _parse_json_list(raw.get("clobTokenIds"))]
    if len(token_ids) != 2:
        raise ValueError(f"clobTokenIds inválido no mercado {raw.get('id')!r}")
    prices_raw = _parse_json_list(raw.get("outcomePrices"))
    prices: tuple[Decimal, Decimal] | None = None
    if len(prices_raw) == 2:
        prices = (Decimal(str(prices_raw[0])), Decimal(str(prices_raw[1])))
    threshold_raw = raw.get("groupItemThreshold") or 0
    return NormalizedMarket(
        id=str(raw["id"]),
        condition_id=str(raw.get("conditionId") or ""),
        question=str(raw.get("question") or ""),
        group_item_title=str(raw.get("groupItemTitle") or ""),
        group_item_threshold=int(Decimal(str(threshold_raw))),
        bucket=parse_bucket(str(raw.get("groupItemTitle") or "")),
        yes_token_id=token_ids[0],
        no_token_id=token_ids[1],
        tick_size=Decimal(str(raw.get("orderPriceMinTickSize") or "0.001")),
        min_order_size=Decimal(str(raw.get("orderMinSize") or "5")),
        closed=bool(raw.get("closed", False)),
        outcome_prices=prices,
        description=str(raw.get("description") or ""),
    )


def normalize_event(raw: dict[str, Any]) -> NormalizedEvent | None:
    """Normaliza evento Gamma; retorna None se não for 'Highest temperature'."""
    slug = str(raw.get("slug") or "")
    city = city_slug_from_event_slug(slug)
    if city is None:
        return None
    markets_raw = [m for m in raw.get("markets") or [] if m.get("clobTokenIds")]
    markets: list[NormalizedMarket] = []
    for market_raw in markets_raw:
        try:
            markets.append(normalize_market(market_raw))
        except (ValueError, KeyError):
            continue  # bucket fora do padrão (ex.: mercado especial) — ignorar
    if not markets:
        return None
    end_date = _parse_end_date(raw.get("endDate"))
    return NormalizedEvent(
        id=str(raw["id"]),
        slug=slug,
        title=str(raw.get("title") or ""),
        city_slug=city,
        target_date=_target_date_from_markets(markets_raw, end_date, slug),
        end_date=end_date,
        neg_risk_market_id=str(raw.get("negRiskMarketID") or "") or None,
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        volume=float(raw["volume"]) if raw.get("volume") is not None else None,
        liquidity=float(raw["liquidity"]) if raw.get("liquidity") is not None else None,
        markets=sorted(markets, key=lambda m: m.group_item_threshold),
    )
