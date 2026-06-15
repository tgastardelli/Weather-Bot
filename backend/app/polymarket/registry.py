"""Extração de metadados de resolução da `description` dos mercados.

A fonte oficial varia por cidade (skill polymarket-api §7): Wunderground
(URL contém a estação ICAO), observatórios oficiais (ex.: HKO), etc.
"""

import re
from dataclasses import dataclass
from typing import Literal

_WUNDERGROUND_RE = re.compile(
    r"wunderground\.com/history/daily/(?P<cc>[a-z]{2})/(?P<city>[a-z-]+)/(?P<icao>[A-Z0-9]{4})",
    re.IGNORECASE,
)
_HKO_RE = re.compile(r"weather\.gov\.hk", re.IGNORECASE)
_UNIT_F_RE = re.compile(r"°F|degrees Fahrenheit", re.IGNORECASE)
_ONE_DECIMAL_RE = re.compile(r"(one|1) decimal place", re.IGNORECASE)


@dataclass(frozen=True)
class ResolutionMeta:
    source: str
    url: str | None
    station_code: str | None
    unit: Literal["C", "F"]
    rounding: Literal["round", "floor"]


def extract_resolution_meta(description: str) -> ResolutionMeta:
    """Heurística sobre a description; cidades sem padrão conhecido => needs_review."""
    unit: Literal["C", "F"] = "F" if _UNIT_F_RE.search(description) else "C"

    if m := _WUNDERGROUND_RE.search(description):
        return ResolutionMeta(
            source="wunderground",
            url=m.group(0),
            station_code=m["icao"].upper(),
            unit=unit,
            # Wunderground publica máximas em graus inteiros — bucket por arredondamento
            rounding="round",
        )
    if _HKO_RE.search(description):
        return ResolutionMeta(
            source="hong_kong_observatory",
            url="https://www.weather.gov.hk/en/cis/climat.htm",
            station_code="HKO",
            unit="C",
            # HKO publica com 1 casa decimal; 28.x cai no bucket "28°C"
            rounding="floor",
        )
    return ResolutionMeta(
        source="unknown",
        url=None,
        station_code=None,
        unit=unit,
        rounding="round",
    )


# Coordenadas das estações de resolução conhecidas (lat, lon, timezone IANA).
# Fonte: localização oficial das estações; usadas pelas APIs de previsão —
# NUNCA usar o centro da cidade (edge #2 da pesquisa).
KNOWN_STATIONS: dict[str, tuple[float, float, str]] = {
    "RKSI": (37.4602, 126.4407, "Asia/Seoul"),  # Incheon Intl — Seoul
    "HKO": (22.3019, 114.1742, "Asia/Hong_Kong"),  # Hong Kong Observatory
    "KLGA": (40.7769, -73.8740, "America/New_York"),  # LaGuardia — NYC
    "KMDW": (41.7868, -87.7522, "America/Chicago"),  # Midway — Chicago
    "KDAL": (32.8471, -96.8517, "America/Chicago"),  # Love Field — Dallas
    "KMIA": (25.7959, -80.2870, "America/New_York"),  # Miami Intl
    "KATL": (33.6407, -84.4277, "America/New_York"),  # Hartsfield — Atlanta
    "KPHL": (39.8729, -75.2437, "America/New_York"),  # Philadelphia Intl
    "KDEN": (39.8561, -104.6737, "America/Denver"),  # Denver Intl
    "KAUS": (30.1975, -97.6664, "America/Chicago"),  # Austin-Bergstrom
    "EGLL": (51.4775, -0.4614, "Europe/London"),  # Heathrow — London
    "LFPB": (48.9694, 2.4414, "Europe/Paris"),  # Le Bourget — Paris
    "RJTT": (35.5533, 139.7811, "Asia/Tokyo"),  # Haneda — Tokyo
    "ZBAA": (40.0801, 116.5846, "Asia/Shanghai"),  # Capital — Beijing
    "ZSSS": (31.1979, 121.3363, "Asia/Shanghai"),  # Hongqiao — Shanghai
    "SAEZ": (-34.8222, -58.5358, "America/Argentina/Buenos_Aires"),  # Ezeiza — Buenos Aires
    "FACT": (-33.9648, 18.6017, "Africa/Johannesburg"),  # Cape Town Intl
}


def station_info(station_code: str | None) -> tuple[float, float, str] | None:
    if station_code is None:
        return None
    return KNOWN_STATIONS.get(station_code.upper())
