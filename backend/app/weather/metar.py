"""Observações METAR em tempo real (NOAA aviationweather.gov, grátis).

Usadas para acompanhar a máxima intradiária na estação de resolução.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

METAR_URL = "https://aviationweather.gov/api/data/metar"


@dataclass(frozen=True)
class MetarObservation:
    station: str
    observed_at: datetime
    temp_c: float


def _parse_observed_at(raw: dict[str, Any]) -> datetime | None:
    obs_time = raw.get("obsTime")
    if isinstance(obs_time, int | float):
        return datetime.fromtimestamp(float(obs_time), tz=UTC)
    report = raw.get("reportTime") or raw.get("receiptTime")
    if isinstance(report, str):
        text = report.strip().replace(" ", "T").replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


class MetarClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def recent(self, station: str, hours: int = 6) -> list[MetarObservation]:
        resp = await self._http.get(
            METAR_URL, params={"ids": station, "format": "json", "hours": hours}
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []
        observations: list[MetarObservation] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            temp = raw.get("temp")
            observed_at = _parse_observed_at(raw)
            if temp is None or observed_at is None:
                continue
            observations.append(
                MetarObservation(
                    station=str(raw.get("icaoId") or station).upper(),
                    observed_at=observed_at,
                    temp_c=float(temp),
                )
            )
        return observations
