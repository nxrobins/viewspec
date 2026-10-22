"""The review server's request logging never writes capability material (finding N4).

Review URLs carry bearer capabilities in the path: the single-use `/open/<token>` bootstrap and the
frame tickets under `/frame/<ticket>/`. BaseHTTPRequestHandler hands the full request line to
`log_message`, and quotes it again in parse errors, so the handler must redact the request target
before anything reaches the `viewspec.review_server` logger.

The assertions avoid depending on the HTTP bytes a malformed request produces: `http.server`
changed what status line it writes for an invalid request version between 3.11-3.14, so the test
checks only what the handler logs (which is ViewSpec's code and identical on every version).
"""

from __future__ import annotations

import http.client
import json
import logging
import socket

from viewspec.intent_tools import starter_intent_payload
from viewspec.review_runtime import ReviewRuntime
from viewspec.review_server import ReviewServer

LOGGER_NAME = "viewspec.review_server"


def _server(tmp_path) -> ReviewServer:
    source = tmp_path / "viewspec.intent.json"
    source.write_text(json.dumps(starter_intent_payload(), sort_keys=True), encoding="utf-8")
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return ReviewServer(runtime, port=port)


def _get(port: int, path: str) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def _raw_request(port: int, request_line: bytes) -> None:
    """Send one raw request line and drain the reply, without asserting on its bytes."""
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(request_line + b"\r\nHost: 127.0.0.1\r\n\r\n")
        try:
            while connection.recv(4096):
                pass
        except OSError:
            pass


def test_request_logging_redacts_bootstrap_tokens_and_frame_tickets(tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    server = _server(tmp_path)
    bootstrap_path = server.bootstrap_path
    frame_path = server.frame_path("index.html")
    token = bootstrap_path.rsplit("/", 1)[1]
    ticket = frame_path.split("/")[2]
    assert len(token) >= 16 and len(ticket) >= 16
    server.start()
    try:
        assert _get(server.port, frame_path) == 200
        assert _get(server.port, bootstrap_path) == 303
        # Malformed request lines that put a capability where BaseHTTPRequestHandler quotes it in a
        # parse error: as the whole request line (one word -> "Bad request syntax (%r)") and in the
        # version slot (three words, invalid version -> "Bad request version (%r)"). These exercise
        # the quoting path on every Python version, where only the emitted status line differs.
        _raw_request(server.port, bootstrap_path.encode())
        _raw_request(server.port, frame_path.encode())
        _raw_request(server.port, f"GET x {bootstrap_path}".encode())
        _raw_request(server.port, f"GET y {frame_path}".encode())
    finally:
        server.stop()

    records = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    # Well-formed requests log a redacted target with the real status, proving the redaction ran
    # rather than the whole line being dropped.
    assert any('"GET <redacted> HTTP/1.1" 200' in message for message in records), records
    assert any('"GET <redacted> HTTP/1.1" 303' in message for message in records), records
    # The malformed lines reached the parse-error path, which quotes the request line.
    assert any("code 400" in message for message in records), records
    assert any("<redacted>" in message for message in records), records
    # No capability material -- token, ticket, or the route prefixes that frame them -- ever lands
    # in a record, on any interpreter.
    for message in records:
        assert token not in message, message
        assert ticket not in message, message
        assert "/open/" not in message and "/frame/" not in message, message
