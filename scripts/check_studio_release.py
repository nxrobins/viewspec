#!/usr/bin/env python3
"""Exercise the homepage's install and launch commands using an isolated release package."""

from __future__ import annotations

import argparse
import base64
import hashlib
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.request import HTTPCookieProcessor, ProxyHandler, build_opener
import venv


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="
)


class ReleaseCheckError(RuntimeError):
    """A customer-visible install or launch invariant failed."""


class HomepageCommands(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.commands: dict[str, str] = {}
        self._command: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id") == "copyCmd":
            self.commands["install"] = str(attributes.get("data-copy-text") or "")
        if tag == "code" and "data-release-command" in attributes:
            name = str(attributes["data-release-command"])
            if name in self.commands:
                raise ReleaseCheckError(f"duplicate homepage release command: {name}")
            self._command = name
            self.commands[name] = ""

    def handle_data(self, data: str) -> None:
        if self._command is not None:
            self.commands[self._command] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "code":
            self._command = None


def homepage_contract(root: Path = ROOT) -> tuple[str, dict[str, str]]:
    facts = json.loads((root / "demos/public-facts.json").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = facts["sdk_version"]
    if project["project"]["version"] != version:
        raise ReleaseCheckError("homepage and package versions differ")
    parser = HomepageCommands()
    parser.feed((root / "demos/index.html").read_text(encoding="utf-8"))
    expected = {
        "install": facts["installation"]["current_command"],
        "creation": facts["studio"]["creation_command"],
        "compare": facts["studio"]["compare_command"],
    }
    if parser.commands != expected:
        raise ReleaseCheckError("homepage install/launch commands differ from the public contract")
    if shlex.split(expected["install"]) != ["python", "-m", "pip", "install", "--pre", "viewspec"]:
        raise ReleaseCheckError("update the release check for the new homepage installation flow")
    for name in ("creation", "compare"):
        if shlex.split(expected[name])[:2] != ["viewspec", "studio"]:
            raise ReleaseCheckError(f"homepage {name} is not a Studio launch command")
    return version, expected


def run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 240) -> str:
    print(f"release check: {shlex.join(command)}", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseCheckError(f"command did not complete: {command[0]}: {error}") from error
    if result.returncode:
        browser_error = Path(env.get("RELEASE_BROWSER_ERROR", "/nonexistent"))
        browser_detail = browser_error.read_text(encoding="utf-8")[-4000:] if browser_error.is_file() else ""
        detail = re.sub(r"http://127\.0\.0\.1:\d+/\S+", "<private Studio URL>",
                        result.stdout[-4000:] + "\n" + result.stderr[-4000:] + "\n" + browser_detail)
        raise ReleaseCheckError(
            f"command failed ({result.returncode}): {shlex.join(command)}\n{detail}"
        )
    return result.stdout


def _environment(work: Path, binary_dir: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PIP_", "VIEWSPEC_")) and key not in {
               "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "NODE_PATH", "BROWSER",
           }}
    env.update({
        "PATH": str(binary_dir) + os.pathsep + env.get("PATH", ""),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_CACHE_DIR": "1",
        "PYTHONNOUSERSITE": "1",
        "VIEWSPEC_REVIEW_STATE_DIR": str(work / "review-state"),
        "VIEWSPEC_CONVERGE_STATE_DIR": str(work / "converge-state"),
        "npm_config_cache": str(work / "npm-cache"),
    })
    # Preserve browser-open behavior. Capture the creation entry for HTTP checks;
    # connect Chromium during comparison launch so its required handshake occurs.
    capture = work / "capture_browser.py"
    capture.write_text(
        "import json,os,pathlib,subprocess,sys,traceback\n"
        "p=pathlib.Path(os.environ['RELEASE_BROWSER_URL'])\n"
        "p.write_text(sys.argv[1],encoding='utf-8');p.chmod(0o600)\n"
        "if os.environ.get('RELEASE_BROWSER_MODE') == 'check':\n"
        " env=dict(os.environ,RELEASE_STUDIO_URL=sys.argv[1])\n"
        " try:\n"
        "  result=subprocess.run(['node',os.environ['RELEASE_BROWSER_SCRIPT'],os.environ['RELEASE_BROWSER_RUNTIME']],"
        "env=env,capture_output=True,text=True,timeout=8)\n"
        "  output=result.stdout if result.returncode==0 else json.dumps({'status':'failed'})\n"
        "  pathlib.Path(os.environ['RELEASE_BROWSER_RESULT']).write_text(output,encoding='utf-8')\n"
        "  pathlib.Path(os.environ['RELEASE_BROWSER_ERROR']).write_text(result.stderr,encoding='utf-8')\n"
        " except Exception:\n"
        "  pathlib.Path(os.environ['RELEASE_BROWSER_ERROR']).write_text(traceback.format_exc(),encoding='utf-8')\n"
        "  pathlib.Path(os.environ['RELEASE_BROWSER_RESULT']).write_text(json.dumps({'status':'failed'}),encoding='utf-8')\n"
        " raise SystemExit(0)\n",
        encoding="utf-8",
    )
    env["BROWSER"] = f"{binary_dir / 'python'} {capture} %s"
    env["RELEASE_BROWSER_URL"] = str(work / "browser-url.txt")
    env["RELEASE_BROWSER_SCRIPT"] = str(ROOT / "scripts/check_studio_release_browser.mjs")
    env["RELEASE_BROWSER_RESULT"] = str(work / "browser-result.json")
    env["RELEASE_BROWSER_ERROR"] = str(work / "browser-error.txt")
    return env


def _browser_url(env: dict[str, str]) -> str:
    capture = Path(env["RELEASE_BROWSER_URL"])
    if not capture.is_file():
        raise ReleaseCheckError("the advertised launch command did not request a browser")
    url = capture.read_text(encoding="utf-8")
    capture.unlink()
    if not url.startswith("http://127.0.0.1:"):
        raise ReleaseCheckError("Studio browser launch is not loopback-only")
    return url


def _http_browser():
    return build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))


