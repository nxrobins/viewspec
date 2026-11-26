"""Own the shared relay and read its complete, binary-safe attempt journal.

All arms should navigate ``origin``. Configure upstream apps with that public origin when they
emit absolute URLs; the relay never rewrites page content or silently widens destination scope.
"""

from __future__ import annotations

import base64
import hashlib
import json
import selectors
import subprocess
from pathlib import Path


class BenchmarkProxy:
    def __init__(
        self,
        upstream: str,
        directory: Path,
        *,
        port: int = 0,
        origin: str | None = None,
        cookie: str = "",
        methods: tuple[str, ...] = ("GET", "HEAD"),
        max_bytes: int = 64 * 1024 * 1024,
    ):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = dict(
            upstream=upstream,
            directory=str(directory.resolve()),
            port=port,
            cookie=cookie,
            methods=list(methods),
            maxBytes=max_bytes,
        )
        if origin is not None:
            config["origin"] = origin
        config_path = directory / "config.json"
        with config_path.open("x") as file:
            json.dump(config, file)
        config_path.chmod(0o600)
        self.stderr = (directory / "stderr.log").open("x")
        (directory / "stderr.log").chmod(0o600)
        self.process = subprocess.Popen(
            ["node", str(Path(__file__).with_suffix(".js")), str(config_path)],
            stdout=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            stderr=self.stderr,
            text=True,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                if not selector.select(10):
                    raise RuntimeError("benchmark proxy startup timed out")
            hello = json.loads(self.process.stdout.readline())
            self.origin, self.proxy = hello["origin"], hello["proxy"]
        except BaseException:
            self.shutdown()
            raise

    def shutdown(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        self.stderr.close()
        if self.process.stdout:
            self.process.stdout.close()

    def server_close(self):
        self.shutdown()

    def journal(self, *, complete: bool = True) -> list[dict]:
        entries = [json.loads(line) for line in (self.directory / "events.jsonl").read_text().splitlines()]
        if complete and (
            self.process.poll() != 0
            or not entries
            or entries[-1].get("event") != "audit_end"
            or not entries[-1].get("valid")
        ):
            raise RuntimeError("benchmark transport audit is incomplete or invalid; do not score this attempt")
        records: dict[int, dict] = {}
        for entry in entries:
            key, event = entry.get("id"), entry["event"]
            if event == "request":
                records[key] = {
                    "id": key,
                    "method": entry["method"],
                    "path": entry["path"],
                    "content_type": entry.get("content_type", ""),
                    "started_at": entry["at"],
                    "response_complete": False,
                    "body_complete": False,
                    "body_base64": "",
                    "body": "",
                }
            elif key in records:
                row = records[key]
                if event == "body":
                    file = entry["file"]
                    if Path(file).name != file:
                        raise RuntimeError("invalid benchmark journal body path")
                    body = (self.directory / file).read_bytes()
                    if len(body) != entry["bytes"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
                        raise RuntimeError("benchmark journal body integrity failed; do not score this attempt")
                    if entry["direction"] == "request":
                        row.update(
                            body=body.decode(errors="replace"),
                            body_base64=base64.b64encode(body).decode(),
                            body_complete=entry["complete"],
                        )
                    else:
                        row.setdefault("websocket_wire", []).append(
                            {k: entry[k] for k in ("direction", "file", "bytes", "sha256", "complete")}
                        )
                elif event == "headers":
                    row["status"] = entry["status"]
                    row["response_content_type"] = entry.get("content_type", "")
                elif event == "response_end":
                    row.update(response_complete=True, finished_at=entry["at"], seconds=entry["at"] - row["started_at"])
                elif event in {
                    "upstream_error",
                    "response_aborted",
                    "client_closed",
                    "upgrade_refused",
                    "socket_closed",
                }:
                    row.setdefault("events", []).append(event)
        return list(records.values())
