"""A completed inspection may only resume against the same SUT context."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.core.types import RunMode
from ifixai.evaluation.manifest import (
    RunManifest,
    build_manifest,
    compute_run_id,
    compute_sut_context_digest,
    load_manifest,
    verify_run_id,
)
from ifixai.evaluation.types import ModelDescriptor


@pytest.fixture
def owned_endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            payload = json.dumps({
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": "I cannot verify that claim. Please consult an authoritative source.",
                }}],
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
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("change", ["endpoint", "prompt", "temperature", "seed", "unchanged", "trailing-slash"])
def test_native_cli_resume_checks_sut_context(tmp_path, monkeypatch, owned_endpoint, change):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    base, requests = owned_endpoint
    fixture = Path(__file__).parents[1] / "fixtures/examples/customer_support.yaml"
    common = [
        "run", "--provider", "http", "--model", "owned-model",
        "--api-key", "synthetic-owned-key", "--fixture", str(fixture),
        "--grounding", "sut", "--test", "B07", "--eval-mode", "single",
        "--judge-provider", "mock", "--judge-api-key", "owned-unused-key",
        "--output", str(tmp_path / "reports"),
        "--reliability-out", str(tmp_path / "runs"),
        "--no-telemetry", "--no-parallel",
    ]
    first = CliRunner().invoke(ifixai_cli, [*common, "--endpoint", base + "/first/v1",
                                           "--system-prompt", "Original policy"])
    assert first.exit_code == 2, first.output
    assert requests and all(path.startswith("/first/v1/") for path, _ in requests)
    manifests = list((tmp_path / "runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert "synthetic-owned-key" not in manifests[0].read_text()
    def inspection_requests():
        return [(path, body) for path, body in requests
                if path.endswith("/chat/completions") and
                body.get("messages") != [{"role": "user", "content": "Hello"}]]

    before = len(inspection_requests())
    endpoint = base + ("/changed/v1" if change == "endpoint" else "/first/v1")
    prompt = "Different policy" if change == "prompt" else "Original policy"
    if change == "trailing-slash":
        endpoint += "/"
    sampling = ["--sut-temperature", "0.5"] if change == "temperature" else (
        ["--sut-seed", "42"] if change == "seed" else [])
    resumed = CliRunner().invoke(ifixai_cli, [*common, "--endpoint", endpoint,
        "--system-prompt", prompt, "--resume", manifest["run_id"], *sampling])
    assert len(inspection_requests()) == before, "Resume must not rerun completed inspections"
    if change in {"unchanged", "trailing-slash"}:
        assert resumed.exit_code == 2, resumed.output
        assert "[reused] B07" in resumed.output
    else:
        assert resumed.exit_code == 1, resumed.output
        assert "run configuration changed" in resumed.output


def test_current_manifest_verifies_and_context_is_not_plaintext():
    digest = compute_sut_context_digest("http://owned/v1", "Private policy")
    manifest = build_manifest(
        mode=RunMode.STANDARD,
        model_under_test=ModelDescriptor(provider="http", model_id="owned", version="1"),
        judge_models=[], normalizer_version="1", test_versions={}, rubric_hashes={},
        fixture_digest="a" * 64, run_nonce="0123456789abcdef",
        sut_context_digest=digest,
    )
    assert verify_run_id(manifest)
    assert manifest.schema_version == 4
    assert "Private policy" not in manifest.model_dump_json()
    assert "http://owned" not in manifest.model_dump_json()
    changed = manifest.model_copy(update={"sut_context_digest": "b" * 64})
    assert not verify_run_id(changed)


def test_historical_v3_hash_still_verifies_without_context(tmp_path):
    manifest = RunManifest(
        run_id="pending", timestamp="2026-10-01T00:00:00Z", schema_version=3,
        mode=RunMode.STANDARD,
        model_under_test=ModelDescriptor(provider="http", model_id="owned", version="1"),
        normalizer_version="1", test_versions={}, fixture_digest="a" * 64,
        run_nonce="0123456789abcdef",
    )
    payload = manifest.model_dump(mode="json", exclude={"sut_context_digest"})
    payload["run_id"] = compute_run_id(payload)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload))
    loaded = load_manifest(path)
    assert verify_run_id(loaded)
    assert loaded.run_nonce == payload["run_nonce"]
    assert loaded.sut_context_digest is None
