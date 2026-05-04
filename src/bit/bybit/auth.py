"""
Bybit v5 request signing.

Pure functions — no I/O, no state, fully testable.

Bybit v5 authentication uses HMAC-SHA256:
    pre_sign = timestamp + api_key + recv_window + payload
    signature = HMAC_SHA256(api_secret, pre_sign).hexdigest()

Where:
  payload = URL-encoded query string for GET requests
            JSON body string for POST requests

Headers required on signed requests:
    X-BAPI-API-KEY      — the API key
    X-BAPI-TIMESTAMP    — milliseconds since epoch (string)
    X-BAPI-SIGN         — HMAC-SHA256 hex digest
    X-BAPI-SIGN-TYPE    — always "2" for v5
    X-BAPI-RECV-WINDOW  — how long the request is valid in milliseconds

Reference:
    https://bybit-exchange.github.io/docs/v5/guide/authentication
"""

import hashlib
import hmac

_DEFAULT_RECV_WINDOW = "5000"


def make_auth_headers(
    api_key: str,
    api_secret: str,
    timestamp: str,
    payload: str = "",
    recv_window: str = _DEFAULT_RECV_WINDOW,
) -> dict[str, str]:
    """
    Build Bybit v5 HMAC authentication headers for a signed request.

    Args:
        api_key:      Bybit API key.
        api_secret:   Bybit API secret.
        timestamp:    Request timestamp in milliseconds (string).
                      Use str(int(time.time() * 1000)).
        payload:      URL-encoded query string for GET requests;
                      JSON body string for POST. Empty string if no payload.
        recv_window:  Validity window in milliseconds. Default "5000".

    Returns:
        Dict of headers to merge into the outgoing request.
    """
    pre_sign = f"{timestamp}{api_key}{recv_window}{payload}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        pre_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-RECV-WINDOW": recv_window,
    }
