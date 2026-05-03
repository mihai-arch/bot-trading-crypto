"""
BybitRestClient

Thin async HTTP wrapper for Bybit v5 public REST endpoints.
Responsibilities:
- Base URL selection (mainnet vs. testnet)
- Request timeouts
- HTTP error detection (4xx / 5xx)
- Bybit envelope validation (retCode)
- JSON decode failure detection
- Extraction of the 'result' field so callers never see envelope boilerplate

No domain knowledge lives here. No parsing of API fields into domain models.
"""

import httpx


class BybitAPIError(Exception):
    """Raised when Bybit returns a non-zero retCode."""

    def __init__(self, ret_code: int, message: str, endpoint: str = "") -> None:
        self.ret_code = ret_code
        self.message = message
        self.endpoint = endpoint
        super().__init__(f"Bybit API error [{ret_code}] at '{endpoint}': {message}")


class BybitNetworkError(Exception):
    """Raised on HTTP errors, timeouts, connection failures, or invalid JSON."""


class BybitRestClient:
    MAINNET = "https://api.bybit.com"
    TESTNET = "https://api-testnet.bybit.com"

    def __init__(
        self,
        testnet: bool = True,
        timeout: float = 10.0,
        *,
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Args:
            testnet: Use Bybit testnet base URL. Defaults to True (safe default).
            timeout: Request timeout in seconds.
            _transport: Inject a custom transport. Used in tests only — not for production.
        """
        base_url = self.TESTNET if testnet else self.MAINNET
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=_transport,
            headers={"Accept": "application/json"},
        )

    async def get(self, path: str, params: dict[str, str | int]) -> dict:
        """
        Perform a GET request and return the 'result' field from the Bybit envelope.

        Raises:
            BybitNetworkError: On HTTP error status, timeout, or invalid JSON.
            BybitAPIError: When retCode != 0.
        """
        try:
            response = await self._http.get(path, params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise BybitNetworkError(f"Request timed out: {path}") from exc
        except httpx.HTTPStatusError as exc:
            raise BybitNetworkError(
                f"HTTP {exc.response.status_code} from {path}"
            ) from exc
        except httpx.RequestError as exc:
            raise BybitNetworkError(f"Network error on {path}: {exc}") from exc

        try:
            data = response.json()
        except Exception as exc:
            raise BybitNetworkError(f"Invalid JSON response from {path}") from exc

        ret_code = data.get("retCode", -1)
        if ret_code != 0:
            raise BybitAPIError(
                ret_code=ret_code,
                message=data.get("retMsg", "unknown"),
                endpoint=path,
            )

        return data["result"]

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Always call this when done."""
        await self._http.aclose()

    async def __aenter__(self) -> "BybitRestClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
