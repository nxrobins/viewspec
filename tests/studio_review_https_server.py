"""Test-only HTTPS bridge for the framework-neutral Studio review adapter."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl

from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_runtime import ReviewRuntime
from viewspec.studio_review_http import ReviewHTTPRequest, StudioReviewHTTPAdapter
from viewspec.studio_review_service import StudioReviewService
from viewspec.studio_share import prepare_studio_share


def _verifier(_package: Path, envelope: dict[str, object]) -> dict[str, object]:
    revision = envelope["revision"]
    totals = envelope["totals"]
    assert isinstance(revision, dict) and isinstance(totals, dict)
    return {
        "schema_version": 1,
        "status": "passed",
        "verifier_id": "studio-review-browser-sandbox-v1",
        "package_id": envelope["package_id"],
        "source_sha256": revision["source_sha256"],
        "artifact_set_sha256": revision["artifact_set_sha256"],
        "root_manifest_sha256": revision["root_manifest_sha256"],
        "inspection_sha256": revision["inspection_sha256"],
        "target_artifact_sets": revision["target_artifact_sets"],
        "rebuild": {
            "evidence_sha256": "b" * 64,
            "expected_inventory_sha256": revision["artifact_set_sha256"],
            "observed_inventory_sha256": revision["artifact_set_sha256"],
            "source_only_request": True,
            "install_used": False,
            "lifecycle_hooks_disabled": True,
            "uploaded_artifacts_executed": False,
        },
        "sandbox": {
            "network": "denied",
            "lifecycle_hooks": "disabled",
            "arbitrary_commands": "disabled",
            "limits": {
                "cpu_seconds": 20,
                "memory_bytes": 256 * 1024 * 1024,
                "wall_seconds": 90,
                "file_count": totals["file_count"],
                "byte_count": totals["bytes"],
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--service-root", required=True)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--port", required=True, type=int)
    values = parser.parse_args()
    source = Path(values.source).resolve()
    state_root = Path(values.state_root).resolve()
    ReviewRuntime.open(source, state_root=state_root, target=STUDIO_COMPARE_TARGET, allow_install=True)
    prepared = prepare_studio_share(source, state_root=state_root, cwd=source.parent)
    archive = Path(str(prepared["paths"]["upload_archive"])).resolve()
    origin = f"https://127.0.0.1:{values.port}"
    service = StudioReviewService(
        values.service_root,
        signing_key=b"test-only-hosted-review-browser-signing-key",
        verifier=_verifier,
    )
    adapter = StudioReviewHTTPAdapter(
        service,
        public_origin=origin,
        authorize_upload=lambda headers: headers.get("authorization") == "Bearer e2e-upload",
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

        def _handle(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            body = self.rfile.read(length) if 0 < length <= 64 * 1024 * 1024 else b""
            response = adapter.handle(
                ReviewHTTPRequest(
                    method=self.command,
                    path=self.path,
                    headers=dict(self.headers.items()),
                    body=body,
                    scheme="https",
                )
            )
            self.send_response(response.status)
            for name, value in response.headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", values.port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(values.certificate, values.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(json.dumps({"status": "ready", "origin": origin, "archive": str(archive)}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
