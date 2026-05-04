"""
Startup credential validator for Bybit API keys.

Performs a single lightweight authenticated request to verify that the
configured API key and secret are valid before the run loop starts.

Endpoint used: GET /v5/user/query-api
This is the minimal authenticated endpoint — it validates the key and returns
its permission set without placing any orders or touching account state.

Behaviour:
  - No credentials configured → status="skipped" (paper trading uses public endpoints)
  - Credentials present and valid  → status="ok"
  - Credentials present but invalid → status="failed" (startup is halted by runner)
  - Network unreachable → status="failed" with network error detail

The result is written to RunnerState.credential_check so the dashboard can
display it on every snapshot without repeating the live check.
"""

from dataclasses import dataclass
from typing import Literal

from ..bybit.client import BybitAPIError, BybitNetworkError, BybitRestClient
from ..config import BITConfig

_VALIDATE_ENDPOINT = "/v5/user/query-api"


@dataclass
class CredentialCheckResult:
    """
    Result of a startup credential check.

    status:
      "ok"       — credentials are valid; key recognised by Bybit.
      "failed"   — credentials are invalid or the check could not be performed.
      "skipped"  — no credentials configured; check was not attempted.
    """

    status: Literal["ok", "failed", "skipped"]
    detail: str


async def check_credentials(
    config: BITConfig,
    client: BybitRestClient,
) -> CredentialCheckResult:
    """
    Validate Bybit API credentials with a single authenticated GET request.

    Args:
        config: BITConfig instance. Reads bybit_api_key and bybit_api_secret.
        client: BybitRestClient instance. Uses the same base URL (testnet/mainnet)
                as the rest of the application.

    Returns:
        CredentialCheckResult. Never raises.
    """
    if not config.bybit_api_key or not config.bybit_api_secret:
        return CredentialCheckResult(
            status="skipped",
            detail=(
                "No API credentials configured. "
                "Paper trading uses public endpoints — no authentication required."
            ),
        )

    try:
        result = await client.get_signed(
            _VALIDATE_ENDPOINT,
            params={},
            api_key=config.bybit_api_key,
            api_secret=config.bybit_api_secret,
        )
        read_only = bool(result.get("readOnly", 1))
        return CredentialCheckResult(
            status="ok",
            detail=f"Credentials validated against Bybit. Read-only: {read_only}.",
        )
    except BybitAPIError as exc:
        return CredentialCheckResult(
            status="failed",
            detail=f"Bybit API error [{exc.ret_code}]: {exc.message}",
        )
    except BybitNetworkError as exc:
        return CredentialCheckResult(
            status="failed",
            detail=f"Network error during credential check: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return CredentialCheckResult(
            status="failed",
            detail=f"Unexpected error during credential check: {exc}",
        )
