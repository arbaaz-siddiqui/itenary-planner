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

    Two bearer tokens are used (per the N8N-Technoheven V1 Postman collection):
      - `token` (main): all booking/search endpoints — visa, restaurant,
        package, flight, tour, transfer, hotel availability. The collection's
        main token (agentId 21 / GT-021) carries every serviceType.
      - `hotel_static_token`: the hotel *static-content* endpoints
        (GetCitiesWithHotel, GetStaticDataByCity, GetHotelStaticDataOptimize,
        gethotelstaticdatalistsuboptimize_v1_Address, GetPropertyDescriptions,
        GetHotelGuestReview). The collection signs these with a separate
        Hotels-only account (agentId 2 / GT-002) that holds the booking
        permissions those endpoints require.

    These come from the client's Postman collection. If Technoheaven rotates
    them, override via env without touching code.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    base_url: str = Field(
        default="https://stagingapi.gujjutours.com", validation_alias="BOOKING_BASE_URL"
    )
    # Some tour endpoints (options, price calendar, option details) live on the
    # B2C host, not the main B2B api host. Separate base URL, same Bearer token.
    b2c_base_url: str = Field(
        # The Postman doc lists the tour-option APIs on stagingb2c, but that
        # host 404s on every /api path; www.gujjutours.com serves them (verified
        # 2026-08-31). Override with BOOKING_B2C_BASE_URL if staging is fixed.
        default="https://www.gujjutours.com", validation_alias="BOOKING_B2C_BASE_URL"
    )
    token: str = Field(default="", validation_alias="BOOKING_TOKEN")
    # Flight search/details `Target` field: "test" (staging fares) or "production"
    # (live fares). The supplier returns different inventory per target.
    flight_target: str = Field(default="test", validation_alias="BOOKING_FLIGHT_TARGET")
    # Hotel static-content endpoints use a separate Hotels-only account token.
    # Falls back to the main token when unset so a single-token setup still works.
    hotel_static_token: str = Field(default="", validation_alias="BOOKING_HOTEL_STATIC_TOKEN")
    # Transfer inventory (TransferList/TransferDetail) is bound to the GT-018
    # account — the main GT-021 token returns statusCode 404/empty for transfers.
    # Falls back to the main token when unset.
    transfer_token: str = Field(default="", validation_alias="BOOKING_TRANSFER_TOKEN")

    def hotel_static_bearer(self) -> str:
        """Token for hotel static-content endpoints (falls back to main token)."""
        return self.hotel_static_token or self.token

    def transfer_bearer(self) -> str:
        """Token for transfer endpoints (falls back to main token)."""
        return self.transfer_token or self.token

    @staticmethod
    def _token_service_types(token: str) -> list[str]:
        """Decode a JWT's `serviceType` claim WITHOUT verifying the signature.

        Used only for a config sanity check (which services the token grants),
        never for trust decisions. Returns [] if the token can't be decoded.
        """
        import base64
        import json

        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            services = claims.get("serviceType") or []
            return [str(s) for s in services] if isinstance(services, list) else []
        except Exception:
            return []

    def main_token_missing_services(self) -> list[str]:
        """Historically warned when the token's `serviceType` JWT claim didn't list
        every service. DISABLED: the current GT-018 (Manoj) token lists only
        ["Activities"] in that claim yet works for flights, hotels, transfers,
        restaurants, and visa alike — confirmed by live calls and the client. The
        supplier does NOT enforce the claim, so the check was a false alarm that
        also recommended the wrong token (GT-021, which returns empty for
        transfers). Always returns [] now; kept for API compatibility.
        """
        return []
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
    # The /api/Currency/ROE/{code} endpoint authenticates with an antiforgery
    # `RequestVerificationToken` header (not the Bearer token) in the Postman
    # collection. That token expires, so it's configurable via env. When unset,
    # the ROE call falls back to the Bearer token, and if the call fails entirely
    # the caller falls back to the manual FX rate in CurrencySettings.
    roe_verification_token: str = Field(default="", validation_alias="BOOKING_ROE_VERIFICATION_TOKEN")


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
    # Comma-separated OpenRouter upstream host names to prefer, in order (e.g.
    # "SiliconFlow,AtlasCloud"). Empty = OpenRouter default routing (what the
    # earlier fast branches used). Set to pin away from slow/flaky hosts.
    openrouter_providers: str = Field(
        default="", validation_alias="OPENROUTER_PROVIDERS"
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
    # Where generated itinerary PDFs are written. On a mounted volume in prod,
    # point this at the volume (e.g. /var/data/itineraries) so they survive
    # restarts and the WhatsApp service can serve them back by id.
    itinerary_dir: str = Field(default="itineraries", validation_alias="ITINERARY_DIR")
    # Public base URL of the WhatsApp/FastAPI service (no trailing slash), used
    # to build the Twilio media URL for PDF delivery. e.g.
    # https://itinerary-planner-production.up.railway.app
    public_base_url: str = Field(default="", validation_alias="PUBLIC_BASE_URL")


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
    # Supplier prices arrive mostly in AED; this is the currency whose live ROE
    # the agent quotes to the (INR) customer. The manual aed_to_inr above is the
    # fallback when the live /api/Currency/ROE call is unavailable.
    roe_base_currency: str = Field(default="AED", validation_alias="ROE_BASE_CURRENCY")

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
