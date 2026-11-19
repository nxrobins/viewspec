"""Benign loopback integration coverage of each browser network policy channel."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import ssl
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_browser_bridge_boundary import _build
from tests.test_browser_bridge_e2e import PROBE

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("VIEWSPEC_RUN_BROWSER_BRIDGE_E2E") != "1", reason="opt-in browser integration checks"
    ),
]


@pytest.fixture
def network_site() -> Iterator[tuple[str, list[str]]]:
    received: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.do_GET()

        def do_GET(self) -> None:
            received.append(f"{self.command} {self.path} {self.headers['Host']}")
            route = urlsplit(self.path).path
            redirects = {
                "/hop1": "/hop2",
                "/hop2": "/read",
                "/unknown-hop": "/unclassified",
                "/post-hop": "/post-final",
                "/cdn-hop": f"http://localhost:{self.server.server_port}/read",
            }
            if route in redirects:
                self.send_response(307 if route == "/post-hop" else 302)
                self.send_header("Location", redirects[route])
                self.end_headers()
                return
            if route == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: hello\n\n")
                return
            scripts = {
                "/reads": "fetch('/hop1').catch(()=>{})",
                "/classified": "fetch('/unknown-hop').catch(()=>{})",
                "/posts": "fetch('/post-hop',{method:'POST',body:'test'}).catch(()=>{})",
                "/denied-post": "fetch('/write',{method:'POST',headers:{accept:'application/json'},body:'test'}).then(async r=>document.querySelector('h1').textContent=r.status+' '+(await r.json()).state)",
                "/denied-get": "fetch('/update',{headers:{accept:'application/json'}}).then(async r=>document.querySelector('h1').textContent=r.status+' '+(await r.json()).state)",
                "/cross-origin-post": "fetch('http://localhost:'+location.port+'/write',{method:'POST',body:'test'}).then(r=>document.querySelector('h1').textContent=String(r.status))",
                "/mutations": "fetch('/update').catch(()=>{})",
                "/method-override": "fetch('/read?_method=PATCH').catch(()=>{})",
                "/header-override": "fetch('/read',{headers:{'X-HTTP-Method-Override':'PATCH'}}).catch(()=>{})",
                "/eventsource": "const es=new EventSource('/events');es.onmessage=()=>es.close()",
                "/workers": "new Worker('/worker.js')",
                "/workers404": "new Worker('/worker.js')",
                "/blob-workers": "new Worker(URL.createObjectURL(new Blob([\"fetch('/worker-read')\"],{type:'text/javascript'})))",
                "/sockets": "new WebSocket('ws://'+location.host+'/socket')",
                "/frames": "document.body.innerHTML='<iframe src=/read></iframe><iframe src=http://localhost:'+location.port+'/frame-document></iframe><img src=http://localhost:'+location.port+'/image>'",
                "/same-frame": "document.body.innerHTML='<iframe src=/read></iframe>'",
                "/frame-redirect": "document.body.innerHTML='<iframe src=/cdn-hop></iframe>'",
                "/frame-srcdoc": "const f=document.createElement('iframe');f.onload=()=>{const b=f.contentDocument.querySelector('button');if(b)document.querySelector('h1').textContent=b.textContent};f.srcdoc='<button>Srcdoc ready</button>';document.body.append(f)",
                "/frame-schemes": "const a=document.createElement('iframe');a.src='data:text/html,Data%20frame';document.body.append(a);const b=document.createElement('iframe');b.src=URL.createObjectURL(new Blob(['Blob frame'],{type:'text/html'}));document.body.append(b)",
                "/popup": "window.open('/read')",
            }
            self.send_response(404 if route == "/workers404" else 200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                (
                    "<!doctype html><h1>Network policy fixture</h1><script>" + scripts.get(route, "") + "</script>"
                ).encode()
            )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _open(url: str, boundary: object) -> tuple[dict, dict]:
    commands = [
        {"id": 1, "cmd": "open", "url": url, "policy": boundary.browser_config()},
        {"id": 2, "cmd": "writes"},
        {"id": 3, "cmd": "close"},
    ]
    replies = _run(commands)
    assert replies[0]["ok"], replies[0]
    return replies[0]["result"], replies[1]["result"]


def _run(commands: list[dict], node_args: tuple[str, ...] = ()) -> list[dict]:
    result = subprocess.run(
        ["node", *node_args, str(PROBE / "bridge.js")],
        input="".join(json.dumps(item) + "\n" for item in commands),
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == len(commands), result.stdout + result.stderr
    return replies


def test_allowed_redirect_chain_keeps_browser_url(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    opened, journal = _open(base + "/hop1", _build(allow_origin=[base], allow_read=[base + "/*"]))
    assert opened["url"] == base + "/read"
    assert [entry.split()[1] for entry in received] == ["/hop1", "/hop2", "/read"]
    assert not journal["refusals"]


def test_fragment_uses_the_underlying_read_permission(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    opened, journal = _open(base + "/read#section", _build(allow_origin=[base], allow_read=[base + "/read"]))
    assert opened["url"] == base + "/read#section"
    assert [entry.split()[1] for entry in received] == ["/read"]
    assert not journal["refusals"]


def test_each_redirect_target_requires_its_own_permission(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    _, journal = _open(
        base + "/classified",
        _build(allow_origin=[base], read_policy="explicit", allow_read=[base + "/classified", base + "/unknown-hop"]),
    )
    assert all("/unclassified" not in entry for entry in received)
    assert any("not been classified" in entry["reason"] for entry in journal["refusals"])


def test_redirected_post_does_not_inherit_write_permission(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    _, journal = _open(
        base + "/posts",
        _build(allow_origin=[base], allow_read=[base + "/posts"], allow_write=["POST " + base + "/post-hop"]),
    )
    assert any("POST /post-hop " in entry for entry in received)
    assert all("/post-final" not in entry for entry in received)
    assert any(entry["reason"] == "write permission required" for entry in journal["refusals"])
    assert any(entry["url"] == base + "/post-final" and entry["state"] == "blocked" for entry in journal["writes"])


def test_get_mutation_overrides_a_broad_read_permission(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    _, journal = _open(
        base + "/mutations", _build(allow_origin=[base], allow_read=[base + "/*"], mutation_url=[base + "/update"])
    )
    assert all("/update" not in entry for entry in received)
    assert journal["writes"][0]["method"] == "GET"


def test_host_permission_does_not_authorize_documents(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    _, journal = _open(
        base + "/frames", _build(allow_origin=[base], allow_host=["localhost"], read_policy="permissive")
    )
    assert any("/read " in entry for entry in received)
    assert any("/image " in entry for entry in received)
    assert all("/frame-document " not in entry for entry in received)
    assert any(
        entry["reason"] in {"frame origin must match the top document", "document security policy blocked a frame"}
        for entry in journal["refusals"]
    )


@pytest.mark.parametrize(
    ("path", "absent"),
    [("/workers", "/worker.js"), ("/workers404", "/worker.js"), ("/blob-workers", "/worker-read"), ("/popup", "/read")],
)
def test_unsupported_execution_channels_fail_closed(
    network_site: tuple[str, list[str]], path: str, absent: str
) -> None:
    base, received = network_site
    _open(base + path, _build(allow_origin=[base], allow_read=[base + "/*"]))
    assert all(absent not in entry for entry in received)


def test_socket_deny_applies_even_when_writes_are_allowed(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    _, journal = _open(
        base + "/sockets",
        _build(
            allow_origin=[base],
            allow_writes=True,
            read_policy="permissive",
            deny_url=[base.replace("http:", "ws:") + "/socket"],
        ),
    )
    assert all("/socket " not in entry for entry in received)
    assert journal["writes"][0]["method"] == "WEBSOCKET"


@pytest.mark.parametrize("path", ["/method-override", "/header-override"])
def test_method_override_requests_require_write_permission(network_site: tuple[str, list[str]], path: str) -> None:
    base, received = network_site
    _, journal = _open(base + path, _build(allow_origin=[base], allow_read=[base + "/*"]))
    assert all("/read" not in entry for entry in received)
    assert journal["writes"][0]["method"] == "GET"


@pytest.mark.parametrize("permitted", [False, True])
def test_eventsource_has_its_own_channel_permission(network_site: tuple[str, list[str]], permitted: bool) -> None:
    base, received = network_site
    rules = ["EVENTSOURCE " + base + "/events"] if permitted else []
    _, journal = _open(base + "/eventsource", _build(allow_origin=[base], allow_read=[base + "/*"], allow_write=rules))
    assert any("GET /events " in entry for entry in received) is permitted
    assert bool(journal["writes"]) is not permitted


def test_blanket_writes_do_not_disable_explicit_read_classification(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    _, journal = _open(
        base + "/classified", _build(allow_origin=[base], allow_writes=True, allow_read=[base + "/classified"])
    )
    assert all("/unknown-hop" not in entry for entry in received)
    assert journal["refusals"][0]["reason"] == "this read has not been classified by the operator"


def test_frames_cannot_change_origin_even_when_both_origins_are_listed(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    other = base.replace("127.0.0.1", "localhost")
    _, journal = _open(
        base + "/frame-redirect", _build(allow_origin=[base, other], allow_read=[base + "/*", other + "/*"])
    )
    assert any("/cdn-hop " in entry for entry in received)
    assert all(other.removeprefix("http://") not in entry for entry in received)
    assert journal["navigations"][0]["reason"] == "frame origin must match the top document"


def test_csp_rejects_networkless_frame_documents(network_site: tuple[str, list[str]]) -> None:
    base, _ = network_site
    _, journal = _open(base + "/frame-schemes", _build(allow_origin=[base], allow_read=[base + "/*"]))
    # Chromium deliberately omits/sanitizes the data/blob URL in its authoritative audit event.
    assert len(journal["refusals"]) == 2
    assert all(entry["reason"] == "document security policy blocked a frame" for entry in journal["refusals"])


def test_ordinary_same_origin_srcdoc_still_loads(network_site: tuple[str, list[str]]) -> None:
    base, _ = network_site
    replies = _run(
        [
            {
                "id": 1,
                "cmd": "open",
                "url": base + "/frame-srcdoc",
                "policy": _build(allow_origin=[base], allow_read=[base + "/*"]).browser_config(),
            },
            {"id": 2, "cmd": "aria"},
            {"id": 3, "cmd": "close"},
        ]
    )
    assert replies[0]["ok"], replies[0]
    assert "Srcdoc ready" in replies[1]["result"]["aria"]


def test_frame_guard_tracks_repeated_opens_and_top_origin_changes(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    other = base.replace("127.0.0.1", "localhost")
    policy = _build(allow_origin=[base, other], allow_read=[base + "/*", other + "/*"]).browser_config()
    replies = _run(
        [
            {"id": i, "cmd": "open", "url": origin + "/same-frame", "policy": policy}
            for i, origin in enumerate([base, base, other], 1)
        ]
        + [{"id": 4, "cmd": "writes"}, {"id": 5, "cmd": "close"}]
    )
    assert all(reply["ok"] for reply in replies), replies
    assert replies[2]["result"]["url"] == other + "/same-frame"
    assert not replies[3]["result"]["refusals"]
    assert sum("/read " in entry for entry in received) == 3


@pytest.mark.parametrize(
    ("path", "method", "destination"),
    [
        ("/denied-post", "POST", "/write"),
        ("/denied-get", "GET", "/update"),
        ("/cross-origin-post", "POST", "/write"),
    ],
)
def test_forbidden_write_destinations_are_journaled_and_never_simulated(
    network_site: tuple[str, list[str]],
    path: str,
    method: str,
    destination: str,
) -> None:
    base, received = network_site
    boundary = _build(
        allow_origin=[base],
        allow_writes=True,
        read_policy="permissive",
        simulate_writes=True,
        mutation_url=[base + "/update"],
        deny_url=[] if path == "/cross-origin-post" else [base + destination],
    )
    replies = _run(
        [
            {"id": 1, "cmd": "open", "url": base + path, "policy": boundary.browser_config()},
            {"id": 2, "cmd": "writes"},
            {"id": 3, "cmd": "aria"},
            {"id": 4, "cmd": "close"},
        ]
    )
    assert all(reply["ok"] for reply in replies), replies
    assert all(f" {destination} " not in entry for entry in received)
    journal = replies[1]["result"]
    assert len(journal["writes"]) == 1
    assert journal["writes"][0]["method"] == method
    assert journal["writes"][0]["state"] == "blocked"
    assert journal["refusals"]
    assert "403" in replies[2]["result"]["aria"]


def test_redirected_denied_write_is_always_journaled(network_site: tuple[str, list[str]]) -> None:
    base, received = network_site
    _, journal = _open(
        base + "/posts",
        _build(
            allow_origin=[base],
            allow_read=[base + "/posts"],
            allow_write=["POST " + base + "/post-hop"],
            deny_url=[base + "/post-final"],
            simulate_writes=True,
        ),
    )
    assert any("POST /post-hop " in entry for entry in received)
    assert all("/post-final" not in entry for entry in received)
    assert journal["writes"][0]["method"] == "POST"
    assert journal["writes"][0]["state"] == "blocked"
    assert journal["refusals"][0]["reason"] == "a rule denies this address"


@pytest.mark.parametrize("simulate", [False, True])
def test_normal_write_refusals_keep_explicit_json_simulation_semantics(
    network_site: tuple[str, list[str]],
    simulate: bool,
) -> None:
    base, received = network_site
    boundary = _build(allow_origin=[base], allow_read=[base + "/*"], simulate_writes=simulate)
    replies = _run(
        [
            {"id": 1, "cmd": "open", "url": base + "/denied-post", "policy": boundary.browser_config()},
            {"id": 2, "cmd": "writes"},
            {"id": 3, "cmd": "aria"},
            {"id": 4, "cmd": "close"},
        ]
    )
    assert all(reply["ok"] for reply in replies), replies
    assert all("POST /write " not in entry for entry in received)
    state = "blocked-and-simulated" if simulate else "blocked"
    assert replies[1]["result"]["writes"][0]["state"] == state
    assert f"{'200' if simulate else '403'} {state}" in replies[2]["result"]["aria"]


def test_https_redirect_chain_and_target_policy(tmp_path: Path) -> None:
    """A real loopback TLS connection through the gate, with only this test certificate pinned.

    The preload changes this test process's Chromium launch arguments. Production launch and
    system/browser trust stores are unchanged; no global ignoreHTTPSErrors setting is used.
    """
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=localhost",
            "-days",
            "1",
        ],
        check=True,
        capture_output=True,
    )
    public = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"], check=True, capture_output=True
    ).stdout
    der = subprocess.run(
        ["openssl", "pkey", "-pubin", "-outform", "DER"], input=public, check=True, capture_output=True
    ).stdout
    pin = base64.b64encode(hashlib.sha256(der).digest()).decode()
    preload = tmp_path / "pin-test-certificate.cjs"
    preload.write_text(
        f"const {{chromium}} = require({json.dumps(str(PROBE / 'node_modules' / 'playwright'))});\n"
        "const launch = chromium.launch.bind(chromium);\n"
        "chromium.launch = (options) => launch({...options,args:[...(options.args||[]),"
        f"{json.dumps('--ignore-certificate-errors-spki-list=' + pin)}]}});\n",
        encoding="utf-8",
    )
    received: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            received.append(self.path)
            redirects = {"/start": "/middle", "/middle": "/final", "/next": "/unclassified"}
            if self.path in redirects:
                self.send_response(302)
                self.send_header("Location", redirects[self.path])
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Final</h1>"
                if self.path != "/page"
                else b"<h1>Page</h1><script>fetch('/next').catch(()=>{})</script>"
            )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"https://127.0.0.1:{server.server_port}"
        policy = _build(
            allow_origin=[base],
            read_policy="explicit",
            allow_read=[base + path for path in ["/start", "/middle", "/final", "/page", "/next"]],
        ).browser_config()
        replies = _run(
            [
                {"id": 1, "cmd": "open", "url": base + "/start", "policy": policy},
                {"id": 2, "cmd": "open", "url": base + "/page", "policy": policy},
                {"id": 3, "cmd": "writes"},
                {"id": 4, "cmd": "close"},
            ],
            node_args=("--require", str(preload)),
        )
        assert all(reply["ok"] for reply in replies), replies
        assert replies[0]["result"]["url"] == base + "/final"
        assert received == ["/start", "/middle", "/final", "/page", "/next"]
        assert replies[2]["result"]["refusals"][0]["url"] == base + "/unclassified"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
