from __future__ import annotations

import http.server
import threading
import urllib.request
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "experiments/browser-agent-probe/comparison"


@pytest.fixture
def upstream():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_shared_proxy_journal_is_binary_safe_and_rejects_corruption(monkeypatch, tmp_path, upstream):
    monkeypatch.syspath_prepend(str(PROBE))
    from transport import BenchmarkProxy

    proxy = BenchmarkProxy(upstream, tmp_path / "transport", methods=("GET", "HEAD", "POST"))
    body = b"\x00\xff\x80fixture"
    try:
        with urllib.request.urlopen(urllib.request.Request(proxy.origin + "/save", data=body), timeout=3) as response:
            assert response.read() == body
    finally:
        proxy.shutdown()
    journal = proxy.journal()
    assert len(journal) == 1
    assert journal[0]["body_base64"] == "AP+AZml4dHVyZQ=="
    assert journal[0]["body_complete"] and journal[0]["response_complete"]
    next(proxy.directory.glob("*.request.bin")).write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="integrity"):
        proxy.journal()


def test_crashed_proxy_cannot_be_scored_as_a_complete_audit(monkeypatch, tmp_path, upstream):
    monkeypatch.syspath_prepend(str(PROBE))
    from transport import BenchmarkProxy

    proxy = BenchmarkProxy(upstream, tmp_path / "transport")
    proxy.process.kill()
    proxy.shutdown()
    with pytest.raises(RuntimeError, match="incomplete or invalid"):
        proxy.journal()
