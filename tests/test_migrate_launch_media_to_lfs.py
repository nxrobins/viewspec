"""The Git LFS move of the launch media (audit finding H-3).

``scripts/migrate_launch_media_to_lfs.sh`` runs here against a throwaway repository built from
copies of the files in ``demos/launch-assets/``; Git LFS needs no server for local commits. The
script must commit the media as canonical LFS pointers on a new branch, keep the working files
byte-identical, leave ``main``, the remote and existing Git hooks alone, never build on a leftover
branch that lacks commits of ``main``, and change nothing when it runs again. The Pages deploy
publishes those files, so its checkout must fetch the LFS content.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_launch_media_to_lfs.sh"
WORKFLOWS = ROOT / ".github" / "workflows"
MEDIA_DIR = "demos/launch-assets"
BRANCH = "lfs/launch-media"
POINTER_VERSION = b"version https://git-lfs.github.com/spec/v1\n"
EXTENSIONS = ("gif", "mp4", "png")
# Printed with the push steps when no pre-push hook uploads LFS objects.
NO_LFS_HOOK_NOTE = "no pre-push hook runs git lfs pre-push here"
# The hooks that the Git LFS filter adds where no hook of that name exists.
LFS_HOOKS = ("pre-push", "post-checkout", "post-commit", "post-merge")
# Printed before the filter runs when core.hooksPath is set.
HOOKS_PATH_NOTE = "Note: core.hooksPath is set in"
SH = shutil.which("sh")
GIT = shutil.which("git")


def _launch_assets() -> list[str]:
    """The tracked files under demos/launch-assets/, as repository-relative paths."""
    if GIT is None:
        return []
    listed = subprocess.run(
        [GIT, "ls-files", "-z", "--", MEDIA_DIR], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return sorted(name for name in listed.stdout.split("\0") if name) if listed.returncode == 0 else []


def _skip_reason() -> str | None:
    if SH is None or GIT is None:
        return "the LFS migration test needs sh and git on PATH"
    if subprocess.run([GIT, "lfs", "version"], capture_output=True, check=False).returncode != 0:
        return "git-lfs is not installed (https://git-lfs.com); the LFS migration test needs it"
    assets = _launch_assets()
    if not assets:
        return f"{MEDIA_DIR}/ is not tracked in a git checkout here"
    pointers = [name for name in assets if (ROOT / name).read_bytes().startswith(POINTER_VERSION)]
    if pointers:
        return (
            f"this checkout holds Git LFS pointers instead of the launch media ({len(pointers)} files); "
            "run 'git lfs pull' to test the migration against the real files"
        )
    return None


_SKIP_REASON = _skip_reason()
needs_git_lfs = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


def _canonical_pointer(data: bytes) -> bytes:
    """The pointer git-lfs writes for ``data`` (Git LFS specification v1)."""
    return POINTER_VERSION + f"oid sha256:{hashlib.sha256(data).hexdigest()}\nsize {len(data)}\n".encode()


@dataclass
class Checkout:
    """A scratch clone of the relevant part of the repository, with ``main`` at ``base``."""

    path: Path
    origin: Path
    env: dict[str, str]
    base: str
    originals: dict[str, bytes]

    def git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            [GIT or "git", *args], cwd=self.path, env=self.env, capture_output=True, text=True, check=False
        )
        if check:
            assert result.returncode == 0, (args, result.stderr)
        return result.stdout

    def blob(self, name: str) -> bytes:
        return subprocess.run(
            [GIT or "git", "cat-file", "blob", name], cwd=self.path, env=self.env, capture_output=True, check=True
        ).stdout

    def migrate(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [SH or "sh", str(self.path / "scripts" / SCRIPT.name), *args],
            cwd=self.path,
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    @property
    def media(self) -> list[str]:
        return [name for name in self.originals if not name.endswith(".json")]

    def assert_untouched(self, head: str | None = None) -> None:
        """Nothing changed: still on main at ``head``, no branch, no LFS hooks, a clean tree."""
        assert self.git("symbolic-ref", "--short", "HEAD").strip() == "main"
        assert self.git("rev-parse", "HEAD").strip() == (head or self.base)
        assert self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{BRANCH}", check=False) == ""
        assert not (self.path / ".git" / "hooks" / "pre-push").exists()
        assert self.git("status", "--porcelain") == ""


def _isolated_git_env(home: Path) -> dict[str, str]:
    """The caller's PATH with git config, identity and LFS settings of the test's own."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    config = home / "gitconfig"
    config.write_text("", encoding="utf-8")
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "xdg"),
            "GIT_CONFIG_GLOBAL": str(config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Launch Media Test",
            "GIT_AUTHOR_EMAIL": "launch-media@example.invalid",
            "GIT_COMMITTER_NAME": "Launch Media Test",
            "GIT_COMMITTER_EMAIL": "launch-media@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
            "TMPDIR": str(home),
        }
    )
    return env


