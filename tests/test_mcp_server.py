"""`viewspec mcp`: five tools over stdio, confined to one workspace."""

from __future__ import annotations

import contextlib
import errno
import importlib
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

import viewspec
import viewspec.mcp_server as mcp_server
from viewspec.cli import main as cli_main
from viewspec.error_codes import ERROR_CODES
from viewspec.mcp_server import ViewSpecTools

ROOT = Path(__file__).resolve().parents[1]
TOOL_NAMES = {"init", "compile", "check", "prove", "studio"}


@pytest.fixture
def tools(tmp_path) -> Iterator[ViewSpecTools]:
    server = ViewSpecTools(tmp_path)
    yield server
    server.close()


def _assert_failed_with(report: dict, code: str) -> None:
    assert report["ok"] is False
    assert [error["code"] for error in report["errors"]] == [code]
    assert code in ERROR_CODES
    assert report["errors"][0]["fix"]


# init, compile, check, prove ------------------------------------------------------------------


def test_the_intent_loop_runs_through_the_tools(tools, tmp_path) -> None:
    initialized = tools.init()
    assert initialized["ok"] is True
    assert initialized["paths"]["intent"] == str(tmp_path / "viewspec.intent.json")

    assert tools.check("viewspec.intent.json")["ok"] is True

    compiled = tools.compile("viewspec.intent.json", "dist")
    assert compiled["ok"] is True
    assert (tmp_path / "dist" / "index.html").is_file()

    assert tools.check("dist")["ok"] is True

    proved = tools.prove("viewspec.intent.json")
    assert proved["ok"] is True
    assert (tmp_path / ".viewspec-proof" / "PROOF.md").is_file()


def test_the_app_loop_runs_through_the_tools(tools, tmp_path) -> None:
    assert tools.init(what="app")["ok"] is True
    assert tools.check("viewspec.app.json")["ok"] is True

    compiled = tools.compile("viewspec.app.json", "app-dist")
    assert compiled["ok"] is True
    assert compiled["target"] == "html-tailwind-app"
    assert tools.check("app-dist")["ok"] is True

    proved = tools.prove("viewspec.app.json", with_shell=True)
    assert proved["ok"] is True
    assert Path(proved["paths"]["proof_summary"]).is_file()


def test_init_writes_a_design_file_and_refuses_to_overwrite_without_force(tools, tmp_path) -> None:
    assert tools.init(what="design")["ok"] is True
    (tmp_path / "DESIGN.md").write_text("kept\n", encoding="utf-8")

    refused = tools.init(what="design")

    _assert_failed_with(refused, "IO_ERROR")
    assert (tmp_path / "DESIGN.md").read_text(encoding="utf-8") == "kept\n"
    assert tools.init(what="design", force=True)["ok"] is True


def test_check_reports_a_correction_prompt_for_an_invalid_source(tools, tmp_path) -> None:
    (tmp_path / "broken.intent.json").write_text('{"substrate": {}}', encoding="utf-8")

    report = tools.check("broken.intent.json")

    assert report["ok"] is False
    assert report["correction_prompt"]


def test_compile_reports_an_unknown_target_instead_of_raising(tools) -> None:
    tools.init()

    report = tools.compile("viewspec.intent.json", "dist", target="swiftui")

    assert report["ok"] is False
    assert report["errors"][0]["code"] in ERROR_CODES


@pytest.mark.parametrize("option", ["with_shell", "freerange", "pretext"])
def test_prove_refuses_app_only_evidence_for_an_intent_source(tools, tmp_path, option) -> None:
    # Ignoring the flag would hand the agent a proof without the evidence it asked for.
    tools.init()

    report = tools.prove("viewspec.intent.json", **{option: True})

    _assert_failed_with(report, "PROVE_INVALID_REQUEST")
    assert not (tmp_path / ".viewspec-proof").exists()


