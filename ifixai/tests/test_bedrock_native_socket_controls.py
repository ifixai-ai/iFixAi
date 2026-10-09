import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.mark.parametrize("timeout,retries,mode,expected,calls", [
    pytest.param(0, 0, "success", "ProviderTimeoutError", 0, id="zero-timeout"),
    pytest.param(-1, 0, "success", "ProviderTimeoutError", 0, id="negative-timeout"),
    (1, 0, "slow", "ProviderTimeoutError", 1),
    (4, 0, "slow", "success", 1),
    (4, 0, "success", "success", 1),
    (4, 1, "throttle_once", "success", 2),
    (4, 0, "throttle", "ProviderRateLimitError", 1),
    (4, 0, "unavailable", "ProviderConnectionError", 1),
    (4, 1, "not_ready_once", "success", 2),
    (4, 2, "not_ready", "ProviderConnectionError", 3),
    (4, 0, "not_ready", "ProviderConnectionError", 1),
    (4, 0, "denied", "ProviderAuthError", 1),
    *[(4, 1, f"http_{status}_once", "success", 2) for status in (500, 502, 503, 504)],
    (4, 1, "reset_once", "success", 2),
    (4, 2, "unavailable", "ProviderConnectionError", 3),
    (4, 0, "reset", "ProviderConnectionError", 1),
    (4, 2, "denied", "ProviderAuthError", 1),
    (4, 2, "invalid_request", "ProviderResponseError", 1),
])
def test_bedrock_timeout_releases_cli_probe_loop(timeout, retries, mode, expected, calls):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["messages"][0]["content"] == [{"text": "owned probe"}]
            if mode == "slow":
                time.sleep(3)
            if mode == "reset" or (mode == "reset_once" and len(requests) == 1):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            error = None
            if mode == "throttle" or (mode == "throttle_once" and len(requests) == 1):
                error = (429, "ThrottlingException")
            elif mode == "unavailable":
                error = (503, "ServiceUnavailableException")
            elif mode == "not_ready" or (mode == "not_ready_once" and len(requests) == 1):
                error = (429, "ModelNotReadyException")
            elif mode.startswith("http_") and len(requests) == 1:
                # An unmodelled proxy/service error still carries its HTTP status.
                error = (int(mode.split("_")[1]), "OwnedTransientError")
            elif mode == "invalid_request":
                error = (400, "ValidationException")
            elif mode == "denied":
                error = (403, "AccessDeniedException")
            body = json.dumps({"output": {"message": {"role": "assistant", "content": [{"text": "owned response"}]}}, "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 3000}}).encode()
            if error:
                body = json.dumps({"__type": error[1], "message": "owned failure"}).encode()
            try:
                self.send_response(error[0] if error else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if error:
                    self.send_header("x-amzn-ErrorType", error[1])
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = "http://127.0.0.1:" + str(server.server_port)
    code = """import asyncio,json,time
from ifixai.providers.bedrock import BedrockProvider
from ifixai.core.types import ProviderConfig,ChatMessage
from ifixai.providers.base import ProviderError
from ifixai.cli.run import _probe_then_close
p=BedrockProvider()
c=ProviderConfig(provider='bedrock',endpoint=ENDPOINT,model='anthropic.claude-3-sonnet-20240229-v1:0',timeout=TIMEOUT,max_retries=RETRIES)
started=time.monotonic()
try:
    reply=asyncio.run(_probe_then_close(p,p.send_message([ChatMessage(role='user',content='owned probe')],c)))
except ProviderError as exc:
    print(json.dumps({'status':type(exc).__name__,'probe_elapsed':time.monotonic()-started}))
else:
    print(json.dumps({'status':'success','reply':reply,'probe_elapsed':time.monotonic()-started}))
""".replace("ENDPOINT", repr(endpoint)).replace("TIMEOUT", str(timeout)).replace("RETRIES", str(retries))
    env = dict(os.environ, AWS_ACCESS_KEY_ID="owned-native-key", AWS_SECRET_ACCESS_KEY="owned-native-secret", AWS_EC2_METADATA_DISABLED="true", AWS_DEFAULT_REGION="us-east-1", PYTHONDONTWRITEBYTECODE="1")
    try:
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, timeout=10)
        elapsed = time.monotonic() - started
        print(json.dumps({'elapsed':elapsed, 'stdout':result.stdout, 'stderr':result.stderr, 'requests':requests}))
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout.splitlines()[-1])
        assert payload['status'] == expected
        assert len(requests) == calls
        if expected == "ProviderTimeoutError":
            # Bound only this owned stalled read, excluding import startup.
            # Socket limits do not promise a global DNS/streaming deadline.
            assert payload['probe_elapsed'] < 2.5, 'CLI-owned loop still waits for the timed-out SDK socket'
        if mode == "unavailable" and retries == 2:
            assert payload['probe_elapsed'] >= 2.8, 'manual exponential backoff was bypassed'
        if expected == "success":
            assert payload['reply'] == 'owned response'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
