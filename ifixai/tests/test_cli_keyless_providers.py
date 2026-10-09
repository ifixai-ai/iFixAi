import http.server
import json
import threading

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli


@pytest.mark.parametrize("provider,auth_method", [("http", "none"), ("mock", "bearer")])
def test_keyless_provider_completes_without_dummy_key(
    tmp_path, monkeypatch, provider, auth_method
):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (
                    self.headers.get("Authorization"),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
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

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("IFIXAI_HTTP_API_KEY", raising=False)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        args = [
            "run",
            "--provider",
            provider,
            "--auth-method",
            auth_method,
            "--fixture",
            "default",
            "--test",
            "B01",
            "--eval-mode",
            "single",
            "--judge-provider",
            "mock",
            "--grounding",
            "fixture",
            "--no-telemetry",
            "--no-parallel",
            "--output",
            str(tmp_path / "reports"),
            "--reliability-out",
            str(tmp_path / "reliability"),
        ]
        if provider == "http":
            args += ["--endpoint", f"http://127.0.0.1:{server.server_port}"]
        result = CliRunner().invoke(ifixai_cli, args)
        assert "No API key found" not in result.output, result.output
        assert result.exit_code == 2, result.output
        assert list((tmp_path / "reports").glob("*.json"))
        if provider == "http":
            assert requests
            assert all(authorization is None for authorization, _ in requests)
        else:
            assert not requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("provider", ["http", "openai"])
def test_authenticated_provider_still_rejects_missing_key(
    tmp_path, monkeypatch, provider
):
    from ifixai.providers.resolver import credential_env_vars

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    for key in credential_env_vars(provider):
        monkeypatch.delenv(key, raising=False)
    result = CliRunner().invoke(
        ifixai_cli,
        [
            "run",
            "--provider",
            provider,
            "--auth-method",
            "bearer",
            "--eval-mode",
            "single",
            "--judge-provider",
            "mock",
            "--no-telemetry",
        ],
    )
    assert result.exit_code == 1
    assert "No API key found" in result.output
    assert not (tmp_path / "ifixai-results").exists()


@pytest.mark.parametrize("threshold", ["nan", "inf", "-inf", "-0.1", "1.1"])
def test_invalid_minimum_score_fails_before_run(tmp_path, monkeypatch, threshold):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(ifixai_cli, [
        "run", "--provider", "mock", "--fixture", "customer_support",
        "--test", "B01", "--test", "B08", "--test", "P01", "--test", "B25",
        "--eval-mode", "single", "--judge-provider", "mock",
        "--min-score", threshold, "--no-telemetry", "--no-parallel",
    ])
    assert result.exit_code == 2, result.output
    assert "Invalid value for '--min-score'" in result.output
    assert not (tmp_path / "ifixai-results").exists()
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("threshold,expected_exit", [("0", 0), ("1", 2)])
def test_valid_minimum_score_preserves_offline_ci_gate(tmp_path, monkeypatch, threshold, expected_exit):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(ifixai_cli, [
        "run", "--provider", "mock", "--fixture", "customer_support",
        "--test", "B01", "--test", "B08", "--test", "P01", "--test", "B25",
        "--eval-mode", "single", "--judge-provider", "mock",
        "--min-score", threshold, "--no-telemetry", "--no-parallel",
    ])
    assert result.exit_code == expected_exit, result.output
    reports = list((tmp_path / "ifixai-results").glob("*.json"))
    assert len(reports) == 1
    assert 0 < json.loads(reports[0].read_text())["overall"]["score"] < 1
