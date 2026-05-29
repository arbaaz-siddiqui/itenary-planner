"""settings — All Pydantic Settings classes, lazy-loaded.

Each domain has its own class. Streamlit doesn't need Twilio to start;
WhatsApp doesn't need OpenRouter if using Anthropic. Settings load on
first access via @lru_cache.

Override in tests: just construct the class directly.
    BookingApiSettings(token="fake")
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# =============================================================================
# Booking API (ActivityLinker)
# =============================================================================
class BookingApiSettings(BaseSettings):
    """Booking API (Technoheaven via Gujjutours staging) settings.

    Three tenant IDs are used:
      - account tenant: every endpoint EXCEPT the two flight ones
      - flight_list tenant: GET-flight-details endpoint only
      - flight_search tenant: flight search endpoint only

    These come from the client's Postman collection. If Technoheaven rotates
    them, override via env without touching code.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    base_url: str = Field(
        default="https://stagingapi.gujjutours.com", validation_alias="BOOKING_BASE_URL"
    )
    token: str = Field(default="", validation_alias="BOOKING_TOKEN")
    tenant_id: str = Field(
        default="A29CD3EE-D050-A34A-3A53-3A20E4FAF5F3",
        validation_alias="BOOKING_TENANT_ID",
    )
    flight_list_tenant_id: str = Field(
        default="E1047144-1A17-A2D5-E474-3A1DFEF15B7F",
        validation_alias="FLIGHT_LIST_TENANT_ID",
    )
    flight_search_tenant_id: str = Field(
        default="DB1EC027-BDEC-3EA4-EDE7-3A1BE86F63F6",
        validation_alias="FLIGHT_SEARCH_TENANT_ID",
    )
    flight_list_custom_host: str = Field(
        default="newinstance.activitylinker.com",
        validation_alias="FLIGHT_LIST_CUSTOM_HOST",
    )


@lru_cache(maxsize=1)
def get_booking_api_settings() -> BookingApiSettings:
    return BookingApiSettings()


# =============================================================================
# LLM
# =============================================================================
class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    provider: Literal["openrouter", "anthropic", "selfhosted"] = Field(
        default="openrouter", validation_alias="LLM_PROVIDER"
    )
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022", validation_alias="ANTHROPIC_MODEL"
    )
    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(
        default="mistralai/mistral-large-2411", validation_alias="OPENROUTER_MODEL"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    selfhosted_base_url: str = Field(
        default="http://localhost:8000/v1", validation_alias="SELFHOSTED_BASE_URL"
    )
    selfhosted_api_key: str = Field(default="EMPTY", validation_alias="SELFHOSTED_API_KEY")
    selfhosted_model: str = Field(
        default="qwen/qwen-2.5-72b-instruct", validation_alias="SELFHOSTED_MODEL"
    )


@lru_cache(maxsize=1)
def get_llm_settings() -> LlmSettings:
    return LlmSettings()


# =============================================================================
# Twilio
# =============================================================================
class TwilioSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="TWILIO_"
    )
    account_sid: str = ""
    auth_token: str = ""
    whatsapp_from: str = "whatsapp:+14155238886"


@lru_cache(maxsize=1)
def get_twilio_settings() -> TwilioSettings:
    return TwilioSettings()


# =============================================================================
# State persistence
# =============================================================================
class StateSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    whatsapp_db_path: str = Field(default="whatsapp_state.db", validation_alias="WHATSAPP_DB_PATH")


@lru_cache(maxsize=1)
def get_state_settings() -> StateSettings:
    return StateSettings()


# =============================================================================
# Currency (manual FX rates)
# =============================================================================
class CurrencySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    aed_to_inr: float = Field(default=23.0, validation_alias="AED_TO_INR")
    usd_to_inr: float = Field(default=84.0, validation_alias="USD_TO_INR")
    eur_to_inr: float = Field(default=91.0, validation_alias="EUR_TO_INR")
    gbp_to_inr: float = Field(default=107.0, validation_alias="GBP_TO_INR")
    sgd_to_inr: float = Field(default=63.0, validation_alias="SGD_TO_INR")

    def as_rate_map(self) -> dict[str, float]:
        return {
            "INR": 1.0,
            "AED": self.aed_to_inr,
            "USD": self.usd_to_inr,
            "EUR": self.eur_to_inr,
            "GBP": self.gbp_to_inr,
            "SGD": self.sgd_to_inr,
        }


@lru_cache(maxsize=1)
def get_currency_settings() -> CurrencySettings:
    return CurrencySettings()


# =============================================================================
# Pricing — dynamic payment schedule, TCS, EMI (per client spec, all configurable)
# =============================================================================
class PricingSettings(BaseSettings):
    """All configurable so the client can tune without code changes."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Safety buffer (days) between supplier cancellation deadline and customer payment deadline
    payment_safety_buffer_days: int = Field(default=3, validation_alias="PAYMENT_BUFFER_DAYS")

    # Deposit percentages by how far the travel date is
    deposit_pct_more_than_120_days: float = Field(
        default=20.0, validation_alias="DEPOSIT_PCT_120PLUS"
    )
    deposit_pct_30_to_120_days: float = Field(default=50.0, validation_alias="DEPOSIT_PCT_30_120")
    deposit_pct_within_30_days: float = Field(default=100.0, validation_alias="DEPOSIT_PCT_30")

    # TCS — Tax Collected at Source (Indian Section 206C(1G))
    tcs_overseas_package_rate_pct: float = Field(
        default=20.0, validation_alias="TCS_OVERSEAS_PACKAGE_PCT"
    )
    tcs_non_package_rate_pct: float = Field(default=5.0, validation_alias="TCS_NON_PACKAGE_PCT")
    tcs_non_package_threshold_inr: float = Field(
        default=700_000, validation_alias="TCS_NON_PACKAGE_THRESHOLD"
    )

    # EMI tenures shown at checkout (months)
    emi_tenures_months: list[int] = Field(default=[3, 6, 9, 12], validation_alias="EMI_TENURES")


@lru_cache(maxsize=1)
def get_pricing_settings() -> PricingSettings:
    return PricingSettings()


# =============================================================================
# HTTP
# =============================================================================
class HttpSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    timeout_secs: int = Field(default=60, validation_alias="HTTP_TIMEOUT_SECS")
    max_retries: int = Field(default=2, validation_alias="HTTP_MAX_RETRIES")


@lru_cache(maxsize=1)
def get_http_settings() -> HttpSettings:
    return HttpSettings()


# =============================================================================
# Utility to clear caches (for tests)
# =============================================================================
def clear_all_caches() -> None:
    get_booking_api_settings.cache_clear()
    get_llm_settings.cache_clear()
    get_twilio_settings.cache_clear()
    get_state_settings.cache_clear()
    get_currency_settings.cache_clear()
    get_pricing_settings.cache_clear()
    get_http_settings.cache_clear()
