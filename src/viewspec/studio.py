"""`viewspec studio`: a loopback preview that rebuilds when its source changes."""

from __future__ import annotations

import contextlib
import json
import mimetypes
import shutil
import signal
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlsplit

from viewspec.api import source_kind
from viewspec.app_bundle import compile_app
from viewspec.intent_tools import compile_intent_bundle_file_tool

STUDIO_DEFAULT_PORT = 4388
STUDIO_SOURCE_NAMES = ("viewspec.app.json", "viewspec.intent.json")
STUDIO_STATUS_PATH = "/__viewspec/status"
STUDIO_WATCH_SECONDS = 0.25

LIVE_RELOAD_SCRIPT = f"""<script>
(() => {{
  let seen = null;
  const banner = document.createElement("pre");
  banner.style.cssText = "position:fixed;inset:auto 0 0 0;z-index:2147483647;margin:0;padding:12px 16px;"
    + "max-height:40vh;overflow:auto;white-space:pre-wrap;background:#7f1d1d;color:#fff;font:13px/1.5 monospace";
  setInterval(async () => {{
    const status = await fetch("{STUDIO_STATUS_PATH}").then((response) => response.json()).catch(() => null);
    if (status === null) return;
    if (seen !== null && status.generation !== seen) location.reload();
    seen = status.generation;
    banner.textContent = status.errors.join("\\n");
    if (status.errors.length > 0) document.body.append(banner);
    else banner.remove();
  }}, 500);
}})();
</script>"""
EMPTY_PAGE = "<!doctype html><title>ViewSpec Studio</title><body></body>"


