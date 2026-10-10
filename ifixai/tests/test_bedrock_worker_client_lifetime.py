"""Native boto3 client ownership follows the blocking worker, not its awaiter."""
import asyncio
import json
import threading
import traceback
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderAuthError, ProviderTimeoutError

pytest.importorskip('boto3')
from ifixai.providers.bedrock import BedrockProvider


@contextmanager
def owned_api(mode):
    received = threading.Event()
    release = threading.Event()
    connections = []
    requests = []
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup()
            self.closed_event = threading.Event()
            with lock:
                connections.append(self.closed_event)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with lock:
                requests.append(body)
                count = len(requests)
            received.set()
            if mode == 'paused':
                release.wait(3)
            error = None
            if mode == 'denied':
                error = (403, 'AccessDeniedException')
            elif mode == 'retry' and count == 1:
                error = (503, 'ServiceUnavailableException')
            payload = {'output': {'message': {'role': 'assistant', 'content': [{'text': 'owned answer'}]}}, 'stopReason': 'end_turn', 'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2}, 'metrics': {'latencyMs': 1}}
            if error:
                payload = {'__type': error[1], 'message': 'owned failure'}
            encoded = json.dumps(payload).encode()
            try:
                self.send_response(error[0] if error else 200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                if error:
                    self.send_header('x-amzn-ErrorType', error[1])
                self.end_headers()
                self.wfile.write(encoded)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        def finish(self):
            try:
                super().finish()
            finally:
                self.closed_event.set()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', received, release, connections, requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
def native_credentials(monkeypatch):
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'owned-key')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'owned-secret')
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED', 'true')



@pytest.fixture
def native_close_trace(monkeypatch):
    from botocore.client import BaseClient
    original_call = BaseClient._make_api_call
    original_close = BaseClient.close
    active = set()
    worker_threads = {}
    closes = []
    def call(client, *args, **kwargs):
        active.add(id(client))
        worker_threads[id(client)] = threading.get_ident()
        try:
            return original_call(client, *args, **kwargs)
        finally:
            active.discard(id(client))
    def close(client):
        closes.append((id(client) in active, threading.get_ident(), worker_threads.get(id(client))))
        return original_close(client)
    monkeypatch.setattr(BaseClient, '_make_api_call', call)
    monkeypatch.setattr(BaseClient, 'close', close)
    return closes


def send(provider, endpoint, timeout=2, retries=0):
    return provider.send_message([ChatMessage(content='owned prompt')], ProviderConfig(provider='bedrock', model='owned', endpoint=endpoint, timeout=timeout, max_retries=retries))


async def wait_closed(connections):
    for _ in range(100):
        if connections and all(event.is_set() for event in connections):
            return True
        await asyncio.sleep(.01)
    return False


@pytest.mark.parametrize('mode', ['success', 'denied'])
async def test_worker_releases_native_keepalive_with_retained_error(mode, native_credentials):
    with owned_api(mode) as (endpoint, _received, _release, connections, requests):
        provider = BedrockProvider()
        retained = None
        if mode == 'denied':
            with pytest.raises(ProviderAuthError) as raised:
                await send(provider, endpoint)
            retained = raised.value
            from botocore.exceptions import ClientError
            assert isinstance(retained.__cause__, ClientError)
            assert retained.__cause__.response["Error"]["Code"] == "AccessDeniedException"
            assert retained.__cause__.response["ResponseMetadata"]["HTTPStatusCode"] == 403
            assert retained.__cause__.operation_name == "Converse"
            native_frames = traceback.extract_tb(retained.__cause__.__traceback__)
            assert any(frame.name == "_make_api_call" for frame in native_frames)
            assert all(frame.filename and frame.lineno > 0 for frame in native_frames)
            assert "AccessDeniedException" in "".join(traceback.format_exception(retained.__cause__))
        else:
            assert await send(provider, endpoint) == 'owned answer'
        await provider.aclose()
        assert len(requests) == 1
        assert await wait_closed(connections), 'owned socket remains open after the worker completed'
        if retained is not None:
            assert retained.details and retained.__cause__ is not None


async def test_each_native_retry_attempt_owns_and_closes_its_client(native_credentials):
    with owned_api('retry') as (endpoint, _received, _release, connections, requests):
        assert await send(BedrockProvider(), endpoint, retries=1) == 'owned answer'
        assert len(requests) == 2
        assert len(connections) == 2
        assert await wait_closed(connections)


@pytest.mark.parametrize('operation', ['cancel', 'outer-deadline'])
async def test_cancelled_awaiter_does_not_close_active_native_worker(operation, native_credentials, native_close_trace):
    with owned_api('paused') as (endpoint, received, release, connections, requests):
        task = asyncio.create_task(send(BedrockProvider(), endpoint))
        assert await asyncio.to_thread(received.wait, 2)
        if operation == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(task, .05)
        await asyncio.sleep(.05)
        assert len(connections) == 1
        assert not connections[0].is_set(), 'client closed while native worker still needed it'
        assert not native_close_trace, 'native client was closed before blocking transport finished'
        release.set()
        assert await wait_closed(connections), 'worker finished but never released its socket'
        assert len(native_close_trace) == 1
        active, close_thread, worker_thread = native_close_trace[0]
        assert not active
        assert close_thread == worker_thread
        assert len(requests) == 1


async def test_native_timeout_remains_typed_and_eventually_releases_worker(native_credentials):
    with owned_api('paused') as (endpoint, _received, release, connections, requests):
        with pytest.raises(ProviderTimeoutError):
            await send(BedrockProvider(), endpoint, timeout=1)
        release.set()
        assert await wait_closed(connections)
        assert len(requests) == 1