def _get(browser, url: str) -> str:
    with browser.open(url, timeout=10) as response:
        if response.status != 200:
            raise ReleaseCheckError(f"Studio browser entry returned HTTP {response.status}")
        return response.read(4 * 1024 * 1024).decode("utf-8")


def _stop_servers(work: Path) -> None:
    # These records belong only to daemons launched in this disposable check.
    for metadata in (work / "review-state").rglob("server.json"):
        try:
            pid = json.loads(metadata.read_text(encoding="utf-8"))["pid"]
            if type(pid) is int and pid > 1:
                os.kill(pid, signal.SIGTERM)
        except (OSError, KeyError, ValueError):
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 4388)) != 0:
                return
        time.sleep(0.1)
    raise ReleaseCheckError("Studio did not release the homepage's default port during cleanup")


def check_release(*, dist: Path | None = None, published: bool = False) -> dict[str, object]:
    version, commands = homepage_contract()
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 4388))
        except OSError as error:
            raise ReleaseCheckError("default Studio port 4388 must be free for the exact homepage flow") from error
    with tempfile.TemporaryDirectory(prefix="viewspec-release-") as temporary:
        work = Path(temporary).resolve()
        venv.EnvBuilder(with_pip=True).create(work / "venv")
        binary_dir = work / "venv" / ("Scripts" if os.name == "nt" else "bin")
        env = _environment(work, binary_dir)
        artifact_hash = None
        if not published:
            if dist is None:
                raise ReleaseCheckError("supply --dist or --published")
            wheels = list(dist.resolve().glob("*.whl"))
            if len(wheels) != 1:
                raise ReleaseCheckError("candidate release must contain exactly one wheel")
            artifact_hash = hashlib.sha256(wheels[0].read_bytes()).hexdigest()
            wheelhouse = work / "wheelhouse"
            wheelhouse.mkdir()
            run(["python", "-m", "pip", "download", "--only-binary=:all:",
                 "--dest", str(wheelhouse), str(wheels[0])], cwd=work, env=env)
            env.update({"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(wheelhouse)})
        try:
            # Candidate mode exposes only the built wheel and its dependencies;
            # published mode uses pip's public index. The command is unchanged.
            run(shlex.split(commands["install"]), cwd=work, env=env)
            installed = json.loads(run(["python", "-c",
                "import importlib.metadata,json,sys,viewspec;"
                "print(json.dumps({'version':importlib.metadata.version('viewspec'),"
                "'runtime_version':viewspec.__version__,'module':viewspec.__file__,'prefix':sys.prefix}))"
            ], cwd=work, env=env))
            if installed["version"] != version or installed["runtime_version"] != version:
                raise ReleaseCheckError(
                    f"homepage advertises {version}, but its install command resolved {installed['version']}"
                )
            if not Path(installed["module"]).resolve().is_relative_to(Path(installed["prefix"]).resolve()):
                raise ReleaseCheckError("ViewSpec imported from outside the isolated installed environment")
            run(["viewspec", "studio", "--help"], cwd=work, env=env)
            exported = work / "agent-assets"
            run(["viewspec", "export-agent-assets", "--out", str(exported)], cwd=work, env=env)
            manifest = json.loads((ROOT / "demos/agent-assets.json").read_text(encoding="utf-8"))
            if json.loads((exported / "agent-assets.json").read_text(encoding="utf-8")) != manifest:
                raise ReleaseCheckError("installed agent assets differ from the website's contract")
            for entry in manifest["files"]:
                name = entry["path"]
                if (exported / name).read_bytes() != (ROOT / "demos" / name).read_bytes():
                    raise ReleaseCheckError(f"installed agent asset differs from the website: {name}")

            driver = work / "browser-driver"
            run(["npm", "install", "--prefix", str(driver), "--ignore-scripts", "--no-audit", "--no-fund",
                 "@playwright/test@1.60.0"], cwd=work, env=env)
            env["RELEASE_BROWSER_RUNTIME"] = str(driver)

            creation = work / "creation"
            creation.mkdir()
            (creation / "product-brief.md").write_text(
                "Build a Release Check Console with a queue and detail screen for incident triage.\n",
                encoding="utf-8",
            )
            (creation / "reference.png").write_bytes(REFERENCE_PNG)
            launched = json.loads(run(shlex.split(commands["creation"]), cwd=creation, env=env))
            if launched.get("ok") is not True or launched["studio"]["status"] != "creating":
                raise ReleaseCheckError("the advertised brief/reference command did not open a creation room")
            browser = _http_browser()
            room_url = _browser_url(env)
            if "ViewSpec Studio" not in _get(browser, room_url):
                raise ReleaseCheckError("creation browser entry did not serve Studio")
            seed = work / "candidate-author"
            seed.mkdir()
            run(["viewspec", "init-app", "--template", "react-app"], cwd=seed, env=env)
            candidate = json.loads((seed / "viewspec.app.json").read_text(encoding="utf-8"))
            candidate["app"]["title"] = "Release Check Console"
            for screen in candidate["screens"]:
                screen["title"] = "Release Check " + screen["id"].title()
            Path(launched["paths"]["candidate"]).write_text(json.dumps(candidate), encoding="utf-8")
            origin = room_url.split("/open/", 1)[0]
            deadline = time.monotonic() + 60
            while True:
                status = json.loads(_get(browser, origin + "/v1/status"))
                if status["handoff_ready"]:
                    break
                if status["stage"] in {"needs_fix", "open_failed"} or time.monotonic() >= deadline:
                    raise ReleaseCheckError(f"packaged creation handoff failed at {status['stage']}")
                time.sleep(0.2)
            if status["checks"] != {"candidate_validation": "passed", "artifact_check": "passed"}:
                raise ReleaseCheckError("creation handoff was not checked")
            if "<title>ViewSpec Studio</title>" not in _get(browser, origin + "/continue"):
                raise ReleaseCheckError("creation did not continue into the checked Studio product")
            source = creation / "viewspec.app.json"
            if not source.is_file():
                raise ReleaseCheckError("creation did not publish checked canonical source")
            run(["viewspec", "review-end", str(source), "--json"], cwd=creation, env=env)
            _stop_servers(work)

            comparison = work / "comparison"
            comparison.mkdir()
            shutil.copyfile(source, comparison / "viewspec.app.json")
            env["RELEASE_BROWSER_MODE"] = "check"
            run(shlex.split(commands["compare"]), cwd=comparison, env=env)
            _browser_url(env)
            checked = json.loads(run(["viewspec", "review-status", "viewspec.app.json", "--json"],
                                     cwd=comparison, env=env))["review"]
            if checked["target"] != "studio-static-react-compare" or checked["check_status"] != "passed":
                raise ReleaseCheckError("homepage comparison did not build both checked targets")
            rendered = json.loads(Path(env["RELEASE_BROWSER_RESULT"]).read_text(encoding="utf-8"))
            if rendered.get("status") != "passed":
                detail = re.sub(r"http://127\.0\.0\.1:\d+/\S+", "<private Studio URL>",
                                Path(env["RELEASE_BROWSER_ERROR"]).read_text(encoding="utf-8")[-4000:])
                raise ReleaseCheckError("the comparison did not complete its browser render check: " + detail)
            run(["viewspec", "review-end", "viewspec.app.json", "--json"], cwd=comparison, env=env)
            return {
                "status": "passed", "mode": "published" if published else "candidate",
                "sdk_version": version, "wheel_sha256": artifact_hash, "commands": commands,
                "isolated_install": True, "agent_assets": "matched", "creation": "checked_handoff",
                "comparison": "checked", "browser": rendered,
            }
        finally:
            _stop_servers(work)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dist", type=Path, help="Test the built wheel before publication using a private wheelhouse.")
    mode.add_argument("--published", action="store_true", help="Test the exact install command against public PyPI.")
    parser.add_argument("--out", type=Path, help="Retain a redacted machine-readable release result.")
    args = parser.parse_args()
    try:
        report = check_release(dist=args.dist, published=args.published)
    except (ReleaseCheckError, OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Studio release check failed: {error}\n")
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
