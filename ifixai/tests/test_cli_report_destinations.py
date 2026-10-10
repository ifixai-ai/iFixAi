"""Known-invalid report destinations must fail before native provider calls."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli


@pytest.fixture
def owned_endpoint():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps({"choices": [{"message": {"content": "Owned fixture response"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def invoke(tmp_path, monkeypatch, endpoint, extra):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    fixture = Path(__file__).parents[1] / "fixtures/examples/customer_support.yaml"
    return CliRunner().invoke(ifixai_cli, [
        "run", "--provider", "http", "--endpoint", endpoint, "--auth-method", "none",
        "--fixture", str(fixture), "--mode", "standard", "--eval-mode", "self",
        "--test", "B03", "--timeout", "2", "--min-score", "0", "--no-telemetry",
        "--no-parallel", "--reliability-out", str(tmp_path / "runs"), *extra,
    ])


@pytest.mark.parametrize("destination", ["output", "artifact", "config_output"])
def test_invalid_destinations_fail_before_owned_http_calls(tmp_path, monkeypatch, owned_endpoint, destination):
    endpoint, calls = owned_endpoint
    target = tmp_path / "invalid"
    if destination == "artifact":
        target.mkdir()
        extra = ["--output", str(tmp_path / "reports"), "--artifact-out", str(target)]
        option = "--artifact-out"
    else:
        target.write_text("previous artifact", encoding="utf-8")
        option = "--output"
        if destination == "config_output":
            (tmp_path / "ifixai.yaml").write_text(f"output: {target}\n", encoding="utf-8")
            extra = []
        else:
            extra = ["--output", str(target)]
    result = invoke(tmp_path, monkeypatch, endpoint, extra)
    assert calls == [], (calls, result.output)
    assert result.exit_code == 2, result.output
    assert option in result.output
    if target.is_file():
        assert target.read_text(encoding="utf-8") == "previous artifact"


@pytest.mark.parametrize("existing", [False, True])
def test_valid_destinations_preserve_native_calls_reports_and_stdout(tmp_path, monkeypatch, owned_endpoint, existing):
    endpoint, calls = owned_endpoint
    output = tmp_path / "reports"
    artifact = tmp_path / "artifact.html"
    if existing:
        output.mkdir()
        artifact.write_text("previous artifact", encoding="utf-8")
    result = invoke(tmp_path, monkeypatch, endpoint, ["--output", str(output), "--artifact-out", str(artifact)])
    assert calls, result.output
    assert "Reports saved:" in result.output
    assert "Interactive artifact ->" in result.output
    assert len(list(output.glob("*.json"))) == 1
    assert "<html" in artifact.read_text(encoding="utf-8").lower()
