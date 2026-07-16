#!/usr/bin/env python3
"""Fail closed when release archives contain unsafe, stale, or bloated files."""

from __future__ import annotations

import argparse
import json
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Any


MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_UNPACKED_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 512
FORBIDDEN_COMPONENTS = {
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "audit-output",
    "build",
    "demos",
    "dist",
    "node_modules",
}


class DistributionError(RuntimeError):
    """The built distribution violates a release invariant."""


def _project_version() -> str:
    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    version = project.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise DistributionError("project version is missing")
    return version


def _validate_member_name(name: str, *, archive: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise DistributionError(f"{archive} has unsafe member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DistributionError(f"{archive} has unsafe member path: {name!r}")
    lowered = {part.casefold() for part in path.parts}
    forbidden = sorted(lowered & FORBIDDEN_COMPONENTS)
    if forbidden:
        raise DistributionError(f"{archive} has forbidden component {forbidden[0]!r}: {name}")
    return path


def _validate_counts(sizes: list[int], *, archive: str) -> None:
    if len(sizes) > MAX_MEMBERS:
        raise DistributionError(f"{archive} has {len(sizes)} members; maximum is {MAX_MEMBERS}")
    if any(size < 0 or size > MAX_MEMBER_BYTES for size in sizes):
        raise DistributionError(f"{archive} has a member larger than {MAX_MEMBER_BYTES} bytes")
    if sum(sizes) > MAX_UNPACKED_BYTES:
        raise DistributionError(f"{archive} expands beyond {MAX_UNPACKED_BYTES} bytes")


def _metadata_version(data: bytes, *, archive: str) -> str:
    version = BytesParser(policy=default).parsebytes(data).get("Version")
    if not version:
        raise DistributionError(f"{archive} metadata has no Version field")
    return str(version)


def _audit_wheel(path: Path, version: str) -> int:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise DistributionError(f"wheel exceeds {MAX_ARCHIVE_BYTES} bytes")
    dist_info = f"viewspec-{version}.dist-info"
    required = {
        "viewspec/__init__.py",
        "viewspec/_version.py",
        "viewspec/py.typed",
        f"{dist_info}/METADATA",
        f"{dist_info}/WHEEL",
        f"{dist_info}/RECORD",
    }
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise DistributionError("wheel has duplicate member names")
        for info in infos:
            member = _validate_member_name(info.filename.rstrip("/"), archive="wheel")
            if info.external_attr >> 16 & 0o170000 == 0o120000:
                raise DistributionError(f"wheel has unsafe symbolic link: {info.filename}")
            if member.parts[0] not in {"viewspec", dist_info}:
                raise DistributionError(f"wheel has forbidden top-level path: {info.filename}")
            if member.parts[:2] == ("viewspec", "tests"):
                raise DistributionError(f"wheel has forbidden repository tests: {info.filename}")
        _validate_counts([info.file_size for info in infos], archive="wheel")
        missing = sorted(required - set(names))
        if missing:
            raise DistributionError(f"wheel is missing required members: {missing}")
        metadata = archive.read(f"{dist_info}/METADATA")
        runtime_version = archive.read("viewspec/_version.py").decode("utf-8")
    if _metadata_version(metadata, archive="wheel") != version:
        raise DistributionError("wheel metadata version does not match pyproject.toml")
    if f'__version__ = "{version}"' not in runtime_version:
        raise DistributionError("wheel runtime version does not match pyproject.toml")
    return len(infos)


def _audit_sdist(path: Path, version: str) -> int:
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise DistributionError(f"sdist exceeds {MAX_ARCHIVE_BYTES} bytes")
    root = f"viewspec-{version}"
    required = {
        f"{root}/LICENSE",
        f"{root}/README.md",
        f"{root}/pyproject.toml",
        f"{root}/PKG-INFO",
        f"{root}/src/viewspec/__init__.py",
        f"{root}/src/viewspec/_version.py",
        f"{root}/src/viewspec/py.typed",
    }
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name.rstrip("/") for member in members]
        if len(names) != len(set(names)):
            raise DistributionError("sdist has duplicate member names")
        for member in members:
            member_path = _validate_member_name(member.name.rstrip("/"), archive="sdist")
            if not (member.isfile() or member.isdir()):
                raise DistributionError(f"sdist has unsafe non-file member: {member.name}")
            if member_path.parts[0] != root:
                raise DistributionError(f"sdist has forbidden top-level path: {member.name}")
            relative = member_path.parts[1:]
            if relative and relative[0] not in {
                ".gitignore",
                "LICENSE",
                "README.md",
                "pyproject.toml",
                "PKG-INFO",
                "src",
            }:
                raise DistributionError(f"sdist has forbidden repository path: {member.name}")
            if relative and relative[0] == "src" and relative[:2] != ("src", "viewspec"):
                raise DistributionError(f"sdist has forbidden source package: {member.name}")
        _validate_counts([member.size for member in members], archive="sdist")
        missing = sorted(required - set(names))
        if missing:
            raise DistributionError(f"sdist is missing required members: {missing}")
        metadata_file = archive.extractfile(f"{root}/PKG-INFO")
        if metadata_file is None:
            raise DistributionError("sdist metadata is not a regular file")
        metadata = metadata_file.read()
        packaged_project_file = archive.extractfile(f"{root}/pyproject.toml")
        runtime_version_file = archive.extractfile(f"{root}/src/viewspec/_version.py")
        if packaged_project_file is None or runtime_version_file is None:
            raise DistributionError("sdist version sources are not regular files")
        packaged_project_bytes = packaged_project_file.read()
        runtime_version = runtime_version_file.read().decode("utf-8")
    if _metadata_version(metadata, archive="sdist") != version:
        raise DistributionError("sdist metadata version does not match pyproject.toml")
    try:
        packaged_project = tomllib.loads(packaged_project_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise DistributionError(f"sdist pyproject.toml is invalid: {error}") from error
    if packaged_project.get("project", {}).get("version") != version:
        raise DistributionError("sdist project version does not match repository pyproject.toml")
    if f'__version__ = "{version}"' not in runtime_version:
        raise DistributionError("sdist runtime version does not match pyproject.toml")
    return len(members)


def audit_distribution(dist_dir: Path) -> dict[str, Any]:
    dist_dir = Path(dist_dir)
    if not dist_dir.is_dir():
        raise DistributionError(f"distribution directory does not exist: {dist_dir}")
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise DistributionError(f"expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        raise DistributionError(f"expected exactly one sdist, found {len(sdists)}")
    unexpected = sorted(path.name for path in dist_dir.iterdir() if path not in {*wheels, *sdists})
    if unexpected:
        raise DistributionError(f"distribution directory has unexpected files: {unexpected}")

    version = _project_version()
    expected_prefix = f"viewspec-{version}"
    if not wheels[0].name.startswith(f"{expected_prefix}-") or sdists[0].name != f"{expected_prefix}.tar.gz":
        raise DistributionError("distribution filenames do not match the project version")
    return {
        "version": version,
        "wheel": wheels[0].name,
        "wheel_members": _audit_wheel(wheels[0], version),
        "sdist": sdists[0].name,
        "sdist_members": _audit_sdist(sdists[0], version),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args()
    try:
        report = audit_distribution(args.dist_dir)
    except (DistributionError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(1, f"distribution audit failed: {error}\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