def test_prove_writes_defaults_into_the_workspace_not_the_process_cwd(tools, tmp_path, monkeypatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    workspace = tmp_path / "workspace"
    elsewhere.mkdir()
    workspace.mkdir()
    monkeypatch.chdir(elsewhere)

    assert ViewSpecTools(workspace).prove()["ok"] is True

    assert (workspace / ".viewspec-proof" / "PROOF.md").is_file()
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize(
    ("what", "source", "code"),
    [
        ("intent", "viewspec.intent.json", "PROVE_OUTPUT_UNSAFE"),
        ("app", "viewspec.app.json", "APP_PROOF_OUTPUT_UNSAFE"),
    ],
)
def test_force_cannot_replace_the_workspace_from_another_process_cwd(tmp_path, monkeypatch, what, source, code) -> None:
    elsewhere = tmp_path / "elsewhere"
    workspace = tmp_path / "workspace"
    elsewhere.mkdir()
    workspace.mkdir()
    monkeypatch.chdir(elsewhere)
    server = ViewSpecTools(workspace)
    server.init(what=what)
    (workspace / "proof_report.json").write_text("{}", encoding="utf-8")
    (workspace / "notes.txt").write_text("keep", encoding="utf-8")

    _assert_failed_with(server.prove(source=source, out=".", force=True), code)

    assert (workspace / "notes.txt").is_file()
    assert (workspace / source).is_file()


def test_force_keeps_an_output_directory_that_holds_an_input(tools, tmp_path) -> None:
    held = tmp_path / "sub"
    held.mkdir()
    tools.init(what="app", out="sub/viewspec.app.json")
    tools.init(what="intent", out="sub/viewspec.intent.json")
    tools.init(what="design", out="sub/DESIGN.md")
    tools.init(what="intent")
    (held / "shell_manifest.json").write_text("{}", encoding="utf-8")
    before = sorted(path.name for path in held.iterdir())

    _assert_failed_with(tools.compile("sub/viewspec.app.json", "sub", force=True), "APP_SHELL_OUTPUT_PATH_UNSAFE")
    _assert_failed_with(tools.prove("sub/viewspec.app.json", out="sub", force=True), "APP_PROOF_OUTPUT_UNSAFE")
    _assert_failed_with(tools.prove("sub/viewspec.intent.json", out="sub", force=True), "PROVE_OUTPUT_UNSAFE")
    _assert_failed_with(
        tools.prove("viewspec.intent.json", out="sub", design="sub/DESIGN.md", force=True), "PROVE_OUTPUT_UNSAFE"
    )

    assert sorted(path.name for path in held.iterdir()) == before


def test_compile_failures_name_a_tool_that_exists(tools, tmp_path) -> None:
    (tmp_path / "viewspec.intent.json").write_text('{"substrate": {}}', encoding="utf-8")

    report = tools.compile("viewspec.intent.json", "dist")

    assert report["ok"] is False
    assert "compile_intent_bundle_file" not in json.dumps(report)


def test_a_plain_server_failure_names_its_cause(monkeypatch, capsys) -> None:
    def fail(**_: object) -> None:
        raise ExceptionGroup("unhandled errors in a TaskGroup", [BrokenPipeError("the client went away")])

    monkeypatch.setattr("viewspec.cli.run_mcp_server", fail)

    assert cli_main(["mcp"]) == 1
    assert "the client went away" in capsys.readouterr().err


def test_viewspec_does_not_export_the_server() -> None:
    assert "ViewSpecTools" not in viewspec.__all__
    assert "run_mcp_server" not in viewspec.__all__


# The path sandbox ----------------------------------------------------------------------------


@pytest.fixture
def outside(tmp_path) -> Path:
    path = tmp_path / "outside"
    path.mkdir()
    (path / "viewspec.intent.json").write_text("{}", encoding="utf-8")
    return path


@pytest.fixture
def jailed(tmp_path) -> Iterator[ViewSpecTools]:
    root = tmp_path / "workspace"
    root.mkdir()
    server = ViewSpecTools(root)
    server.init()
    yield server
    server.close()


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda tools, escape: tools.init(out=f"{escape}/new.intent.json"), id="init_out"),
        pytest.param(
            lambda tools, escape: tools.compile(f"{escape}/viewspec.intent.json", "dist"), id="compile_source"
        ),
        pytest.param(lambda tools, escape: tools.compile("viewspec.intent.json", escape), id="compile_out"),
        pytest.param(
            lambda tools, escape: tools.compile("viewspec.intent.json", "d", design=f"{escape}/D.md"), id="design"
        ),
        pytest.param(lambda tools, escape: tools.check(f"{escape}/viewspec.intent.json"), id="check"),
        pytest.param(lambda tools, escape: tools.prove(f"{escape}/viewspec.intent.json"), id="prove_source"),
        pytest.param(lambda tools, escape: tools.prove(out=escape), id="prove_out"),
        pytest.param(lambda tools, escape: tools.studio(f"{escape}/viewspec.intent.json"), id="studio"),
    ],
)
@pytest.mark.parametrize("form", ["relative", "absolute", "symlink"])
def test_every_path_argument_stays_inside_the_workspace(jailed, outside, call, form) -> None:
    (outside / "D.md").write_text("", encoding="utf-8")
    before = sorted(path.name for path in outside.iterdir())
    if form == "relative":
        escape = "../outside"
    elif form == "absolute":
        escape = str(outside)
    else:
        (jailed.root / "link").symlink_to(outside, target_is_directory=True)
        escape = "link"

    _assert_failed_with(call(jailed, escape), "PATH_OUTSIDE_CWD")

    assert sorted(path.name for path in outside.iterdir()) == before


