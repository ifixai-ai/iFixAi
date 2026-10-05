"""Judge cutoff handling for Gemini SDK candidates and native Bedrock HTTP."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderTruncatedError

PARTIAL = '{"passed": true'


@pytest.mark.asyncio
@pytest.mark.parametrize("reject,cutoff,text", [(True, True, PARTIAL), (False, True, PARTIAL), (True, False, PARTIAL), (True, True, "")])
async def test_gemini_sdk_finish_reason_controls_judge_cutoff(monkeypatch, reject, cutoff, text):
    genai = pytest.importorskip("google.generativeai")
    glm = pytest.importorskip("google.ai.generativelanguage")
    from google.generativeai import client

    from ifixai.providers.gemini import GeminiProvider

    class OwnedTransport:
        async def generate_content(self, request, **kwargs):
            assert request.contents[0].parts[0].text == "judge this"
            return glm.GenerateContentResponse(candidates=[glm.Candidate(
                content=glm.Content(parts=[glm.Part(text=text)]),
                finish_reason=glm.Candidate.FinishReason.MAX_TOKENS if cutoff else glm.Candidate.FinishReason.STOP,
            )])

    # Use the installed GenerativeModel and its native response conversion.
    # Only the outbound transport is synthetic; no Google service is called.
    monkeypatch.setattr(client, "get_default_generative_async_client", OwnedTransport)
    config = ProviderConfig(provider="gemini", api_key="synthetic-local-key", reject_truncated=reject, max_retries=0)
    call = GeminiProvider().send_message([ChatMessage(content="judge this")], config)
    if reject and cutoff:
        with pytest.raises(ProviderTruncatedError):
            await call
    else:
        assert await call == text
    assert genai is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("reject,cutoff,text", [(True, True, PARTIAL), (False, True, PARTIAL), (True, False, PARTIAL), (True, True, "")])
async def test_bedrock_native_http_stop_reason_controls_judge_cutoff(monkeypatch, tmp_path, reject, cutoff, text):
    pytest.importorskip("boto3")
    from ifixai.providers.bedrock import BedrockProvider

    # Explicit owned credentials avoid SDK discovery of any user credentials.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-local-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-local-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "absent-config"))
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            payload = json.dumps({
                "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
                "stopReason": "max_tokens" if cutoff else "end_turn",
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
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
    try:
        config = ProviderConfig(provider="bedrock", endpoint=f"http://127.0.0.1:{server.server_port}", model="owned-model", reject_truncated=reject, max_retries=0)
        call = BedrockProvider().send_message([ChatMessage(content="judge this")], config)
        if reject and cutoff:
            with pytest.raises(ProviderTruncatedError):
                await call
        else:
            assert await call == text
        assert len(seen) == 1
        assert seen[0][0] == "/model/owned-model/converse"
        assert seen[0][1]["messages"][0]["content"] == [{"text": "judge this"}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
