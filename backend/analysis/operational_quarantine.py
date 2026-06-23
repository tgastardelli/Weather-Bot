"""Operational quarantine rules for research-only weather cities.

Quarantined cities can remain in diagnostics, but cannot be used for repair,
shadow/paper execution approval, or live-readiness.
"""

from __future__ import annotations

from typing import Final

OPERATIONAL_QUARANTINE: Final[dict[str, dict[str, object]]] = {
    "nyc": {
        "reasons": [
            "resolution_not_verified",
            "era5_mismatch_rate=0.2288",
            "wunderground_unusable_constant_series",
        ],
        "allowed_scope": "research_only_diagnostic",
        "blocked_from": ["repair_v5", "shadow_paper", "paper_execution", "live_readiness"],
    }
}


def is_operationally_quarantined(city_slug: str | None) -> bool:
    return city_slug in OPERATIONAL_QUARANTINE if city_slug is not None else False


def quarantine_reasons(city_slug: str | None) -> list[str]:
    if city_slug is None:
        return []
    payload = OPERATIONAL_QUARANTINE.get(city_slug)
    if payload is None:
        return []
    reasons = payload.get("reasons")
    return [str(reason) for reason in reasons] if isinstance(reasons, list) else []


def quarantine_payload(city_slug: str | None) -> dict[str, object] | None:
    if city_slug is None:
        return None
    payload = OPERATIONAL_QUARANTINE.get(city_slug)
    if payload is None:
        return None
    return {"city_slug": city_slug, **payload}


def quarantine_payloads(city_slugs: list[str] | set[str]) -> list[dict[str, object]]:
    return [
        payload
        for city_slug in sorted(city_slugs)
        if (payload := quarantine_payload(city_slug)) is not None
    ]


def split_operational_cities(city_slugs: list[str]) -> tuple[list[str], list[str]]:
    operational = [city for city in city_slugs if not is_operationally_quarantined(city)]
    quarantined = [city for city in city_slugs if is_operationally_quarantined(city)]
    return operational, quarantined