@pytest.fixture
def checkout(tmp_path: Path) -> Checkout:
    """``main`` holding the launch media as plain blobs, the script, and a bare ``origin``."""
    env = _isolated_git_env(tmp_path)
    repo = tmp_path / "viewspec"
    originals = {name: (ROOT / name).read_bytes() for name in _launch_assets()}
    # Files an hour old, like a checkout made earlier: git then trusts the index's stat data
    # instead of re-hashing racily new files, so only a renormalizing add converts them.
    an_hour_ago = time.time() - 3600
    for name, data in originals.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(data)
        os.utime(repo / name, (an_hour_ago, an_hour_ago))
    (repo / "scripts").mkdir()
    shutil.copyfile(SCRIPT, repo / "scripts" / SCRIPT.name)
    shutil.copyfile(ROOT / "demos" / "CNAME", repo / "demos" / "CNAME")
    # The repository's own attributes from the same commit as the tracked file list, minus any
    # LFS lines, so the run meets the text=auto and eol rules (and blank lines) of the real one.
    committed = subprocess.run(
        [GIT or "git", "cat-file", "blob", "HEAD:.gitattributes"], cwd=ROOT, capture_output=True, check=True
    )
    attributes = committed.stdout.splitlines(keepends=True)
    (repo / ".gitattributes").write_bytes(
        b"".join(line for line in attributes if b"filter=lfs" not in line and b"Git LFS" not in line)
    )
    origin = tmp_path / "origin.git"
    checkout = Checkout(path=repo, origin=origin, env=env, base="", originals=originals)
    checkout.git("init", "-q")
    checkout.git("symbolic-ref", "HEAD", "refs/heads/main")
    checkout.git("add", "-A")
    checkout.git("commit", "-q", "-m", "Launch media as plain blobs")
    checkout.git("init", "-q", "--bare", str(origin))
    checkout.git("remote", "add", "origin", str(origin))
    checkout.git("push", "-q", "origin", "main")
    checkout.base = checkout.git("rev-parse", "HEAD").strip()
    return checkout


def _remote_state(checkout: Checkout) -> tuple[str, bool]:
    refs = checkout.git("--git-dir", str(checkout.origin), "for-each-ref", "--format=%(refname) %(objectname)")
    return refs, (checkout.origin / "lfs").exists()


def _commands(message: str) -> list[str]:
    """The commands that an error or recovery message tells the owner to run, in order."""
    return [line.strip() for line in message.splitlines() if line.startswith(("    git ", "    sh "))]


def _hooks(directory: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in directory.iterdir()}


def _added_lfs_hooks(directory: Path, before: dict[str, str]) -> list[str]:
    """The hooks that appeared in ``directory`` since ``before``; each must be a Git LFS hook,
    and every hook in ``before`` must be unchanged."""
    after = _hooks(directory)
    assert {name: after.get(name) for name in before} == before, "a hook that was there changed or went away"
    added = sorted(set(after) - set(before))
    for name in added:
        assert name in LFS_HOOKS and f'git lfs {name} "$@"' in after[name], (name, after[name])
    return added


