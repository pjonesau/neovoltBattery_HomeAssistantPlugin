"""Tests for low-level neovolt_client helpers and the encryption fail-loud contract.

The client imports Home Assistant at module load, so the module is skipped
when HA isn't installed.
"""
from __future__ import annotations

import pytest

# neovolt_auth needs pycryptodome at module load; neovolt_client also imports it.
pytest.importorskip("Crypto.Cipher")
pytest.importorskip("aiohttp")


# neovolt_client imports homeassistant.helpers.aiohttp_client at module load —
# skip cleanly when HA isn't installed (bare sandbox). Import through the
# package so the client's relative ``from .neovolt_auth import`` resolves.
try:
    from custom_components.bytewatt.api import neovolt_auth, neovolt_client
except ModuleNotFoundError as exc:
    pytest.skip(f"Module not installed in this environment: {exc.name}", allow_module_level=True)

EncryptionError = neovolt_auth.EncryptionError
encrypt_password = neovolt_auth.encrypt_password
ByteWattAPIError = neovolt_client.ByteWattAPIError
_stat_value = neovolt_client._stat_value
_decode_json_object = neovolt_client._decode_json_object


def test_stat_value_returns_value_when_present():
    assert _stat_value({"epvtoday": 12.5}, "epvtoday") == 12.5
    assert _stat_value({"epvtoday": 0}, "epvtoday") == 0


def test_stat_value_coalesces_missing_key_to_zero():
    assert _stat_value({}, "epvtoday") == 0


def test_stat_value_coalesces_explicit_none_to_zero():
    """The fragile arithmetic path used to crash on None — never again."""
    assert _stat_value({"epvtoday": None}, "epvtoday") == 0


def test_battery_discharged_today_calculation_survives_partial_data():
    """The discharge calc used to raise TypeError when any input was None.
    With _stat_value, the calc must complete with sensible zeros."""
    stats_data = {
        "epvtoday": 10,
        "ehomeload": None,   # Missing field
        # efeedIn missing entirely
        "einput": 5,
        "echarge": 2,
    }
    pv_today    = _stat_value(stats_data, "epvtoday")
    consumed    = _stat_value(stats_data, "ehomeload")
    feed_in     = _stat_value(stats_data, "efeedIn")
    grid_import = _stat_value(stats_data, "einput")
    charged     = _stat_value(stats_data, "echarge")
    # Should not raise.
    total_gained = pv_today + grid_import
    total_used   = consumed + feed_in + charged
    discharged = total_used - total_gained
    assert discharged == 2 - 15  # 0+0+2 - (10+5)


def test_encryption_known_vector():
    """Anchor a known cipher output so we'd catch any regression in the
    encryption algorithm (key derivation, IV, padding, base64)."""
    assert encrypt_password("1", "caraa") == "CH1iL1FqYK9bhTd9izZyMA=="
    assert encrypt_password("1", "carraa") == "oFzzKemj3O4WP92FBSjZzw=="


def test_encryption_error_is_runtime_error_subclass():
    """Callers can catch RuntimeError generically and still get EncryptionError."""
    assert issubclass(EncryptionError, RuntimeError)


def test_bytewatt_api_error_is_exception_subclass():
    """The coordinator catches Exception broadly — ByteWattAPIError must match."""
    assert issubclass(ByteWattAPIError, Exception)


# ---------------------------------------------------------------------------
# _decode_json_object — must reject non-object JSON before .get() crashes
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal stand-in for aiohttp.ClientResponse."""

    def __init__(self, json_value=None, raise_exc=None):
        self._json_value = json_value
        self._raise_exc = raise_exc

    async def json(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._json_value


@pytest.mark.asyncio
async def test_decode_returns_dict_for_object():
    result = await _decode_json_object(_FakeResponse({"code": 200}), "ctx")
    assert result == {"code": 200}


@pytest.mark.asyncio
async def test_decode_returns_none_for_array():
    """Body is valid JSON but a list — `.get('code')` would crash later."""
    result = await _decode_json_object(_FakeResponse(["error", "details"]), "ctx")
    assert result is None


@pytest.mark.asyncio
async def test_decode_returns_none_for_string():
    """Body is valid JSON but a bare string."""
    result = await _decode_json_object(_FakeResponse("just an error message"), "ctx")
    assert result is None


@pytest.mark.asyncio
async def test_decode_returns_none_for_null():
    result = await _decode_json_object(_FakeResponse(None), "ctx")
    assert result is None


@pytest.mark.asyncio
async def test_decode_returns_none_on_value_error():
    """ValueError covers json.JSONDecodeError for malformed bodies."""
    result = await _decode_json_object(_FakeResponse(raise_exc=ValueError("bad json")), "ctx")
    assert result is None


# ---------------------------------------------------------------------------
# _async_get_host_soc — SOC comes from the Host, not the sysSn=All average
# ---------------------------------------------------------------------------

class _FakeGetResponse(_FakeResponse):
    def __init__(self, json_value, status=200):
        super().__init__(json_value)
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


def _client_with(session, host_sys_sn="HOST123"):
    client = object.__new__(neovolt_client.NeovoltClient)
    client.base_url = "https://example.invalid"
    client.session = session
    client.host_sys_sn = host_sys_sn
    return client


@pytest.mark.asyncio
async def test_host_soc_queries_host_sys_sn():
    session = _FakeSession(_FakeGetResponse({"code": 200, "data": {"soc": 99.9}}))
    soc = await _client_with(session)._async_get_host_soc({})
    assert soc == 99.9
    assert session.calls[0][1]["sysSn"] == "HOST123"


@pytest.mark.asyncio
async def test_host_soc_none_on_http_error():
    session = _FakeSession(_FakeGetResponse({}, status=500))
    assert await _client_with(session)._async_get_host_soc({}) is None


@pytest.mark.asyncio
async def test_host_soc_none_on_api_error_code():
    session = _FakeSession(_FakeGetResponse({"code": 6069, "msg": "expired"}))
    assert await _client_with(session)._async_get_host_soc({}) is None


@pytest.mark.asyncio
async def test_host_soc_none_when_soc_missing_or_null():
    for data in ({}, {"soc": None}, None):
        session = _FakeSession(_FakeGetResponse({"code": 200, "data": data}))
        assert await _client_with(session)._async_get_host_soc({}) is None


@pytest.mark.asyncio
async def test_host_soc_none_on_transport_error():
    import aiohttp
    session = _FakeSession(raise_exc=aiohttp.ClientError("boom"))
    assert await _client_with(session)._async_get_host_soc({}) is None
