"""A stand-in agent for smoke tests: an OpenAI-shaped chat endpoint on localhost.

It answers every message with the same refusal, makes no outbound calls, and
exists so `ifixai audit` and the GitHub Action can be exercised end to end in CI
without a real agent or any key.

Usage: python scripts/fake_agent.py [port]   (default 8765)
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

REPLY = "I can't do that. Please ask an administrator through the approved channel."


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(min(length, 1024 * 1024))
        body = json.dumps({
            "id": "chatcmpl-smoke",
            "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": REPLY}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
