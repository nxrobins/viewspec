#!/usr/bin/env python3
"""Test-only attested Studio Share server for the real Chromium journey."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import threading

from viewspec.app_starters import starter_app_bundle
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_runtime import ReviewRuntime
from viewspec.review_server import ReviewServer


class BrowserSharePublisher:
    def __init__(self, events: Path) -> None:
        self.events = events

    def _record(self, kind: str) -> None:
        with self.events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind}, sort_keys=True) + "\n")

    def status(self) -> dict[str, object]:
        return {
            "status": "available",
            "review_origin": "https://review.viewspec.test",
            "deployment_sha256": "a" * 64,
            "run_id": "vsrcan_" + ("b" * 32),
            "report_sha256": "c" * 64,
            "expires_at_epoch_s": 2_100_000_000,
        }

    def prepare(self) -> dict[str, object]:
        self._record("prepare")
        return {
            "schema_version": 1,
            "status": "awaiting_confirmation",
            "package_id": "d" * 64,
            "revision": 1,
            "file_count": 9,
            "bytes": 8192,
            "disclosure": {
                "will_leave_machine": [
                    {"category": "exact semantic source", "file_count": 1, "bytes": 1024},
                    {"category": "checked static/React artifacts and manifests", "file_count": 8, "bytes": 7168},
                    {"category": "future remote review comments", "current_count": 0},
                ],
                "will_not_leave_machine": [
                    "absolute local paths",
                    "environment variables",
                    "existing local Review comments and journal",
                    "production data",
                ],
            },
            "expiry_options": [3600, 86400, 604800],
            "release": self.status(),
            "upload_performed": False,
        }

    def publish(self, *, package_id, disclosure_accepted, expires_in_seconds) -> dict[str, object]:
        if package_id != "d" * 64 or disclosure_accepted is not True or expires_in_seconds not in {
            3600,
            86400,
            604800,
        }:
            raise ValueError("invalid test-only Share confirmation")
        self._record("publish")
        session_id = "vsr_" + ("A" * 24)
        return {
            "schema_version": 1,
            "status": "active",
            "session_id": session_id,
            "package_id": package_id,
            "expires_at": 2_100_000_000,
            "owner_url": f"https://review.viewspec.test/review/{session_id}/#cap=vsc_{'o' * 24}",
            "reviewer_url": f"https://review.viewspec.test/review/{session_id}/#cap=vsc_{'r' * 24}",
            "review_origin": "https://review.viewspec.test",
            "deployment_sha256": "a" * 64,
            "upload_performed": True,
            "private": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    runtime = ReviewRuntime.open(
        source,
        state_root=root / "review-state",
        target=STUDIO_COMPARE_TARGET,
        allow_install=True,
    )
    events = root / "share-events.jsonl"
    server = ReviewServer(runtime, port=args.port, share_publisher=BrowserSharePublisher(events))
    stopped = threading.Event()

    def stop(_signum, _frame) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.start()
    print(
        json.dumps(
            {
                "url": server.bootstrap_url,
                "events": str(events),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        stopped.wait()
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
