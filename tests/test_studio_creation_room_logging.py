"""The Studio creation room's request logging never writes capability material (finding N4).

The creation room's bootstrap route is `/open/<token>`, a single-use bearer capability.
`StudioCreationRoomServer`'s handler now redacts the request target before anything reaches the
`viewspec.studio_creation_room` logger, mirroring `ReviewServer.log_message`. As in the review-server
logging test, the assertions check only what the handler logs (ViewSpec's code, identical on every
Python version), not the HTTP bytes a malformed request produces.
"""

from __future__ import annotations

import http.client
import logging
import socket

from viewspec.studio_creation import prepare_studio_creation
from viewspec.studio_creation_room import StudioCreationRoomController, StudioCreationRoomServer

LOGGER_NAME = "viewspec.studio_creation_room"


def _server(tmp_path) -> StudioCreationRoomServer:
    prepare_studio_creation(brief="Build a dispatch console for field crews.", cwd=tmp_path)
    controller = StudioCreationRoomController(cwd=tmp_path, handoff=lambda _source, _accepted: None)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return StudioCreationRoomServer(controller, port=port, state_root=tmp_path / "state")


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
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(request_line + b"\r\nHost: 127.0.0.1\r\n\r\n")
        try:
            while connection.recv(4096):
                pass
        except OSError:
            pass


def test_creation_room_logging_redacts_bootstrap_token(tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    server = _server(tmp_path)
    bootstrap_path = server.bootstrap_url.removeprefix(server.origin)
    token = bootstrap_path.rsplit("/", 1)[1]
    assert bootstrap_path.startswith("/open/") and len(token) >= 16
    server.start()
    try:
        # A well-formed bootstrap GET (303 redirect to the room) and a malformed line that quotes
        # the token in a parse error.
        assert _get(server.port, bootstrap_path) == 303
        _raw_request(server.port, bootstrap_path.encode())
        _raw_request(server.port, f"GET x {bootstrap_path}".encode())
    finally:
        server.stop()

    records = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert records, "the creation room logged nothing"
    assert any('"GET <redacted> HTTP/1.1" 303' in message for message in records), records
    for message in records:
        assert token not in message, message
        assert "/open/" not in message, message
