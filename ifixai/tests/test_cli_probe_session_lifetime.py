import asyncio
import http.server
import importlib
import json
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.providers.http import HttpProvider


def test_native_cli_closes_connection_probe_before_loop_exit(tmp_path, monkeypatch):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": "I cannot find that account in the available records."
                            },
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    probe = HttpProvider()
    module = importlib.import_module("ifixai.cli.run")
    monkeypatch.setattr(module, "resolve_provider", lambda provider: probe)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    fixture = Path(__file__).parents[1] / "fixtures/examples/customer_support.yaml"
    try:
        result = CliRunner().invoke(
            ifixai_cli,
            [
                "run",
                "--provider",
                "http",
                "--api-key",
                "synthetic",
                "--endpoint",
                f"http://127.0.0.1:{server.server_port}",
                "--auth-method",
                "none",
                "--fixture",
                str(fixture),
                "--test",
                "B01",
                "--eval-mode",
                "single",
                "--judge-provider",
                "mock",
                "--judge-api-key",
                "not-used",
                "--grounding",
                "fixture",
                "--output",
                str(tmp_path / "reports"),
                "--reliability-out",
                str(tmp_path / "runs"),
                "--no-telemetry",
            ],
        )
        assert result.exit_code == 2, result.output
        assert requests and requests[0]["messages"][-1]["content"] == "Hello"
        assert list((tmp_path / "reports").glob("*.json"))
        assert probe._session is None or probe._session.closed
    finally:
        asyncio.run(probe.aclose())
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.asyncio
async def test_cancelled_probe_closes_a_real_aiohttp_session():
    module = importlib.import_module("ifixai.cli.run")
    provider = HttpProvider()
    session = await provider.get_session()
    started = asyncio.Event()

    async def operation():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(module._probe_then_close(provider, operation()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.closed
    assert provider._session is None
