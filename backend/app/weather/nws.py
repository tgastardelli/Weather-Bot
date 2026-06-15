"""Cliente mínimo da NWS (api.weather.gov) para cidades dos EUA.

Exige header User-Agent identificável (política da NWS). Complementa o
Open-Meteo nas cidades americanas; expansão fica para fase futura.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

NWS_BASE = "https://api.weather.gov"


@dataclass(frozen=True)
class NwsObservation:
    station: str
    observed_at: datetime
    temp_c: float


class NwsClient:
    def __init__(self, http: httpx.AsyncClient, user_agent: str) -> None:
        self._http = http
        self._headers = {"User-Agent": user_agent, "Accept": "application/geo+json"}

    async def latest_observation(self, station: str) -> NwsObservation | None:
        resp = await self._http.get(
            f"{NWS_BASE}/stations/{station}/observations/latest", headers=self._headers
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        props = data.get("properties") or {}
        temp = (props.get("temperature") or {}).get("value")
        timestamp = props.get("timestamp")
        if temp is None or not timestamp:
            return None
        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return NwsObservation(
            station=station.upper(),
            observed_at=dt if dt.tzinfo else dt.replace(tzinfo=UTC),
            temp_c=float(temp),
        )