def test_a_url_is_not_a_path(jailed) -> None:
    _assert_failed_with(jailed.check("file:///etc/passwd"), "INVALID_PATH")


def test_allow_outside_cwd_opens_the_sandbox(jailed, outside, tmp_path) -> None:
    server = ViewSpecTools(jailed.root, allow_outside_cwd=True)

    report = server.check(str(outside / "viewspec.intent.json"))

    # The file was read: its contract issues are reported, not the sandbox.
    assert report["issues"]
    assert "PATH_OUTSIDE_CWD" not in {issue["code"] for issue in report["issues"]}


def test_a_missing_workspace_fails_at_startup(tmp_path) -> None:
    with pytest.raises(ValueError, match="Invalid cwd"):
        ViewSpecTools(tmp_path / "missing")


# studio --------------------------------------------------------------------------------------


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def test_studio_returns_at_once_with_a_url_that_serves_the_preview(tools) -> None:
    tools.init()

    started = tools.studio()

    assert started["ok"] is True
    assert started["url"].startswith("http://127.0.0.1:")
    assert started["build_errors"] == []
    assert "/__viewspec/status" in _get(started["url"])


def test_studio_runs_one_preview_at_a_time(tools) -> None:
    tools.init()
    first = tools.studio()["url"]

    second = tools.studio()["url"]

    assert second != first
    assert "/__viewspec/status" in _get(second)
    with pytest.raises(OSError):
        _get(first)


def test_a_bad_studio_request_leaves_the_running_preview_alone(tools) -> None:
    tools.init()
    url = tools.studio()["url"]

    _assert_failed_with(tools.studio("missing.intent.json"), "INVALID_PATH")
    _assert_failed_with(tools.studio(design="missing.md"), "INVALID_PATH")

    assert "/__viewspec/status" in _get(url)


@pytest.mark.parametrize("port", [-1, 70000])
def test_an_out_of_range_port_leaves_the_running_preview_alone(tools, port) -> None:
    tools.init()
    url = tools.studio()["url"]

    _assert_failed_with(tools.studio(port=port), "STUDIO_PORT_UNAVAILABLE")

    assert "/__viewspec/status" in _get(url)


def test_a_taken_port_leaves_the_running_preview_alone(tools) -> None:
    tools.init()
    url = tools.studio()["url"]

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        _assert_failed_with(tools.studio(port=taken.getsockname()[1]), "STUDIO_PORT_UNAVAILABLE")

    assert "/__viewspec/status" in _get(url)


def test_studio_can_restart_on_its_own_port(tools) -> None:
    tools.init()
    url = tools.studio()["url"]
    port = int(url.rstrip("/").rsplit(":", 1)[1])

    assert tools.studio(port=port)["url"] == url
    assert "/__viewspec/status" in _get(url)


