"""
Tests for check_credentials() startup validator.

Verifies:
- Returns "skipped" when no API credentials are configured
- Returns "ok" when Bybit accepts the credentials
- Returns "failed" with detail when Bybit rejects credentials (BybitAPIError)
- Returns "failed" with detail on network error (BybitNetworkError)
- Returns "failed" with detail on unexpected exception
- Never raises
"""

import pytest

from bit.bybit.client import BybitAPIError, BybitNetworkError
from bit.config import BITConfig
from bit.services.credential_check import CredentialCheckResult, check_credentials


def _config(**kwargs) -> BITConfig:
    defaults = dict(bybit_api_key="", bybit_api_secret="", paper_trading=True)
    defaults.update(kwargs)
    return BITConfig(**defaults)


# ── Helpers: fake BybitRestClient ──────────────────────────────────────────

class _FakeClient:
    """Minimal stand-in for BybitRestClient with a controllable get_signed()."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc

    async def get_signed(self, path, params, api_key, api_secret, recv_window="5000"):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


# ── No credentials ─────────────────────────────────────────────────────────

class TestNoCredentials:
    @pytest.mark.asyncio
    async def test_skipped_when_no_key(self):
        result = await check_credentials(_config(bybit_api_key=""), _FakeClient())
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_skipped_when_no_secret(self):
        result = await check_credentials(
            _config(bybit_api_key="key123", bybit_api_secret=""),
            _FakeClient(),
        )
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_skipped_detail_mentions_paper(self):
        result = await check_credentials(_config(), _FakeClient())
        assert "paper" in result.detail.lower() or "public" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_skipped_does_not_call_client(self):
        class _TrackingClient(_FakeClient):
            called = False
            async def get_signed(self, *args, **kwargs):
                _TrackingClient.called = True
                return {}

        await check_credentials(_config(), _TrackingClient())
        assert not _TrackingClient.called


# ── Successful validation ──────────────────────────────────────────────────

class TestSuccessfulValidation:
    @pytest.mark.asyncio
    async def test_ok_status_on_success(self):
        result = await check_credentials(
            _config(bybit_api_key="real_key", bybit_api_secret="real_secret"),
            _FakeClient(response={"readOnly": 0}),
        )
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_ok_detail_mentions_validated(self):
        result = await check_credentials(
            _config(bybit_api_key="real_key", bybit_api_secret="real_secret"),
            _FakeClient(response={"readOnly": 0}),
        )
        assert "validated" in result.detail.lower() or "ok" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_result_is_credential_check_result(self):
        result = await check_credentials(
            _config(bybit_api_key="real_key", bybit_api_secret="real_secret"),
            _FakeClient(response={}),
        )
        assert isinstance(result, CredentialCheckResult)


# ── API error (e.g. invalid key) ──────────────────────────────────────────

class TestBybitAPIError:
    @pytest.mark.asyncio
    async def test_failed_on_bybit_api_error(self):
        exc = BybitAPIError(ret_code=10003, message="Invalid API key.")
        result = await check_credentials(
            _config(bybit_api_key="bad_key", bybit_api_secret="bad_secret"),
            _FakeClient(raise_exc=exc),
        )
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_failed_detail_includes_ret_code(self):
        exc = BybitAPIError(ret_code=10003, message="Invalid API key.")
        result = await check_credentials(
            _config(bybit_api_key="bad_key", bybit_api_secret="bad_secret"),
            _FakeClient(raise_exc=exc),
        )
        assert "10003" in result.detail

    @pytest.mark.asyncio
    async def test_failed_detail_includes_message(self):
        exc = BybitAPIError(ret_code=10003, message="Invalid API key.")
        result = await check_credentials(
            _config(bybit_api_key="bad_key", bybit_api_secret="bad_secret"),
            _FakeClient(raise_exc=exc),
        )
        assert "Invalid API key" in result.detail


# ── Network error ─────────────────────────────────────────────────────────

class TestNetworkError:
    @pytest.mark.asyncio
    async def test_failed_on_network_error(self):
        exc = BybitNetworkError("Connection refused")
        result = await check_credentials(
            _config(bybit_api_key="key", bybit_api_secret="secret"),
            _FakeClient(raise_exc=exc),
        )
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_failed_detail_mentions_network(self):
        exc = BybitNetworkError("Connection refused")
        result = await check_credentials(
            _config(bybit_api_key="key", bybit_api_secret="secret"),
            _FakeClient(raise_exc=exc),
        )
        assert "network" in result.detail.lower() or "connection" in result.detail.lower()


# ── Unexpected exception ──────────────────────────────────────────────────

class TestUnexpectedException:
    @pytest.mark.asyncio
    async def test_failed_on_unexpected_exception(self):
        result = await check_credentials(
            _config(bybit_api_key="key", bybit_api_secret="secret"),
            _FakeClient(raise_exc=RuntimeError("Something broke")),
        )
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_never_raises(self):
        """check_credentials must never propagate exceptions."""
        result = await check_credentials(
            _config(bybit_api_key="key", bybit_api_secret="secret"),
            _FakeClient(raise_exc=Exception("boom")),
        )
        # If we reach here, it didn't raise
        assert result.status == "failed"
