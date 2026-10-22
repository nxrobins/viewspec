from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from scripts.check_distribution import (
    SCHEMA_RELEASE_FILES,
    STUDIO_RELEASE_FILES,
    DistributionError,
    _validate_member_name,
    audit_distribution,
)
from viewspec import __version__


VERSION = __version__


def _write_wheel(path: Path, *, extra: dict[str, bytes] | None = None) -> None:
    members = {
        "viewspec/__init__.py": b"",
        "viewspec/_version.py": f'__version__ = "{VERSION}"\n'.encode(),
        "viewspec/py.typed": b"",
        f"viewspec-{VERSION}.dist-info/METADATA": f"Name: viewspec\nVersion: {VERSION}\n".encode(),
        f"viewspec-{VERSION}.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
        f"viewspec-{VERSION}.dist-info/RECORD": b"",
    }
    members.update({f"viewspec/{name}": b"" for name in STUDIO_RELEASE_FILES | SCHEMA_RELEASE_FILES})
    members.update(extra or {})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _write_sdist(path: Path, *, extra: dict[str, bytes] | None = None) -> None:
    root = f"viewspec-{VERSION}"
    members = {
        f"{root}/LICENSE": b"MIT\n",
        f"{root}/README.md": b"# ViewSpec\n",
        f"{root}/pyproject.toml": f'[project]\nname = "viewspec"\nversion = "{VERSION}"\n'.encode(),
        f"{root}/PKG-INFO": f"Name: viewspec\nVersion: {VERSION}\n".encode(),
        f"{root}/src/viewspec/__init__.py": b"",
        f"{root}/src/viewspec/_version.py": f'__version__ = "{VERSION}"\n'.encode(),
        f"{root}/src/viewspec/py.typed": b"",
    }
    members.update({f"{root}/src/viewspec/{name}": b"" for name in STUDIO_RELEASE_FILES | SCHEMA_RELEASE_FILES})
    members.update(extra or {})
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def _safe_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(dist / f"viewspec-{VERSION}-py3-none-any.whl")
    _write_sdist(dist / f"viewspec-{VERSION}.tar.gz")
    return dist


def test_distribution_audit_accepts_one_bounded_wheel_and_sdist(tmp_path: Path) -> None:
    report = audit_distribution(_safe_dist(tmp_path))
    assert report["version"] == VERSION
    assert report["wheel_members"] >= 6
    assert report["sdist_members"] >= 7


@pytest.mark.parametrize(
    ("archive", "member"),
    [
        ("wheel", "../outside.py"),
        ("wheel", "viewspec/node_modules/evil.js"),
        ("sdist", f"viewspec-{VERSION}/demos/index.html"),
        ("sdist", f"viewspec-{VERSION}/.github/workflows/ci.yml"),
        ("sdist", f"viewspec-{VERSION}/src/viewspec/.git/config"),
    ],
)
def test_distribution_audit_rejects_traversal_and_repository_bloat(tmp_path: Path, archive: str, member: str) -> None:
    dist = _safe_dist(tmp_path)
    if archive == "wheel":
        _write_wheel(dist / f"viewspec-{VERSION}-py3-none-any.whl", extra={member: b"bad"})
    else:
        _write_sdist(dist / f"viewspec-{VERSION}.tar.gz", extra={member: b"bad"})

    with pytest.raises(DistributionError, match="forbidden|unsafe"):
        audit_distribution(dist)


def test_distribution_audit_rejects_duplicate_artifact_kinds(tmp_path: Path) -> None:
    dist = _safe_dist(tmp_path)
    _write_wheel(dist / f"viewspec-{VERSION}-1-py3-none-any.whl")

    with pytest.raises(DistributionError, match="exactly one wheel"):
        audit_distribution(dist)


def test_distribution_audit_rejects_version_drift(tmp_path: Path) -> None:
    dist = _safe_dist(tmp_path)
    _write_wheel(
        dist / f"viewspec-{VERSION}-py3-none-any.whl",
        extra={f"viewspec-{VERSION}.dist-info/METADATA": b"Name: viewspec\nVersion: 9.9.9\n"},
    )

    with pytest.raises(DistributionError, match="version"):
        audit_distribution(dist)


def test_distribution_audit_rejects_sdist_runtime_version_drift(tmp_path: Path) -> None:
    dist = _safe_dist(tmp_path)
    root = f"viewspec-{VERSION}"
    _write_sdist(
        dist / f"viewspec-{VERSION}.tar.gz",
        extra={f"{root}/src/viewspec/_version.py": b'__version__ = "9.9.9"\n'},
    )

    with pytest.raises(DistributionError, match="runtime version"):
        audit_distribution(dist)


@pytest.mark.parametrize("archive", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "missing",
    [
        "studio.py",
        "studio_creation_daemon.py",
        "review_annotation.py",
        "host_verify_template/package-lock.json",
        "schema/viewspec_pb2.pyi",
    ],
)
def test_distribution_audit_rejects_missing_studio_runtime(tmp_path: Path, archive: str, missing: str) -> None:
    dist = _safe_dist(tmp_path)
    if archive == "wheel":
        artifact = dist / f"viewspec-{VERSION}-py3-none-any.whl"
        with zipfile.ZipFile(artifact) as wheel:
            members = {name: wheel.read(name) for name in wheel.namelist() if name != f"viewspec/{missing}"}
        with zipfile.ZipFile(artifact, "w") as wheel:
            for name, data in members.items():
                wheel.writestr(name, data)
    else:
        artifact = dist / f"viewspec-{VERSION}.tar.gz"
        with tarfile.open(artifact, "r:gz") as sdist:
            members = [(member, sdist.extractfile(member).read()) for member in sdist.getmembers()
                       if member.name != f"viewspec-{VERSION}/src/viewspec/{missing}"]
        with tarfile.open(artifact, "w:gz") as sdist:
            for member, data in members:
                sdist.addfile(member, io.BytesIO(data))
    with pytest.raises(DistributionError, match="missing required members"):
        audit_distribution(dist)


@st.composite
def _forbidden_distribution_component(draw: st.DrawFn) -> str:
    component = draw(st.sampled_from((".git", ".github", ".venv", "__pycache__", "demos", "node_modules")))
    uppercase = draw(st.lists(st.booleans(), min_size=len(component), max_size=len(component)))
    return "".join(character.upper() if flag else character for character, flag in zip(component, uppercase, strict=True))


@given(
    prefix=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8),
        min_size=0,
        max_size=4,
    ),
    forbidden=_forbidden_distribution_component(),
)
def test_distribution_member_policy_rejects_generated_forbidden_components(prefix, forbidden) -> None:
    member = "/".join(("viewspec", *prefix, forbidden, "payload.bin"))

    with pytest.raises(DistributionError, match="forbidden"):
        _validate_member_name(member, archive="wheel")
