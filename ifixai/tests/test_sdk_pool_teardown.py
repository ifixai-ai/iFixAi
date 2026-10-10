import importlib

import pytest

ADAPTERS = [
    ("openai", "OpenAIProvider", "_clients"),
    ("azure", "AzureOpenAIProvider", "_clients"),
    ("anthropic", "AnthropicProvider", "_clients"),
    ("atlascloud", "AtlasCloudProvider", "_clients"),
    ("openrouter", "OpenRouterProvider", "_clients"),
    ("orcarouter", "OrcaRouterProvider", "_clients"),
    ("requesty", "RequestyProvider", "_clients"),
    ("cloudflare", "CloudflareAIGatewayProvider", "clients"),
    ("vercel", "VercelAIGatewayProvider", "clients"),
]


class OwnedClient:
    def __init__(self, error=None, callback=None):
        self.error = error
        self.callback = callback
        self.closes = 0

    async def close(self):
        self.closes += 1
        if self.callback:
            self.callback()
        if self.error:
            raise self.error


@pytest.mark.parametrize("module,name,attribute", ADAPTERS)
@pytest.mark.asyncio
async def test_pool_closes_every_client_after_an_ordinary_close_failure(module, name, attribute):
    provider = getattr(importlib.import_module(f"ifixai.providers.{module}"), name)()
    error = RuntimeError("owned close failure")
    first, second = OwnedClient(error=error), OwnedClient()
    pool = getattr(provider, attribute)
    pool.update({("first",): first, ("second",): second})
    with pytest.raises(RuntimeError, match="owned close failure") as raised:
        await provider.aclose()
    assert raised.value is error
    assert first.closes == second.closes == 1
    assert pool == {}
    await provider.aclose()
    assert first.closes == second.closes == 1


@pytest.mark.parametrize("module,name,attribute", ADAPTERS)
@pytest.mark.asyncio
async def test_pool_does_not_erase_a_new_client_created_during_close(module, name, attribute):
    provider = getattr(importlib.import_module(f"ifixai.providers.{module}"), name)()
    pool = getattr(provider, attribute)
    replacement = OwnedClient()
    first = OwnedClient(callback=lambda: pool.update({("new",): replacement}))
    pool[("first",)] = first
    await provider.aclose()
    assert pool == {("new",): replacement}
    assert replacement.closes == 0
    await provider.aclose()
    assert replacement.closes == 1
    assert pool == {}
