"""Route native Hugging Face inference to the configured owned endpoint."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers import huggingface

hf = pytest.importorskip("huggingface_hub")


@pytest.mark.asyncio
async def test_endpoint_receives_native_chat_request_with_model_and_key(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            payload = json.dumps({
                "id": "owned-reply", "object": "chat.completion", "created": 1,
                "model": "owned-model", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "owned endpoint reply"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"

    def native_client(**kwargs):
        # Fail before any network access if the adapter ignored the endpoint.
        # Once this passes, the installed SDK performs the actual HTTP call.
        assert kwargs["model"] == endpoint
        return hf.InferenceClient(**kwargs)

    monkeypatch.setattr(huggingface, "InferenceClient", native_client)
    try:
        config = ProviderConfig(provider="huggingface", endpoint=endpoint, model="owned-model", api_key="synthetic-local-key", max_retries=0)
        reply = await huggingface.HuggingFaceProvider().send_message([ChatMessage(content="hello")], config)
        assert reply == "owned endpoint reply"
        assert len(requests) == 1
        path, auth, body = requests[0]
        assert path == "/v1/chat/completions"
        assert auth == "Bearer synthetic-local-key"
        assert body["model"] == "owned-model"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_default_model_routing_is_retained(monkeypatch):
    class CapturedClient:
        def __init__(self, **kwargs):
            assert kwargs["model"] == "org/model"

    monkeypatch.setattr(huggingface, "InferenceClient", CapturedClient)
    monkeypatch.setattr(huggingface, "_call_chat_completion", lambda *args: "model reply")
    reply = await huggingface.HuggingFaceProvider().send_message([ChatMessage(content="hello")], ProviderConfig(provider="huggingface", model="org/model"))
    assert reply == "model reply"