class StudioError(ValueError):
    """Stable-code failure before the preview can start."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix


def resolve_studio_source(source: str | Path | None, *, cwd: str | Path | None = None) -> Path:
    """Resolve an explicit source, or the only canonical source in the workspace."""
    if source is not None:
        return Path(source)
    root = Path(cwd) if cwd is not None else Path.cwd()
    candidates = [root / name for name in STUDIO_SOURCE_NAMES if (root / name).is_file()]
    if not candidates:
        raise StudioError(
            "STUDIO_SOURCE_NOT_FOUND",
            "ViewSpec Studio could not find viewspec.app.json or viewspec.intent.json.",
            "Run viewspec init, or name a source: viewspec studio SOURCE.",
        )
    if len(candidates) > 1:
        raise StudioError(
            "STUDIO_SOURCE_AMBIGUOUS",
            "ViewSpec Studio found both viewspec.app.json and viewspec.intent.json.",
            "Name the one to preview: viewspec studio SOURCE.",
        )
    return candidates[0]


class Studio:
    """A preview server bound to one source. Use it as a context manager, then call ``serve``."""

    def __init__(
        self,
        source: str | Path | None = None,
        *,
        cwd: str | Path | None = None,
        design: str | Path | None = None,
        port: int = STUDIO_DEFAULT_PORT,
    ) -> None:
        self.source = resolve_studio_source(source, cwd=cwd).resolve()
        self.design = Path(design).resolve() if design is not None else None
        self.generation = 0
        self.errors: list[str] = []
        self.build_dir: Path | None = None
        self._state = Path(tempfile.mkdtemp(prefix="viewspec-studio-"))
        self._stop = threading.Event()
        self._serving = False
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(self))
        except OSError as exc:
            shutil.rmtree(self._state, ignore_errors=True)
            raise StudioError(
                "STUDIO_PORT_UNAVAILABLE",
                f"ViewSpec Studio could not listen on 127.0.0.1:{port}: {exc}",
                "Stop the other server, or pick another port with --port.",
            ) from exc

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def __enter__(self) -> Studio:
        self.rebuild()
        self._watcher.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def serve(self) -> None:
        self._serving = True
        self._server.serve_forever()

    def start(self) -> None:
        """Build, then serve from a background thread until ``close``, for a caller that cannot block."""
        self.__enter__()
        self._serving = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        # shutdown() waits for serve_forever() to finish, so it hangs if the server never ran.
        if self._serving:
            self._server.shutdown()
        self._server.server_close()
        # An in-flight rebuild would write into the directory removed below.
        if self._watcher.is_alive():
            self._watcher.join()
        shutil.rmtree(self._state, ignore_errors=True)

    def rebuild(self) -> None:
        """Compile the source; a failed build keeps the last good one on screen with its errors."""
        output = self._state / f"build-{self.generation + 1}"
        errors = _compile_preview(self.source, output, self.design)
        previous = output if errors else self.build_dir
        if not errors:
            self.build_dir = output
        self.errors = errors
        self.generation += 1
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)

    def _watch(self) -> None:
        stamp = self._stamp()
        while not self._stop.wait(STUDIO_WATCH_SECONDS):
            current = self._stamp()
            if current != stamp:
                stamp = current
                self.rebuild()

    def _stamp(self) -> tuple[tuple[int, int] | None, ...]:
        return tuple(_file_stamp(path) for path in (self.source, self.design) if path is not None)


def open_studio(
    source: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    design: str | Path | None = None,
    port: int = STUDIO_DEFAULT_PORT,
    no_open: bool = False,
    on_ready: Any = None,
) -> None:
    """Serve the preview until interrupted. ``on_ready`` receives the running ``Studio``."""
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    with Studio(source, cwd=cwd, design=design, port=port) as studio:
        if on_ready is not None:
            on_ready(studio)
        if not no_open:
            webbrowser.open(studio.url)
        with contextlib.suppress(KeyboardInterrupt):
            studio.serve()


def _compile_preview(source: Path, output: Path, design: Path | None) -> list[str]:
    """Build one previewable static artifact and return its errors, never raising to the watcher."""
    try:
        if source_kind(source) == "app_bundle":
            report = compile_app(source, out_dir=output, design_path=design)
        else:
            report = compile_intent_bundle_file_tool(source, output, design_path=design, allow_outside_cwd=True)
    except Exception as exc:
        return [f"{type(exc).__name__}: {exc}"]
    if report["ok"]:
        return []
    return [f"{error['code']}: {error['message']}" for error in report["errors"]] or ["Compilation failed."]


def _file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _resolve_inside(root: Path, request_path: str) -> Path | None:
    """Map a URL path to a regular file inside ``root``; anything else is not found."""
    root = root.resolve()
    candidate = (root / unquote(urlsplit(request_path).path).lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate if candidate.is_file() else None


def _with_live_reload(html: str) -> str:
    head, marker, tail = html.rpartition("</body>")
    return f"{head}{LIVE_RELOAD_SCRIPT}{marker}{tail}" if marker else f"{html}{LIVE_RELOAD_SCRIPT}"


def _handler_for(studio: Studio) -> type[BaseHTTPRequestHandler]:
    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "ViewSpecStudio"

        def do_GET(self) -> None:
            # The preview is loopback-only; refusing other Host values also stops DNS rebinding.
            if self.headers.get("Host") not in {f"127.0.0.1:{studio.port}", f"localhost:{studio.port}"}:
                self._send(HTTPStatus.FORBIDDEN, b"Forbidden.", "text/plain; charset=utf-8")
                return
            route = urlsplit(self.path).path
            if route == STUDIO_STATUS_PATH:
                status = {"generation": studio.generation, "errors": studio.errors}
                self._send(HTTPStatus.OK, json.dumps(status).encode("utf-8"), "application/json")
                return
            try:
                self._send_artifact(route, studio.build_dir)
            except OSError:
                # A rebuild replaced this build mid-request; the page's next poll reloads it.
                self._send(HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send_artifact(self, route: str, build_dir: Path | None) -> None:
            if route == "/":
                html = (build_dir / "index.html").read_text(encoding="utf-8") if build_dir else EMPTY_PAGE
                self._send(HTTPStatus.OK, _with_live_reload(html).encode("utf-8"), "text/html; charset=utf-8")
                return
            target = _resolve_inside(build_dir, self.path) if build_dir else None
            if target is None:
                self._send(HTTPStatus.NOT_FOUND, b"Not found.", "text/plain; charset=utf-8")
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(HTTPStatus.OK, target.read_bytes(), content_type)

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    return StudioHandler


__all__ = [
    "STUDIO_DEFAULT_PORT",
    "STUDIO_SOURCE_NAMES",
    "Studio",
    "StudioError",
    "open_studio",
    "resolve_studio_source",
]
