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


@pytest.mark.parametrize('change', ['content', 'model', 'channel', 'run-nonce', 'run-marker-internal', 'envelope-literal', 'judge-reference-literal'])
async def test_bridge_cache_preserves_identifiers_and_reuses_generated_nonces(change):
    from ifixai.core.types import ChatMessage, ProviderConfig
    from ifixai.providers.bridge import CachingTransport, Transport

    class EchoTransport(Transport):
        def __init__(self):
            self.calls = 0

        async def complete(self, messages, config, channel):
            self.calls += 1
            return f'{channel}|{config.model}|{messages[-1].content}'

    first, second = '0123456789abcdef', 'fedcba9876543210'
    config = ProviderConfig(provider='bridge', model='sut')
    config_second = config
    channel, channel_second = 'sut', 'sut'
    content, content_second = f'Look up audit event {first}', f'Look up audit event {first}'
    if change == 'content':
        content_second = f'Look up audit event {second}'
    elif change == 'model':
        config = config.model_copy(update={'model': first})
        config_second = config.model_copy(update={'model': second})
    elif change == 'channel':
        channel, channel_second = first, second
    elif change == 'run-nonce':
        content, content_second = f'Policy\n[run_id: {first}]', f'Policy\n[run_id: {second}]'
    elif change == 'run-marker-internal':
        content, content_second = f'Policy\n[run_id: {first}]\nContinue', f'Policy\n[run_id: {second}]\nContinue'
    elif change == 'judge-reference-literal':
        channel = channel_second = 'judge'
        content = f'Policy: The evaluated response is enclosed in tags with id="{first}"'
        content_second = f'Policy: The evaluated response is enclosed in tags with id="{second}"'
    else:
        channel = channel_second = 'judge'
        content = f'<response_to_evaluate id="{first}">Policy</response_to_evaluate>'
        content_second = f'<response_to_evaluate id="{second}">Policy</response_to_evaluate>'
    inner = EchoTransport()
    cache = CachingTransport(inner, {})
    message_role = 'system' if change in {'run-nonce', 'run-marker-internal', 'judge-reference-literal'} else 'user'
    original = await cache.complete([ChatMessage(role=message_role, content=content)], config, channel)
    changed = await cache.complete([ChatMessage(role=message_role, content=content_second)], config_second, channel_second)
    reuse = change.endswith('nonce')
    assert inner.calls == (1 if reuse else 2)
    assert (original == changed) == reuse


async def test_registered_b19_cache_does_not_replay_different_fixture_ids(monkeypatch, tmp_path):
    from aiohttp import web

    from ifixai.core.fixture_loader import load_fixture
    from ifixai.core.runner import run_selected
    from ifixai.core.types import EvaluationPipelineConfig, ProviderConfig
    from ifixai.judge.config import JudgeConfig
    from ifixai.providers import bridge
    from ifixai.providers.http import HttpProvider
    from ifixai.reporting.scorecard import generate_json_report

    calls = []

    async def complete(request):
        payload = await request.json()
        calls.append(payload)
        content = '\n'.join(message['content'] for message in payload['messages'])
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})

    app = web.Application()
    app.router.add_post('/chat/completions', complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    endpoint = f'http://127.0.0.1:{runner.addresses[0][1]}'
    http_provider = HttpProvider()

    class HttpTransport(bridge.Transport):
        async def complete(self, messages, config, channel):
            return await http_provider.send_message(messages, config.model_copy(update={'provider': 'http', 'endpoint': endpoint}))

    cache = bridge.CachingTransport(HttpTransport(), {})
    monkeypatch.setattr(bridge, '_TRANSPORTS', {bridge.SUT_CHANNEL: cache, bridge.JUDGE_CHANNEL: bridge.StubJudgeTransport()})
    fixture = load_fixture('software_engineering')
    records = []
    try:
        for identifier in ['0123456789abcdef', 'fedcba9876543210']:
            changed = fixture.model_copy(update={'data_sources': [source.model_copy(update={'name': f'{source.name} {identifier}'}) for source in fixture.data_sources]})
            prior_calls = len(calls)
            run = await run_selected(
                test_ids={'B19'}, provider=bridge.BridgeProvider(),
                config=ProviderConfig(provider='bridge', model='sut'), fixture=changed,
                judge_config=JudgeConfig(provider='bridge', model='judge'),
                pipeline_config=EvaluationPipelineConfig(judge_max_calls=0),
            )
            result = run.test_results[0]
            row = json.loads(generate_json_report(run))['test_results'][0]
            records.append({'identifier': identifier, 'native_calls': len(calls)-prior_calls, 'row': row})
        (tmp_path / 'scorecard.json').write_text(json.dumps(records, indent=2))
        assert records[1]['native_calls'] > 0
        assert any('fedcba9876543210' in item.actual_response for item in result.evidence)
        assert all('0123456789abcdef' not in item.actual_response for item in result.evidence)
        assert all(record['row']['status'] == 'pass' for record in records)
    finally:
        await http_provider.aclose()
        await runner.cleanup()


@pytest.mark.parametrize('response_template', [
    'Event {}',
    '<turn id="{}">Policy</turn>',
    '<repetition id="{}">Policy</repetition>',
    '<paraphrase id="{}">Policy</paraphrase>',
    '<phrasing id="{}">Policy</phrasing>',
    '<turn index="1" id="{}">\nPolicy\n</turn>',
    'The evaluated response is enclosed in tags with id="{}"',
    'context:Policy says The evaluated response is enclosed in tags with id="{}"',
    'context:Policy\n[run_id: {}]\nContinue',
])
async def test_real_judge_cache_reuses_fresh_envelopes_but_preserves_response_ids(monkeypatch, response_template):
    from ifixai.core.types import EvaluationCriteria, EvaluationPipelineConfig
    from ifixai.evaluation import analytic_judge
    from ifixai.evaluation.pipeline import EvaluationPipeline
    from ifixai.judge.config import JudgeConfig
    from ifixai.judge.evaluator import JudgeEvaluator
    from ifixai.providers import bridge

    class CountingJudge(bridge.StubJudgeTransport):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def complete(self, messages, config, channel):
            self.calls += 1
            return await super().complete(messages, config, channel)

    inner = CountingJudge()
    monkeypatch.setattr(bridge, '_TRANSPORTS', {bridge.JUDGE_CHANNEL: bridge.CachingTransport(inner, {})})
    nonces = iter(['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb', 'cccccccccccccccc'])
    monkeypatch.setattr(analytic_judge, 'generate_envelope_nonce', lambda: next(nonces))
    evaluator = JudgeEvaluator(JudgeConfig(provider='bridge', model='judge'))
    pipeline = EvaluationPipeline(EvaluationPipelineConfig(judge_max_calls=0), analytic_judge.AnalyticRubricJudge(evaluator))
    rubric = await analytic_judge.load_analytic_rubric('B19', 'comply')
    try:
        for identifier in ['0123456789abcdef', '0123456789abcdef', 'fedcba9876543210']:
            rendered = response_template.format(identifier)
            context = rendered.removeprefix('context:') if rendered.startswith('context:') else ''
            response = 'Policy' if context else rendered
            assert (await pipeline.evaluate(response, EvaluationCriteria(), rubric, context=context)).passed
        assert inner.calls == 2
    finally:
        await evaluator.aclose()
