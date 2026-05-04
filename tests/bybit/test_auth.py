"""
Tests for bit.bybit.auth.make_auth_headers.

Verifies:
- Correct HMAC-SHA256 signing (deterministic with fixed inputs)
- All five required headers are present
- Header values are strings
- Different inputs produce different signatures
- Empty payload is handled correctly
- recv_window defaults to "5000"
"""

import hashlib
import hmac

import pytest

from bit.bybit.auth import make_auth_headers


def _expected_sig(api_secret: str, timestamp: str, api_key: str, recv_window: str, payload: str) -> str:
    pre_sign = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(
        api_secret.encode("utf-8"),
        pre_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class TestMakeAuthHeaders:
    def test_returns_dict(self):
        headers = make_auth_headers("key", "secret", "1000000")
        assert isinstance(headers, dict)

    def test_all_five_headers_present(self):
        headers = make_auth_headers("key", "secret", "1000000")
        assert "X-BAPI-API-KEY" in headers
        assert "X-BAPI-TIMESTAMP" in headers
        assert "X-BAPI-SIGN" in headers
        assert "X-BAPI-SIGN-TYPE" in headers
        assert "X-BAPI-RECV-WINDOW" in headers

    def test_all_values_are_strings(self):
        headers = make_auth_headers("key", "secret", "1000000")
        for k, v in headers.items():
            assert isinstance(v, str), f"Header {k} value is not a string: {v!r}"

    def test_api_key_header_matches_input(self):
        headers = make_auth_headers("my_api_key", "secret", "1000000")
        assert headers["X-BAPI-API-KEY"] == "my_api_key"

    def test_timestamp_header_matches_input(self):
        headers = make_auth_headers("key", "secret", "1234567890123")
        assert headers["X-BAPI-TIMESTAMP"] == "1234567890123"

    def test_sign_type_always_two(self):
        headers = make_auth_headers("key", "secret", "1000000")
        assert headers["X-BAPI-SIGN-TYPE"] == "2"

    def test_default_recv_window_is_5000(self):
        headers = make_auth_headers("key", "secret", "1000000")
        assert headers["X-BAPI-RECV-WINDOW"] == "5000"

    def test_custom_recv_window(self):
        headers = make_auth_headers("key", "secret", "1000000", recv_window="10000")
        assert headers["X-BAPI-RECV-WINDOW"] == "10000"

    def test_signature_correct_no_payload(self):
        api_key = "testkey"
        api_secret = "testsecret"
        timestamp = "1700000000000"
        recv_window = "5000"
        headers = make_auth_headers(api_key, api_secret, timestamp, payload="", recv_window=recv_window)
        expected = _expected_sig(api_secret, timestamp, api_key, recv_window, "")
        assert headers["X-BAPI-SIGN"] == expected

    def test_signature_correct_with_payload(self):
        api_key = "testkey"
        api_secret = "testsecret"
        timestamp = "1700000000000"
        payload = "symbol=BTCUSDT&limit=10"
        recv_window = "5000"
        headers = make_auth_headers(api_key, api_secret, timestamp, payload=payload, recv_window=recv_window)
        expected = _expected_sig(api_secret, timestamp, api_key, recv_window, payload)
        assert headers["X-BAPI-SIGN"] == expected

    def test_different_keys_produce_different_signatures(self):
        h1 = make_auth_headers("key1", "secret", "1000000")
        h2 = make_auth_headers("key2", "secret", "1000000")
        assert h1["X-BAPI-SIGN"] != h2["X-BAPI-SIGN"]

    def test_different_secrets_produce_different_signatures(self):
        h1 = make_auth_headers("key", "secret1", "1000000")
        h2 = make_auth_headers("key", "secret2", "1000000")
        assert h1["X-BAPI-SIGN"] != h2["X-BAPI-SIGN"]

    def test_different_timestamps_produce_different_signatures(self):
        h1 = make_auth_headers("key", "secret", "1000000")
        h2 = make_auth_headers("key", "secret", "2000000")
        assert h1["X-BAPI-SIGN"] != h2["X-BAPI-SIGN"]

    def test_different_payloads_produce_different_signatures(self):
        h1 = make_auth_headers("key", "secret", "1000000", payload="a=1")
        h2 = make_auth_headers("key", "secret", "1000000", payload="a=2")
        assert h1["X-BAPI-SIGN"] != h2["X-BAPI-SIGN"]

    def test_deterministic_same_inputs(self):
        h1 = make_auth_headers("key", "secret", "1000000", payload="x=1", recv_window="5000")
        h2 = make_auth_headers("key", "secret", "1000000", payload="x=1", recv_window="5000")
        assert h1["X-BAPI-SIGN"] == h2["X-BAPI-SIGN"]

    def test_signature_is_64_char_hex(self):
        headers = make_auth_headers("key", "secret", "1000000")
        sig = headers["X-BAPI-SIGN"]
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)
