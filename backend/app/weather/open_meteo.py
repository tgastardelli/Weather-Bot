"""Cliente Open-Meteo: previsões determinísticas, ensembles e histórico.

APIs (grátis, sem key; ~10k chamadas/dia):
- Forecast:            https://api.open-meteo.com/v1/forecast
- Ensemble:            https://ensemble-api.open-meteo.com/v1/ensemble
- Historical Forecast: https://historical-forecast-api.open-meteo.com/v1/forecast
- Archive (ERA5):      https://archive-api.open-meteo.com/v1/archive

Sempre nas coordenadas da ESTAÇÃO de resolução, nunca no centro da cidade.
"""

from collections import defaultdict
from datetime import date
from typing import Any

import httpx

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class OpenMeteoClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = await self._http.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"resposta inesperada de {url}")
        return data

    @staticmethod
    def _daily_by_model(
        data: dict[str, Any], models: list[str]
    ) -> dict[str, list[tuple[date, float | None]]]:
        """Extrai daily.temperature_2m_max; com 1 modelo a chave não tem sufixo."""
        daily = data.get("daily") or {}
        dates = [date.fromisoformat(d) for d in daily.get("time", [])]
        out: dict[str, list[tuple[date, float | None]]] = {}
        for model in models:
            key = f"temperature_2m_max_{model}"
            if key not in daily and len(models) == 1:
                key = "temperature_2m_max"
            values = daily.get(key)
            if values is None:
                continue
            out[model] = [
                (d, float(v) if v is not None else None)
                for d, v in zip(dates, values, strict=True)
            ]
        return out

    async def daily_tmax_forecast(
        self, lat: float, lon: float, models: list[str], days: int
    ) -> dict[str, list[tuple[date, float | None]]]:
        """Tmax diária determinística por modelo, no fuso local da estação."""
        data = await self._get_json(
            FORECAST_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max",
                "models": ",".join(models),
                "timezone": "auto",
                "forecast_days": days,
            },
        )
        return self._daily_by_model(data, models)

    async def ensemble_daily_tmax(
        self, lat: float, lon: float, model: str, days: int
    ) -> dict[date, list[float]]:
        """Tmax diária POR MEMBRO do ensemble (máximo do horário no dia local)."""
        data = await self._get_json(
            ENSEMBLE_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m",
                "models": model,
                "timezone": "auto",
                "forecast_days": days,
            },
        )
        hourly = data.get("hourly") or {}
        times: list[str] = hourly.get("time", [])
        day_index: dict[date, list[int]] = defaultdict(list)
        for i, stamp in enumerate(times):
            day_index[date.fromisoformat(stamp[:10])].append(i)

        member_keys = [
            key
            for key in hourly
            if key == "temperature_2m" or key.startswith("temperature_2m_member")
        ]
        result: dict[date, list[float]] = {}
        for day, indices in sorted(day_index.items()):
            members_max: list[float] = []
            for key in member_keys:
                series = hourly[key]
                values = [
                    float(series[i])
                    for i in indices
                    if i < len(series) and series[i] is not None
                ]
                if values:
                    members_max.append(max(values))
            if members_max:
                result[day] = members_max
        return result

    async def historical_daily_tmax(
        self, lat: float, lon: float, models: list[str], start: date, end: date
    ) -> dict[str, list[tuple[date, float | None]]]:
        """Previsões PASSADAS arquivadas (backfill de calibração)."""
        data = await self._get_json(
            HISTORICAL_FORECAST_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max",
                "models": ",".join(models),
                "timezone": "auto",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        return self._daily_by_model(data, models)

    async def era5_daily_tmax(
        self, lat: float, lon: float, start: date, end: date
    ) -> list[tuple[date, float | None]]:
        """Tmax observada (reanálise ERA5) — verdade aproximada p/ calibração."""
        data = await self._get_json(
            ARCHIVE_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max",
                "timezone": "auto",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        daily = data.get("daily") or {}
        dates = [date.fromisoformat(d) for d in daily.get("time", [])]
        values = daily.get("temperature_2m_max", [])
        return [
            (d, float(v) if v is not None else None)
            for d, v in zip(dates, values, strict=True)
        ]

    async def era5_hourly_temperature(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[date, list[float]]:
        """Observed hourly temperature (ERA5), grouped by the station local day."""
        data = await self._get_json(
            ARCHIVE_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m",
                "timezone": "auto",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        hourly = data.get("hourly") or {}
        times = hourly.get("time", [])
        values = hourly.get("temperature_2m", [])
        if not isinstance(times, list) or not isinstance(values, list):
            return {}

        grouped: dict[date, list[float]] = defaultdict(list)
        for stamp, value in zip(times, values, strict=True):
            if value is None:
                continue
            grouped[date.fromisoformat(str(stamp)[:10])].append(float(value))
        return dict(grouped)