def test_the_default_studio_source_stays_inside_the_workspace(jailed, outside) -> None:
    source = jailed.root / "viewspec.intent.json"
    source.unlink()
    source.symlink_to(outside / "viewspec.intent.json")

    _assert_failed_with(jailed.studio(), "PATH_OUTSIDE_CWD")


def test_studio_stop_ends_the_preview_and_is_safe_to_repeat(tools) -> None:
    tools.init()
    url = tools.studio()["url"]

    assert tools.studio(stop=True)["ok"] is True
    assert tools.studio(stop=True)["ok"] is True

    with pytest.raises(OSError):
        _get(url)


def test_studio_reports_a_missing_source_with_its_own_code(tools) -> None:
    _assert_failed_with(tools.studio(), "STUDIO_SOURCE_NOT_FOUND")


def test_studio_reports_a_broken_first_build_and_keeps_serving(tools, tmp_path) -> None:
    (tmp_path / "viewspec.intent.json").write_text("{", encoding="utf-8")

    started = tools.studio()

    assert started["ok"] is True
    assert started["build_errors"]
    assert "ViewSpec Studio" in _get(started["url"])


def test_closing_right_after_start_cannot_hang(tools) -> None:
    tools.init()
    finished = threading.Event()

    def start_and_close() -> None:
        for _ in range(5):
            tools.studio()
            tools.close()
        finished.set()

    threading.Thread(target=start_and_close, daemon=True).start()

    assert finished.wait(timeout=60)


# The SDK dependency --------------------------------------------------------------------------


@pytest.fixture
def isolated_mcp_modules() -> Iterator[None]:
    """Let a test import stand-in ``mcp`` packages, then restore whatever was imported before."""

    def owned() -> list[str]:
        return [name for name in sys.modules if name == "mcp" or name.startswith("mcp.")]

    saved = {name: sys.modules[name] for name in owned()}
    for name in saved:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in owned():
            del sys.modules[name]
        sys.modules.update(saved)
        importlib.invalidate_caches()


# What mcp.server.fastmcp contains in each stand-in SDK; None leaves the module out.
_FASTMCP_V1 = "class FastMCP:\n    pass\n"
# MCP SDK 2.x keeps a mcp/server/fastmcp.py whose import fails with a migration hint.
_FASTMCP_V2_STUB = "raise ModuleNotFoundError('This is mcp 2.x, where FastMCP was renamed', name=__name__)\n"
# A broken or partly upgraded install fails with something other than ImportError.
_FASTMCP_RUNTIME_ERROR = "raise RuntimeError('pydantic could not build the FastMCP settings model')\n"
_FASTMCP_SYNTAX_ERROR = "class FastMCP(\n"


def _write_stub_mcp(root: Path, fastmcp_source: str | None) -> None:
    server = root / "mcp" / "server"
    server.mkdir(parents=True)
    (root / "mcp" / "__init__.py").write_text("", encoding="utf-8")
    (server / "__init__.py").write_text("", encoding="utf-8")
    if fastmcp_source is not None:
        (server / "fastmcp.py").write_text(fastmcp_source, encoding="utf-8")


@pytest.mark.parametrize(
    "fastmcp_source",
    [_FASTMCP_V2_STUB, None, _FASTMCP_RUNTIME_ERROR, _FASTMCP_SYNTAX_ERROR],
    ids=["mcp2_stub", "module_absent", "runtime_error", "syntax_error"],
)
def test_dependency_check_requires_the_module_the_server_imports(
    monkeypatch, tmp_path, isolated_mcp_modules, fastmcp_source
) -> None:
    _write_stub_mcp(tmp_path / "sdk", fastmcp_source)
    monkeypatch.syspath_prepend(str(tmp_path / "sdk"))

    assert mcp_server.mcp_dependency_available() is False


def test_dependency_check_finds_the_server_module(monkeypatch, tmp_path, isolated_mcp_modules) -> None:
    _write_stub_mcp(tmp_path / "sdk", _FASTMCP_V1)
    monkeypatch.syspath_prepend(str(tmp_path / "sdk"))

    assert mcp_server.mcp_dependency_available() is True


