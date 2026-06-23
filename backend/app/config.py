"""Configurações da aplicação (pydantic-settings).

Regras (trading-safety): paper é o modo default; live exige flag explícita
no .env e nunca é assumido em código.
"""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mode: Literal["paper", "live"] = "paper"
    db_url: str = "sqlite+aiosqlite:///./data/bot.db"

    # Coleta
    collectors_enabled: bool = True
    markets_interval_minutes: int = 15
    forecasts_interval_minutes: int = 60
    observations_interval_minutes: int = 60
    resolutions_interval_minutes: int = 30
    weekly_validation_enabled: bool = True
    weekly_validation_day_of_week: str = "sun"
    weekly_validation_hour_utc: int = 18
    weekly_validation_minute_utc: int = 0
    validation_history_days: int = 730
    validation_min_samples: int = 120
    # Universo inicial de evidência: caminho balanceado com três cidades foco.
    # None = todas as cidades ativas do registry.
    cities: list[str] | None = ["seoul", "tokyo", "hong-kong"]
    book_depth_levels: int = 20
    forecast_days: int = 5
    ensemble_models: list[str] = ["gfs025", "ecmwf_ifs025"]
    deterministic_models: list[str] = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]
    nws_user_agent: str = "WeatherBot/0.1 (set-contact-in-env)"

    # Estratégia (parâmetros — sem números mágicos no código)
    bankroll: Decimal = Decimal("1000")
    kelly_fraction: Decimal = Decimal("0.15")
    min_edge_net: Decimal = Decimal("0.08")
    longshot_max_price: Decimal = Decimal("0.20")
    min_hours_to_close: float = 2.0
    max_hours_to_close: float = 72.0
    taker_fee_rate: Decimal = Decimal("0.05")  # weather_fees: 5% taker-only
    prob_clamp_epsilon: float = 0.005
    spread_inflation: float = 1.0  # >1 infla o spread do ensemble (subdispersão)
    strategy_policy_mode: Literal["raw", "repair_v2", "repair_v3", "repair_v4", "repair_v5"] = (
        "raw"
    )
    shadow_policy_mode: Literal[
        "off",
        "flexible_validation_v1",
        "discovery_v3",
        "discovery_v4_shadow",
        "high_reward_shadow_v1",
    ] = "off"

    # Risco (rule trading-safety — válido já no paper)
    max_stake_per_order: Decimal = Decimal("10")
    max_exposure_per_market: Decimal = Decimal("50")
    max_daily_loss: Decimal = Decimal("25")

    # Execucao paper-only para provar medicao antes de qualquer capital real.
    paper_trading_enabled: bool = True
    paper_initial_cash: Decimal = Decimal("1000")
    paper_book_stale_seconds: int = 3600

    # Live readiness remains double-locked until a separate explicit approval phase.
    live_trading_enabled: bool = False
    live_bankroll_cap: Decimal = Decimal("100")
    live_kill_switch_enabled: bool = True
    live_kill_switch_engaged: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
