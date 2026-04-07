"""Tests for the API Gateway."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.gateway.models import AuthType, GatewayRequest, GatewayResponse, ServiceConfig
from atlas.gateway.gateway import APIGateway


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_gateway(**kwargs):
    """Create a gateway with mocked permission manager."""
    pm = AsyncMock()
    pm.check = AsyncMock(return_value=True)
    return APIGateway(permission_manager=pm, **kwargs)


TEST_SERVICE = ServiceConfig(
    name="test_api",
    base_url="https://api.example.com",
    auth_type=AuthType.HEADER,
    auth_key_name="test_api_key",
    auth_header="X-API-Key",
    permission="internet_access",
)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestModels:
    def test_auth_type_values(self):
        assert AuthType.HEADER.value == "header"
        assert AuthType.QUERY_PARAM.value == "query_param"
        assert AuthType.BEARER.value == "bearer"
        assert AuthType.OAUTH2.value == "oauth2"
        assert AuthType.CONSTRUCTOR.value == "constructor"
    
    def test_gateway_request_defaults(self):
        req = GatewayRequest(service="test")
        assert req.method == "GET"
        assert req.endpoint == ""
        assert req.params is None
        assert req.json_body is None
        assert req.timeout == 10.0
    
    def test_gateway_response_ok(self):
        resp = GatewayResponse(
            status_code=200,
            body={"result": "ok"},
            headers={},
            service="test",
            request_id="abc123",
        )
        assert resp.ok is True
        assert resp.error is None
    
    def test_service_config_fields(self):
        svc = ServiceConfig(
            name="tavily",
            base_url="https://api.tavily.com",
            auth_type=AuthType.CONSTRUCTOR,
            auth_key_name="tavily_api_key",
            permission="internet_access",
        )
        assert svc.auth_header is None
        assert svc.rate_limit is None
        assert svc.cache_ttl is None


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_build_registry_returns_dict(self):
        from atlas.gateway.registry import build_registry
        reg = build_registry()
        assert isinstance(reg, dict)
        assert "tavily" in reg
        assert "openweathermap" in reg
    
    def test_all_services_have_required_fields(self):
        from atlas.gateway.registry import build_registry
        for name, svc in build_registry().items():
            assert svc.name == name
            assert svc.base_url
            assert isinstance(svc.auth_type, AuthType)
            assert svc.auth_key_name


# ---------------------------------------------------------------------------
# Gateway core tests
# ---------------------------------------------------------------------------

class TestGatewayRequest:
    
    def test_unknown_service_returns_error(self):
        gw = _make_gateway()
        req = GatewayRequest(service="nonexistent", method="GET")
        resp = asyncio.run(gw.request(req))
        assert not resp.ok
        assert "Unknown service" in resp.error
    
    def test_permission_denied_returns_403(self):
        gw = _make_gateway()
        gw.register_service(TEST_SERVICE)
        gw._pm.check = AsyncMock(return_value=False)
        
        req = GatewayRequest(service="test_api", method="GET", endpoint="/data")
        resp = asyncio.run(gw.request(req))
        assert resp.status_code == 403
        assert not resp.ok
        assert "denied" in resp.error
    
    def test_permission_check_called_with_correct_args(self):
        gw = _make_gateway()
        gw.register_service(TEST_SERVICE)
        
        # Mock _resolve_secret and _execute to avoid real HTTP
        gw._resolve_secret = MagicMock(return_value="fake_key")
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        mock_response.json.return_value = {"result": "ok"}
        mock_response.headers = {}
        gw._execute = AsyncMock(return_value=mock_response)
        
        req = GatewayRequest(service="test_api", method="GET", endpoint="/data")
        asyncio.run(gw.request(req))
        
        gw._pm.check.assert_called_once_with(
            "internet_access", "https://api.example.com"
        )
    
    def test_successful_request_returns_body(self):
        gw = _make_gateway()
        gw.register_service(TEST_SERVICE)
        gw._resolve_secret = MagicMock(return_value="fake_key")
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        mock_response.json.return_value = {"data": "hello"}
        mock_response.headers = {}
        gw._execute = AsyncMock(return_value=mock_response)
        
        req = GatewayRequest(service="test_api", method="GET", endpoint="/data")
        resp = asyncio.run(gw.request(req))
        
        assert resp.ok
        assert resp.status_code == 200
        assert resp.body == {"data": "hello"}
        assert resp.request_id  # non-empty
    
    def test_register_service_overrides(self):
        gw = _make_gateway()
        svc1 = ServiceConfig(
            name="test", base_url="https://a.com",
            auth_type=AuthType.HEADER, auth_key_name="k1", permission="p1",
        )
        svc2 = ServiceConfig(
            name="test", base_url="https://b.com",
            auth_type=AuthType.BEARER, auth_key_name="k2", permission="p2",
        )
        gw.register_service(svc1)
        gw.register_service(svc2)
        assert gw._registry["test"].base_url == "https://b.com"


# ---------------------------------------------------------------------------
# Sanitization tests
# ---------------------------------------------------------------------------

class TestSanitization:
    
    def test_dict_sanitizes_api_key(self):
        gw = _make_gateway()
        result = gw._sanitize_dict({"api_key": "sk-abc123xyz", "data": "safe"})
        assert result["api_key"] == "***REDACTED***"
        assert result["data"] == "safe"
    
    def test_nested_dict_sanitizes(self):
        gw = _make_gateway()
        result = gw._sanitize_dict({
            "outer": {"token": "secret123", "value": 42}
        })
        assert result["outer"]["token"] == "***REDACTED***"
        assert result["outer"]["value"] == 42
    
    def test_list_of_dicts_sanitized(self):
        gw = _make_gateway()
        result = gw._sanitize_dict({
            "items": [
                {"password": "abc", "name": "ok"},
                {"secret": "xyz", "count": 1},
            ]
        })
        assert result["items"][0]["password"] == "***REDACTED***"
        assert result["items"][0]["name"] == "ok"
        assert result["items"][1]["secret"] == "***REDACTED***"
    
    def test_non_sensitive_keys_preserved(self):
        gw = _make_gateway()
        result = gw._sanitize_dict({"query": "test", "results": [1, 2, 3]})
        assert result == {"query": "test", "results": [1, 2, 3]}


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------

class TestCache:
    
    def test_cache_stores_and_retrieves(self):
        gw = _make_gateway()
        req = GatewayRequest(service="test", method="GET", endpoint="/weather")
        resp = GatewayResponse(
            status_code=200, body={"temp": 25}, headers={},
            service="test", request_id="r1",
        )
        
        gw._store_cache(req, resp)
        cached = gw._check_cache(req, ttl=60)
        assert cached is not None
        assert cached.body == {"temp": 25}
    
    def test_cache_miss_returns_none(self):
        gw = _make_gateway()
        req = GatewayRequest(service="test", method="GET", endpoint="/new")
        assert gw._check_cache(req, ttl=60) is None
    
    def test_cache_key_deterministic(self):
        gw = _make_gateway()
        req1 = GatewayRequest(service="s", method="GET", endpoint="/p", params={"a": 1})
        req2 = GatewayRequest(service="s", method="GET", endpoint="/p", params={"a": 1})
        assert gw._cache_key(req1) == gw._cache_key(req2)
    
    def test_different_params_different_keys(self):
        gw = _make_gateway()
        req1 = GatewayRequest(service="s", method="GET", endpoint="/p", params={"a": 1})
        req2 = GatewayRequest(service="s", method="GET", endpoint="/p", params={"a": 2})
        assert gw._cache_key(req1) != gw._cache_key(req2)