@needs_git_lfs
def test_migration_commits_canonical_pointers_and_keeps_the_media(checkout: Checkout) -> None:
    attributes_before = (checkout.path / ".gitattributes").read_bytes()

    result = checkout.migrate()

    assert result.returncode == 0, result.stderr + result.stdout
    assert sorted(checkout.media) == sorted(name for name in checkout.originals if name.endswith(EXTENSIONS))
    assert checkout.git("symbolic-ref", "--short", "HEAD").strip() == BRANCH
    # One new commit on top of main; main itself and the remote are untouched.
    assert checkout.git("rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [checkout.base]
    assert checkout.git("rev-parse", "main").strip() == checkout.base
    assert _remote_state(checkout) == (f"refs/heads/main {checkout.base}\n", False)
    assert checkout.git("status", "--porcelain") == ""
    for name, data in checkout.originals.items():
        assert (checkout.path / name).read_bytes() == data, f"{name} changed in the working tree"
        committed = checkout.blob(f"HEAD:{name}")
        if name in checkout.media:
            assert committed == _canonical_pointer(data), name
            oid = hashlib.sha256(data).hexdigest()
            stored = checkout.path / ".git" / "lfs" / "objects" / oid[:2] / oid[2:4] / oid
            assert stored.read_bytes() == data, f"the push would have no object to upload for {name}"
        else:
            assert committed == data, f"{name} must stay a plain Git file"
    assert sorted(checkout.git("lfs", "ls-files", "--name-only").split()) == sorted(checkout.media)
    attributes = checkout.blob("HEAD:.gitattributes")
    assert attributes.startswith(attributes_before), "the existing .gitattributes lines must stay byte-identical"
    for extension in EXTENSIONS:
        assert f"{MEDIA_DIR}/*.{extension} filter=lfs diff=lfs merge=lfs -text\n".encode() in attributes
    assert f"git lfs push origin {BRANCH}\n" in result.stdout
    assert f"git push --set-upstream origin {BRANCH}\n" in result.stdout
    assert "sh scripts/migrate_launch_media_to_lfs.sh --verify-site https://viewspec.dev\n" in result.stdout
    assert "--force" not in result.stdout


@needs_git_lfs
def test_running_the_migration_again_changes_nothing(checkout: Checkout) -> None:
    assert checkout.migrate().returncode == 0
    head = checkout.git("rev-parse", "HEAD").strip()
    attributes = (checkout.path / ".gitattributes").read_bytes()

    again = checkout.migrate()
    checkout.git("switch", "-q", "main")
    from_main = checkout.migrate()

    for result in (again, from_main):
        assert result.returncode == 0, result.stderr + result.stdout
        assert f"{BRANCH} already stores the launch media as Git LFS pointers" in result.stdout
        assert f"git push --set-upstream origin {BRANCH}\n" in result.stdout
    # The run from main switched to the existing branch instead of making another commit.
    assert checkout.git("symbolic-ref", "--short", "HEAD").strip() == BRANCH
    assert checkout.git("rev-parse", "HEAD").strip() == head
    assert checkout.git("for-each-ref", "--format=%(refname:short)", "refs/heads").split() == [BRANCH, "main"]
    assert (checkout.path / ".gitattributes").read_bytes() == attributes
    assert checkout.git("status", "--porcelain") == ""


@needs_git_lfs
@pytest.mark.parametrize("calls_lfs", [True, False], ids=["merged-hook", "own-hook"])
@pytest.mark.parametrize("shared", [False, True], ids=["git-dir", "core.hooksPath"])
def test_an_existing_pre_push_hook_neither_stops_the_migration_nor_changes(
    checkout: Checkout, tmp_path: Path, calls_lfs: bool, shared: bool
) -> None:
    # git lfs install refuses to replace a hook it did not write, even one merged the way
    # git lfs update --manual shows, so it must not stand between the owner and the commit.
    hooks = tmp_path / "shared-hooks" if shared else checkout.path / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    if shared:
        checkout.git("config", "--global", "core.hooksPath", str(hooks))
    hook = hooks / "pre-push"
    body = "#!/bin/sh\necho 'my own pre-push checks'\n" + ('git lfs pre-push "$@"\n' if calls_lfs else "")
    hook.write_text(body, encoding="utf-8")
    hook.chmod(0o755)
    before = _hooks(hooks)

    first = checkout.migrate()
    again = checkout.migrate()
    checkout.git("switch", "-q", "main")
    from_main = checkout.migrate()

    assert first.returncode == 0, first.stderr + first.stdout
    assert f"Committed {checkout.git('rev-parse', '--short', BRANCH).strip()} on {BRANCH}" in first.stdout
    for result in (again, from_main):
        assert result.returncode == 0, result.stderr + result.stdout
        assert f"{BRANCH} already stores the launch media as Git LFS pointers" in result.stdout
    for name in checkout.media:
        assert checkout.blob(f"{BRANCH}:{name}") == _canonical_pointer(checkout.originals[name]), name
    # The pre-push hook is as it was, and anything new in its directory is a Git LFS hook.
    assert hook.read_text(encoding="utf-8") == body
    _added_lfs_hooks(hooks, before)
    # The push steps upload the objects themselves and say when a plain push would not.
    shown = str(hook) if shared else ".git/hooks/pre-push"
    for result in (first, again, from_main):
        assert f"git lfs push origin {BRANCH}\n" in result.stdout
        if shared:
            assert f"{HOOKS_PATH_NOTE} your global Git config" in result.stdout, result.stdout
            assert f"\n    {hooks}\n" in result.stdout, result.stdout
        else:
            assert HOOKS_PATH_NOTE not in result.stdout, result.stdout
        if calls_lfs:
            assert NO_LFS_HOOK_NOTE not in result.stdout, result.stdout
        else:
            assert f"{NO_LFS_HOOK_NOTE} ({shown})" in result.stdout, result.stdout


@needs_git_lfs
@pytest.mark.parametrize("scope", ["global", "local"])
def test_git_lfs_hooks_that_go_into_core_hooks_path_are_announced(
    checkout: Checkout, tmp_path: Path, scope: str
) -> None:
    # The LFS filter that converts the files adds the Git LFS hooks where no hook of that name
    # exists, in core.hooksPath when that is set. A global setting shares that directory with
    # every repository that does not set its own.
    hooks = tmp_path / "shared-hooks"
    hooks.mkdir()
    pre_commit = hooks / "pre-commit"
    pre_commit.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pre_commit.chmod(0o755)
    checkout.git("config", f"--{scope}", "core.hooksPath", str(hooks))
    before = _hooks(hooks)
    own_hooks = _hooks(checkout.path / ".git" / "hooks")

    result = checkout.migrate()

    assert result.returncode == 0, result.stderr + result.stdout
    # The note comes before the first command that can run the filter.
    note = result.stdout[: result.stdout.index("+ git switch -c")]
    assert note.startswith(HOOKS_PATH_NOTE), result.stdout
    assert f"\n    {hooks}\n" in note, note
    flat = " ".join(note.split())
    if scope == "global":
        assert f"{HOOKS_PATH_NOTE} your global Git config ({checkout.env['GIT_CONFIG_GLOBAL']})." in flat, note
        assert "and of every other repository that does not set its own core.hooksPath" in flat, note
    else:
        assert f"{HOOKS_PATH_NOTE} this repository's Git config (.git/config)." in flat, note
        assert "every other repository" not in flat, note
    assert "may add its pre-push, post-checkout, post-commit and post-merge hooks there" in flat, note
    # No hook of those names was in the way, so all four are now in that directory, next to
    # the pre-commit hook, which is unchanged; .git/hooks gets none.
    assert _added_lfs_hooks(hooks, before) == sorted(LFS_HOOKS)
    assert _hooks(checkout.path / ".git" / "hooks") == own_hooks


@needs_git_lfs
@pytest.mark.parametrize("rerun_on", ["main", BRANCH])
@pytest.mark.parametrize("left_by", ["a-stopped-run", "an-unpushed-migration"])
def test_a_branch_that_lacks_commits_of_main_is_not_reused(checkout: Checkout, left_by: str, rerun_on: str) -> None:
    if left_by == "an-unpushed-migration":
        assert checkout.migrate().returncode == 0
        checkout.git("switch", "-q", "main")
    else:
        # A run that stops after creating the branch leaves it at main as it was then.
        checkout.git("branch", BRANCH)
    stale = checkout.git("rev-parse", BRANCH).strip()
    # Then main moves on with a re-encode of a launch asset, and the next run starts on main
    # or on the branch, where a stopped run leaves the owner.
    gif = f"{MEDIA_DIR}/hn-launch-demo.gif"
    reencoded = checkout.originals[gif] + b"\0"
    (checkout.path / gif).write_bytes(reencoded)
    checkout.git("commit", "-q", "-am", "Re-encode the HN demo GIF")
    head = checkout.git("rev-parse", "HEAD").strip()
    checkout.git("switch", "-q", rerun_on)

    refused = checkout.migrate()

    assert refused.returncode == 1, refused.stdout
    assert f"error: {BRANCH} " in refused.stderr
    assert checkout.git("symbolic-ref", "--short", "HEAD").strip() == rerun_on
    assert checkout.git("rev-parse", "main").strip() == head
    assert checkout.git("rev-parse", BRANCH).strip() == stale
    assert checkout.git("status", "--porcelain") == ""

    # The way out that the error names ends in a migration of main as it is now.
    delete = "-D" if left_by == "an-unpushed-migration" else "-d"
    way_out = [f"git branch {delete} {BRANCH}"]
    if rerun_on == BRANCH:
        way_out.insert(0, "git switch main")
    assert _commands(refused.stderr) == way_out, refused.stderr
    if left_by == "an-unpushed-migration":
        assert "Store the launch media in Git LFS (audit H-3)" in refused.stderr
    for command in way_out:
        checkout.git(*command.split()[1:])
    migrated = checkout.migrate()
    assert migrated.returncode == 0, migrated.stderr + migrated.stdout
    assert checkout.git("rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [head]
    assert checkout.blob(f"HEAD:{gif}") == _canonical_pointer(reencoded)
    assert (checkout.path / gif).read_bytes() == reencoded


@needs_git_lfs
def test_a_run_that_stopped_on_its_branch_is_not_resumed_there_once_main_moves_on(checkout: Checkout) -> None:
    # A pre-commit hook that fails stops the run after it created the branch and staged the
    # pointers, and the branch stays checked out.
    hook = checkout.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'pre-commit: not now' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    stopped = checkout.migrate()

    assert stopped.returncode == 1, stopped.stdout
    assert "pre-commit: not now" in stopped.stderr
    assert f"Nothing was committed, and {BRANCH} is left checked out." in stopped.stderr
    assert _commands(stopped.stderr) == [
        f"git restore --staged --worktree -- .gitattributes {MEDIA_DIR}",
        "git switch main",
        f"sh scripts/{SCRIPT.name}",
    ]
    assert checkout.git("symbolic-ref", "--short", "HEAD").strip() == BRANCH
    assert checkout.git("rev-parse", "HEAD").strip() == checkout.base

    # The owner returns to a clean tree and removes the hook, but stays on the branch while
    # main moves on with a re-encode of a launch asset.
    checkout.git("restore", "--staged", "--worktree", "--", ".gitattributes", MEDIA_DIR)
    hook.unlink()
    checkout.git("switch", "-q", "main")
    gif = f"{MEDIA_DIR}/hn-launch-demo.gif"
    (checkout.path / gif).write_bytes(checkout.originals[gif] + b"\0")
    checkout.git("commit", "-q", "-am", "Re-encode the HN demo GIF")
    head = checkout.git("rev-parse", "HEAD").strip()
    checkout.git("switch", "-q", BRANCH)

    # Run again on the branch, it must not commit the media of the older main there.
    refused = checkout.migrate()

    assert refused.returncode == 1, refused.stdout
    assert f"error: {BRANCH} is behind main and has no commits of its own" in refused.stderr
    assert _commands(refused.stderr) == ["git switch main", f"git branch -d {BRANCH}"]
    assert checkout.git("symbolic-ref", "--short", "HEAD").strip() == BRANCH
    assert checkout.git("rev-parse", BRANCH).strip() == checkout.base
    assert checkout.git("rev-parse", "main").strip() == head
    assert checkout.git("status", "--porcelain") == ""


@needs_git_lfs
def test_a_tag_named_main_does_not_turn_main_into_an_ordinary_branch(checkout: Checkout) -> None:
    # The tag makes "main" ambiguous: git symbolic-ref --short HEAD then prints heads/main.
    checkout.git("tag", "main")

    result = checkout.migrate()

    assert result.returncode == 0, result.stderr + result.stdout
    assert checkout.git("symbolic-ref", "HEAD").strip() == f"refs/heads/{BRANCH}"
    assert checkout.git("rev-parse", "refs/heads/main").strip() == checkout.base
    assert f"git push --set-upstream origin {BRANCH}\n" in result.stdout
    assert "heads/main" not in result.stdout

    # The branch main moves on and the tag stays behind: the branch must be compared with
    # the branch main.
    checkout.git("switch", "-q", "main")
    (checkout.path / "NOTES.md").write_text("Later work.\n", encoding="utf-8")
    checkout.git("add", "NOTES.md")
    checkout.git("commit", "-q", "-m", "Later work")

    again = checkout.migrate()

    assert again.returncode == 1, again.stdout
    assert f"error: {BRANCH} does not contain main" in again.stderr
    assert checkout.git("symbolic-ref", "HEAD").strip() == "refs/heads/main"


@needs_git_lfs
def test_migration_on_a_migrated_main_only_points_at_the_site_check(checkout: Checkout) -> None:
    assert checkout.migrate().returncode == 0
    checkout.git("switch", "-q", "main")
    checkout.git("merge", "-q", "--ff-only", BRANCH)
    head = checkout.git("rev-parse", "HEAD").strip()

    result = checkout.migrate()

    assert result.returncode == 0, result.stderr + result.stdout
    assert "main already stores the launch media in Git LFS" in result.stdout
    assert "--verify-site https://viewspec.dev" in result.stdout
    assert checkout.git("symbolic-ref", "--short", "HEAD").strip() == "main"
    assert checkout.git("rev-parse", "HEAD").strip() == head


@needs_git_lfs
def test_migration_refuses_a_dirty_tree(checkout: Checkout) -> None:
    manifest = checkout.path / MEDIA_DIR / "headliner-manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b"\n")

    result = checkout.migrate()

    assert result.returncode == 1
    assert "the working tree is not clean" in result.stderr
    manifest.write_bytes(checkout.originals[f"{MEDIA_DIR}/headliner-manifest.json"])
    checkout.assert_untouched()


@needs_git_lfs
def test_migration_stops_before_anything_when_git_lfs_is_missing(checkout: Checkout, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin-without-git-lfs"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(GIT or "git")
    env = {**checkout.env, "PATH": str(bin_dir)}
    probe = subprocess.run([str(bin_dir / "git"), "lfs", "version"], env=env, capture_output=True, check=False)
    if probe.returncode == 0:
        pytest.skip("git finds git-lfs through its exec path, so PATH cannot hide it")

    result = checkout.migrate(env=env)

    assert result.returncode == 1
    assert "git-lfs is not installed" in result.stderr
    checkout.assert_untouched()


@needs_git_lfs
def test_an_unlisted_launch_asset_stops_the_migration(checkout: Checkout) -> None:
    (checkout.path / MEDIA_DIR / "launch-teaser.webm").write_bytes(b"\x1aE\xdf\xa3 a new asset")
    checkout.git("add", "-A")
    checkout.git("commit", "-q", "-m", "Add a teaser")
    head = checkout.git("rev-parse", "HEAD").strip()

    result = checkout.migrate()

    assert result.returncode == 1
    assert f"not listed in scripts/migrate_launch_media_to_lfs.sh: {MEDIA_DIR}/launch-teaser.webm" in result.stderr
    checkout.assert_untouched(head)


@needs_git_lfs
@pytest.mark.skipif(shutil.which("curl") is None, reason="--verify-site downloads with curl")
def test_verify_site_accepts_the_media_and_names_pointer_files(checkout: Checkout, tmp_path: Path) -> None:
    site = tmp_path / "site"

    def publish(name: str, data: bytes) -> None:
        target = site / name.removeprefix("demos/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    for name, data in checkout.originals.items():
        publish(name, data)
    url = site.as_uri()

    # Before and after the migration HEAD holds the files or their pointers; both verify.
    before = checkout.migrate("--verify-site", url)
    assert checkout.migrate().returncode == 0
    after = checkout.migrate("--verify-site", url + "/")
    for result in (before, after):
        assert result.returncode == 0, result.stderr + result.stdout
        assert f"All {len(checkout.media)} launch media files on {url} match HEAD." in result.stdout

    # A deploy without lfs: true publishes the pointers; a broken upload truncates, corrupts
    # (same size, other bytes) or loses files.
    served_pointer, truncated, corrupted, missing = sorted(checkout.media)[:4]
    publish(served_pointer, _canonical_pointer(checkout.originals[served_pointer]))
    publish(truncated, checkout.originals[truncated][:1000])
    data = checkout.originals[corrupted]
    publish(corrupted, data[:-1] + bytes([data[-1] ^ 0xFF]))
    (site / missing.removeprefix("demos/")).unlink()

    broken = checkout.migrate("--verify-site", url)

    assert broken.returncode == 1
    assert f"error: 4 of {len(checkout.media)} launch media files" in broken.stderr
    lines = broken.stdout.splitlines()
    verdicts = {served_pointer: "POINTER", truncated: "MISMATCH", corrupted: "MISMATCH", missing: "FAILED"}
    for name, verdict in verdicts.items():
        published = name.removeprefix("demos/")
        assert any(line.split()[:2] == [verdict, f"{published}:"] for line in lines), (name, broken.stdout)
    assert sum(line.split()[0] == "ok" for line in lines if line.startswith("  ")) == len(checkout.media) - 4


def _workflow_jobs() -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_id, job in (yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs") or {}).items():
            jobs[f"{path.name}:{job_id}"] = job
    return jobs


def _checkouts(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in job.get("steps", []) if "actions/checkout" in str(step.get("uses", ""))]


def test_pages_deploy_checks_out_the_lfs_media() -> None:
    publishing = {
        name: job
        for name, job in _workflow_jobs().items()
        for step in job.get("steps", [])
        if "actions/upload-pages-artifact" in str(step.get("uses", ""))
        and f"{MEDIA_DIR}/".startswith(str((step.get("with") or {}).get("path", "_site")).rstrip("/") + "/")
    }

    assert publishing, f"no workflow publishes {MEDIA_DIR}/ to GitHub Pages"
    for name, job in publishing.items():
        # Without lfs: true actions/checkout skips the LFS download and the site serves pointers.
        assert _checkouts(job), name
        assert all((step.get("with") or {}).get("lfs") is True for step in _checkouts(job)), name


def test_release_checklist_covers_the_lfs_migration_and_every_lfs_checkout() -> None:
    checklist = (ROOT / "docs" / "release-checklist.md").read_text(encoding="utf-8")
    lfs_jobs = [
        name.split(":", 1)[1]
        for name, job in _workflow_jobs().items()
        if any((step.get("with") or {}).get("lfs") is True for step in _checkouts(job))
    ]

    assert "sh scripts/migrate_launch_media_to_lfs.sh" in checklist
    assert "sh scripts/migrate_launch_media_to_lfs.sh --verify-site" in checklist
    # Each job that downloads LFS content spends the owner's LFS bandwidth, so the quota item
    # must name it.
    assert lfs_jobs
    for job_id in lfs_jobs:
        assert f"`{job_id}`" in checklist, job_id
