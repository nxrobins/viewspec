#!/usr/bin/env python3
"""Run the public browser conformance corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from viewspec.conformance import load_conformance_corpus, run_conformance_corpus
from viewspec.local_tools import atomic_write


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default="conformance/verification/corpus.json",
        help="Conformance corpus manifest path.",
    )
    parser.add_argument(
        "--output",
        default="verification-corpus-report.json",
        help="Machine-readable corpus report path.",
    )
    parser.add_argument("--install", action="store_true", help="Prepare host dependencies.")
    args = parser.parse_args()

    report = run_conformance_corpus(
        load_conformance_corpus(args.manifest),
        install=args.install,
    )
    atomic_write(Path(args.output), json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
