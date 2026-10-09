"""Actual incomplete HTTP bodies follow the existing native retry contract."""

import asyncio
import json

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderConnectionError, ProviderResponseError
from ifixai.providers.http import HttpProvider


@pytest.mark.parametrize(
    "bodies, retries, expected, requests",
    [
        (["incomplete", "complete"], 1, "complete", 2),
        (["incomplete"], 0, "connection_error", 1),
        (["incomplete", "incomplete"], 1, "connection_error", 2),
        (["invalid_json", "complete"], 1, "response_error", 1),
        (["complete"], 1, "complete", 1),
    ],
)
def test_completion_body_transport_failure_uses_existing_retry_policy(
    bodies, retries, expected, requests
):
    async def run():
        received = []
        handlers = set()

        async def respond(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.partition(b":")[2].strip())
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                received.append(json.loads(await reader.readexactly(length)))
                kind = bodies[min(len(received) - 1, len(bodies) - 1)]
                body = json.dumps({"choices": [{"message": {"content": "Complete response"}}]}).encode()
                if kind == "invalid_json":
                    body = b"not JSON"
                transmitted = body[:len(body) // 2] if kind == "incomplete" else body
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + transmitted
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}/v1"
        provider = HttpProvider()
        try:
            config = ProviderConfig(provider="http", model="owned", endpoint=endpoint, max_retries=retries, timeout=5)
            messages = [ChatMessage(role="user", content="hello")]
            if expected == "complete":
                assert await provider.send_message(messages, config) == "Complete response"
            else:
                error = ProviderConnectionError if expected == "connection_error" else ProviderResponseError
                with pytest.raises(error) as caught:
                    await provider.send_message(messages, config)
                assert caught.value.provider == "http"
                assert caught.value.endpoint == endpoint
            assert len(received) == requests
            assert all(request["messages"] == [{"role": "user", "content": "hello"}] for request in received)
        finally:
            await provider.aclose()
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.gather(*handlers)

    asyncio.run(run())
