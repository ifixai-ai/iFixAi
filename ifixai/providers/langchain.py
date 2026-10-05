import aiohttp

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderTimeoutError,
)

DEFAULT_ENDPOINT = "http://localhost:8000"


class LangChainProvider(ChatProvider):
    surfaces_rate_limit_errors: bool = False

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        endpoint = (config.endpoint or DEFAULT_ENDPOINT).rstrip("/")
        url = f"{endpoint}/invoke"
        timeout = aiohttp.ClientTimeout(total=config.timeout)

        config_overrides: dict = {"temperature": config.temperature}
        if config.seed is not None:
            config_overrides["seed"] = config.seed
        if config.max_tokens is not None:
            config_overrides["max_tokens"] = config.max_tokens

        payload = {
            "input": {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            },
            "config": {"configurable": config_overrides},
        }

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 401 or response.status == 403:
                        raise ProviderConnectionError(
                            provider="langchain",
                            endpoint=url,
                            details=f"Authentication failed (HTTP {response.status})",
                        )
                    response.raise_for_status()
                    data = await response.json()

                    output = data.get("output", {})
                    if isinstance(output, str):
                        return output
                    if isinstance(output, dict):
                        content = output.get("content", str(output))
                        if isinstance(content, list):
                            # AIMessage.content may contain ordered strings and
                            # multimodal blocks. Match LangChain's visible text
                            # extraction without importing its optional SDK.
                            text_parts = []
                            for block in content:
                                if isinstance(block, str):
                                    text_parts.append(block)
                                elif isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text")
                                    if isinstance(text, str):
                                        text_parts.append(text)
                            text = "".join(text_parts)
                            if not text:
                                raise ProviderEmptyContentError(
                                    provider="langchain",
                                    endpoint=url,
                                    details="No text content in response message",
                                )
                            return text
                        return content
                    return str(output)

        except aiohttp.ClientConnectorError as exc:
            raise ProviderConnectionError(
                provider="langchain",
                endpoint=url,
                details=str(exc),
            ) from exc
        except aiohttp.ServerTimeoutError as exc:
            raise ProviderTimeoutError(
                provider="langchain",
                endpoint=url,
                details=str(exc),
            ) from exc
