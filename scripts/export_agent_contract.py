#!/usr/bin/env python3
"""Regenerate the published agent contract files under demos/ from the SDK."""

from __future__ import annotations

import json
from pathlib import Path

from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, AGENT_SYSTEM_PROMPT
from viewspec.app_bundle import AGENT_APP_BUNDLE_SCHEMA, starter_app_bundle
from viewspec.intent_tools import starter_intent_payload

DEMOS = Path(__file__).resolve().parents[1] / "demos"
JSON_FILES = {
    "agent-intent-bundle.schema.json": AGENT_INTENT_BUNDLE_SCHEMA,
    "agent-intent-example.dashboard.json": starter_intent_payload("dashboard"),
    "agent-app-bundle.schema.json": AGENT_APP_BUNDLE_SCHEMA,
    "agent-app-example.internal-tool.json": starter_app_bundle("internal_tool"),
}


def main() -> int:
    (DEMOS / "agent-system-prompt.txt").write_text(AGENT_SYSTEM_PROMPT.rstrip("\n") + "\n", encoding="utf-8")
    for name, payload in JSON_FILES.items():
        (DEMOS / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