def test_dependency_check_reports_a_missing_sdk_without_raising(isolated_mcp_modules) -> None:
    sys.modules["mcp"] = None  # type: ignore[assignment]  # makes ``import mcp`` fail

    assert mcp_server.mcp_dependency_available() is False


def test_agents_extra_and_ci_pins_stay_on_the_mcp_1_api() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    (requirement,) = [dep for dep in pyproject["project"]["optional-dependencies"]["agents"] if dep.startswith("mcp")]
    assert requirement.replace(" ", "") == "mcp>=1.30,<2"
    pins = [
        line.split("==", 1)[1].split(";", 1)[0].strip()
        for line in (ROOT / "requirements-ci.txt").read_text(encoding="utf-8").splitlines()
        if line.startswith("mcp==")
    ]
    assert len(pins) == 1 and pins[0].split(".")[0] == "1", pins


def test_missing_sdk_prints_the_install_hint(monkeypatch, capsys) -> None:
    monkeypatch.setattr(mcp_server, "mcp_dependency_available", lambda: False)

    assert cli_main(["mcp"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "viewspec[agents]" in captured.err


def test_missing_sdk_under_json_keeps_stdout_for_the_protocol(monkeypatch, capsys) -> None:
    monkeypatch.setattr(mcp_server, "mcp_dependency_available", lambda: False)

    assert cli_main(["mcp", "--json"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["errors"][0]["code"] == "MCP_DEPENDENCY_MISSING"


# A real server over real stdio ----------------------------------------------------------------


class _StdioClient:
    """Speak newline-delimited JSON-RPC to ``python -m viewspec mcp``."""

    def __init__(self, workspace: Path, *extra_args: str) -> None:
        if not mcp_server.mcp_dependency_available():
            pytest.skip("the MCP SDK with mcp.server.fastmcp (viewspec[agents]) is not installed")
        self.stderr_path = workspace.parent / f"{workspace.name}.server.stderr"
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(workspace),
            "TMPDIR": str(workspace.parent),
        }
        with self.stderr_path.open("wb") as stderr:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "viewspec", "mcp", "--cwd", str(workspace), *extra_args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                cwd=workspace,
                env=env,
            )
        self._unread = b""
        self._next_id = 0

    def stderr(self) -> str:
        return self.stderr_path.read_text(errors="replace")

    def send(self, message: dict[str, object]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.process.stdin.flush()

    def _read_line(self) -> bytes:
        assert self.process.stdout is not None
        deadline = time.monotonic() + 120
        while b"\n" not in self._unread:
            ready, _, _ = select.select([self.process.stdout], [], [], max(deadline - time.monotonic(), 0))
            if not ready:
                pytest.fail(f"no response within 120s: {self.stderr()}")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                pytest.fail(f"the server closed stdout early: {self.stderr()}")
            self._unread += chunk
        line, _, self._unread = self._unread.partition(b"\n")
        return line

    def request(self, method: str, params: dict[str, object]) -> dict:
        self._next_id += 1
        self.send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
        while True:
            # Nothing but JSON-RPC may reach stdout, whatever the tool ran.
            message = json.loads(self._read_line())
            assert message.get("jsonrpc") == "2.0", message
            if message.get("id") == self._next_id:
                assert "error" not in message, message
                return message["result"]

    def initialize(self) -> dict:
        from mcp.types import LATEST_PROTOCOL_VERSION  # type: ignore[import-not-found]

        result = self.request(
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "viewspec-tests", "version": "0"},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def call(self, name: str, **arguments: object) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=30)
        with contextlib.suppress(BrokenPipeError, ValueError):
            assert self.process.stdin is not None
            self.process.stdin.close()


@pytest.fixture
def workspace(tmp_path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.mark.parametrize("extra_args", [(), ("--json",)], ids=["default", "json"])
def test_real_server_lists_exactly_the_five_tools(workspace, extra_args) -> None:
    client = _StdioClient(workspace, *extra_args)
    try:
        initialized = client.initialize()
        assert initialized["serverInfo"]["name"] == "viewspec"
        assert "init, edit the source, check, compile, then prove" in initialized["instructions"]

        tools = client.request("tools/list", {})["tools"]

        assert {tool["name"] for tool in tools} == TOOL_NAMES
        assert all(tool["description"] and tool["inputSchema"]["type"] == "object" for tool in tools)
        schemas = {tool["name"]: tool["inputSchema"] for tool in tools}
        assert "self" not in schemas["check"]["properties"]
        assert schemas["check"]["required"] == ["path"]
        assert schemas["compile"]["required"] == ["source", "out"]

        assert client.process.stdin is not None
        client.process.stdin.close()
        assert client.process.wait(timeout=30) == 0, client.stderr()
        if extra_args:
            # --json keeps the SDK's per-request INFO logs off stderr, which it reserves for the
            # one-line failure report.
            assert client.stderr() == ""
    finally:
        client.close()


def test_real_server_runs_the_tools_and_reports_the_sandbox(workspace, tmp_path) -> None:
    client = _StdioClient(workspace)
    try:
        client.initialize()

        assert client.call("init")["ok"] is True
        assert client.call("check", path="viewspec.intent.json")["ok"] is True
        assert client.call("compile", source="viewspec.intent.json", out="dist")["ok"] is True
        assert client.call("check", path="dist")["ok"] is True
        assert client.call("prove", source="viewspec.intent.json")["ok"] is True

        escaped = client.call("check", path="../server-escape.json")
        assert escaped["errors"][0]["code"] == "PATH_OUTSIDE_CWD"
    finally:
        client.close()


@pytest.mark.parametrize("how", ["stdin_closed", "sigterm", "sigint"])
def test_real_server_stops_promptly_and_leaves_no_preview_behind(workspace, tmp_path, how) -> None:
    client = _StdioClient(workspace)
    try:
        client.initialize()
        client.call("init")
        url = client.call("studio")["url"]
        assert "/__viewspec/status" in _get(url)
        assert list(tmp_path.glob("viewspec-studio-*"))

        if how == "stdin_closed":
            assert client.process.stdin is not None
            client.process.stdin.close()
            expected_exit = 0
        else:
            signum = signal.SIGTERM if how == "sigterm" else signal.SIGINT
            client.process.send_signal(signum)
            expected_exit = -signum

        assert client.process.wait(timeout=30) == expected_exit
        assert list(tmp_path.glob("viewspec-studio-*")) == []
        with pytest.raises(OSError):
            _get(url)
    finally:
        client.close()


def test_real_server_json_reports_a_lost_client_as_one_stderr_line(workspace) -> None:
    """A client that goes away breaks the server's stdout; ``mcp --json`` reports that as one line.

    The SDK wraps the BrokenPipeError in an exception group whose own text names nothing, logs each
    request at INFO, and on Python 3.14 the exit-time flush of the broken stdout adds its own
    reports and exit status 120. The envelope must be the only stderr line, name the real error and
    keep exit 1.
    """
    client = _StdioClient(workspace, "--json")
    try:
        client.initialize()
        assert client.process.stdout is not None
        # The client goes away: the server's next response hits a closed pipe.
        client.process.stdout.close()
        deadline = time.monotonic() + 60
        request_id = 100
        while client.process.poll() is None and time.monotonic() < deadline:
            try:
                client.send({"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}})
            except BrokenPipeError:
                break  # the server stopped and its stdin closed with it
            request_id += 1
            time.sleep(0.1)
        exit_code = client.process.wait(timeout=60)
    finally:
        client.close()

    stderr_text = client.stderr()
    assert exit_code == 1, stderr_text
    assert stderr_text.endswith("\n") and stderr_text.count("\n") == 1, stderr_text
    assert json.loads(stderr_text)["errors"] == [
        {
            "code": "MCP_SERVER_FAILED",
            "message": f"[Errno {errno.EPIPE}] {os.strerror(errno.EPIPE)}",
            "fix": "Fix the reported problem and restart viewspec mcp.",
        }
    ]
