import httpx
import pytest
from app.agents.scout_agent import call_llm, ProviderError
from app.core.config import get_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload,expected", [
    (402, {"error": {"message": "insufficient credits"}}, "insufficient credits"),
    (200, {"choices": [{"finish_reason": "length", "message": {"content": None}}]}, "token budget"),
])
async def test_provider_failure_is_explicit(monkeypatch, status, payload, expected):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    original = httpx.AsyncClient
    def remote(request):
        import json
        body = json.loads(request.content)
        assert body["max_tokens"] == get_settings().llm_max_output_tokens
        assert body["provider"]["sort"] == "price"
        return httpx.Response(status, json=payload)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(remote), **kwargs))
    with pytest.raises(ProviderError, match=expected):
        await call_llm("Draft", "Test", strict=True)
