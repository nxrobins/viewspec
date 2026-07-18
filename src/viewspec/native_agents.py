"""Managed instruction files for native agent adoption."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec.agent_assets import (
    AGENT_ASSET_CHECK_COMMAND,
    AGENT_ASSET_CONTRACT_PROFILE,
    AGENT_ASSET_EXPORT_COMMAND,
    AGENT_ASSET_SCHEMA_VERSION,
)
from viewspec.local_tools import atomic_write


BEGIN_MARKER = "<!-- BEGIN VIEWSPEC AGENT INSTRUCTIONS v1 -->"
END_MARKER = "<!-- END VIEWSPEC AGENT INSTRUCTIONS v1 -->"
TARGET_PATHS = {
    "codex": Path("AGENTS.md"),
    "claude-code": Path("CLAUDE.md"),
    "cursor": Path(".cursor/rules/viewspec.mdc"),
    "copilot": Path(".github/copilot-instructions.md"),
}
VALID_TARGETS = (*TARGET_PATHS.keys(), "all")


class NativeAgentError(ValueError):
    """Raised when managed agent instructions cannot be safely updated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentInstructionChange:
    target: str
    path: Path
    action: str
    content: str

    def to_json(self, root: Path) -> dict[str, str]:
        return {
            "target": self.target,
            "path": _display_path(self.path, root),
            "action": self.action,
        }


def init_agent_instructions(root: str | Path, target: str, *, dry_run: bool = False) -> dict[str, Any]:
    resolved_root = Path(root).resolve()
    changes = plan_agent_instruction_changes(resolved_root, target)
    if not dry_run:
        for change in changes:
            if change.action == "unchanged":
                continue
            atomic_write(change.path, change.content)
    return {
        "ok": True,
        "target": target,
        "root": str(resolved_root),
        "changes": [change.to_json(resolved_root) for change in changes if change.action != "unchanged"],
    }


def plan_agent_instruction_changes(root: Path, target: str) -> list[AgentInstructionChange]:
    targets = _expand_targets(target)
    changes: list[AgentInstructionChange] = []
    for name in targets:
        path = root / TARGET_PATHS[name]
        block = agent_instruction_block(name)
        if not path.exists():
            changes.append(AgentInstructionChange(name, path, "create", block))
            continue
        current = _read_text_exact(path)
        next_text, action = _merge_instruction_block(current, block, path)
        changes.append(AgentInstructionChange(name, path, action, next_text))
    return changes


def agent_instruction_block(target: str) -> str:
    label = {
        "codex": "Codex",
        "claude-code": "Claude Code",
        "cursor": "Cursor",
        "copilot": "GitHub Copilot",
    }[target]
    return (
        f"{BEGIN_MARKER}\n"
        f"# ViewSpec Agent-Native UI Intent ({label})\n\n"
        "For new human-facing UI, create `viewspec.intent.json` as ViewSpec IntentBundle JSON. Do not write HTML, "
        "CSS, React, SwiftUI, Flutter, or CompositionIR as source; those are compiler outputs.\n\n"
        "Required workflow for new UI:\n\n"
        "If `DESIGN.md` is missing, create the starter design file once:\n\n"
        "```bash\n"
        "viewspec init-design --out DESIGN.md\n"
        "```\n\n"
        "Then validate, compile, and check:\n\n"
        "```bash\n"
        "viewspec validate-intent viewspec.intent.json --json\n"
        "viewspec compile viewspec.intent.json --design DESIGN.md --out dist/\n"
        "viewspec check dist/\n"
        "```\n\n"
        "For a first local proof bundle, run `viewspec prove --out .viewspec-proof`. Read `.viewspec-proof/PROOF.md` "
        "for the human summary, `.viewspec-proof/proof_report.json` for tool output, and "
        "`.viewspec-proof/support_bundle.json` for redacted failure triage. This proves source artifact integrity "
        "and provenance; it is not pixel-perfect visual regression, accessibility certification, or "
        "arbitrary host-app certification.\n\n"
        "For governed art direction, use at most one view-level aesthetic profile token in `viewspec.intent.json`: "
        "`aesthetic.calm_ops`, `aesthetic.premium_saas`, `aesthetic.data_dense`, "
        "`aesthetic.editorial_product`, or `aesthetic.executive_review`. It must target exactly "
        "`view:<view_spec.id>`. Aesthetic profiles are deterministic style and bounded-layout handles, not CSS, "
        "pixel-perfect visual proof, accessibility certification, arbitrary host-app compatibility, or "
        "design-review approval.\n\n"
        "If the user explicitly needs local React source instead of standalone HTML, change only the compile target:\n\n"
        "```bash\n"
        "viewspec compile viewspec.intent.json --design DESIGN.md --target react-tsx --out react-output/\n"
        "viewspec check react-output/\n"
        "```\n\n"
        "For Tailwind host apps, use `--target react-tailwind-tsx`; Tailwind classes remain compiler-owned recipes. "
        "The ViewSpec repo CI includes a bounded representative React/Tailwind host proof, but that does not mean "
        "every generated artifact has been rendered in a host app; add host-app tests when the repository requires "
        "that assurance.\n\n"
        "When the user explicitly wants ViewSpec's bounded per-artifact React/Tailwind runtime proof, run:\n\n"
        "```bash\n"
        "viewspec verify-host react-tailwind-output/ --target react-tailwind-tsx --install --json\n"
        "viewspec prove --target react-tailwind-tsx --install --out .viewspec-proof --json\n"
        "```\n\n"
        "When comparing IntentBundle revisions, run:\n\n"
        "```bash\n"
        "viewspec diff-intent old.intent.json new.intent.json --json\n"
        "```\n\n"
        "Review `semantic_changes` before inspecting generated artifacts. For concise summaries, use the human "
        "`diff-intent` output, MCP `semantic_summary`, or Python "
        "`intent_semantic_change_lines(diff[\"semantic_changes\"])`.\n\n"
        "For a first narrow multi-screen internal tool contract, use AppBundle JSON instead of writing an app scaffold "
        "or router by hand:\n\n"
        "```bash\n"
        "viewspec init-app --out viewspec.app.json\n"
        "viewspec init-app --resource-binding fixture-readonly-v0 --out viewspec.bound.app.json\n"
        "viewspec validate-app viewspec.app.json --json\n"
        "viewspec diff-app old.app.json new.app.json --json\n"
        "viewspec compile-app viewspec.app.json --out app-dist --target html-tailwind-app --json\n"
        "viewspec prove-app --app viewspec.app.json --out .viewspec-app-proof --with-shell --json\n"
        "```\n\n"
        "When the user wants a runnable React internal tool, use the checked V4 starter and React app target:\n\n"
        "```bash\n"
        "viewspec init-app --template react-app --out viewspec.app.json\n"
        "viewspec compile-app viewspec.app.json --target react-tailwind-app --out app-dist\n"
        "viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install\n"
        "cd app-dist && npm ci && npm run dev\n"
        "```\n\n"
        "Edit `viewspec.app.json` and regenerate with `--force`; do not edit generated React. The generated "
        "`ViewSpecApp` owns browser-history routes, reducer events, resource-backed text, selectors, and visibility, "
        "while authentication, persistence, arbitrary APIs, optimistic updates, and deployment remain host-owned.\n\n"
        "AppBundle schema_version 1 embeds local V1 screen IntentBundles, static canonical routes, and unbound fixture resources. "
        "schema_version 2 adds proof-only `fixture_readonly_v0` resource views that verify exact fixture scalar visibility in declared motifs. "
        "schema_version 3 adds bounded `interactive_state_v0` state, mutations, selectors, replay assertions, and a pure reducer artifact. "
        "Static Shell V0 is available only through `compile-app` or `prove-app --with-shell`; it is a local shell proof "
        "artifact, not a deployable React/Vite/Next app, browser-history proof, live DOM rebinding layer, framework state "
        "adapter, persistence layer, sync runtime, or hosted extended behavior claim.\n\n"
        "The additive `react-tailwind-app` target is a runnable Vite/React/Tailwind host bridge. Its `prove-app` "
        "path checks generated file hashes, the production build, routes, history, mutations, rebinding, selectors, "
        "and visibility in Chromium.\n\n"
        "When the user asks to visually review a ViewSpec IntentBundle or AppBundle, use the durable local Review flow:\n\n"
        "```bash\n"
        "viewspec review viewspec.intent.json --json\n"
        "viewspec review-poll viewspec.intent.json --json\n"
        "viewspec review-poll viewspec.intent.json --ack <batch-id> --agent-reply \"Applied.\" --json\n"
        "viewspec review-end viewspec.intent.json --json\n"
        "```\n\n"
        "Keep the poll attached to the active turn or a completion-aware harness wait; never launch detached "
        "fire-and-forget polling. Deduplicate at-least-once feedback by `event_id`, durably capture a batch before "
        "acknowledging it, edit only semantic source or `DESIGN.md`, and keep human approval distinct from verifier "
        "conformance. Stop after a human-ended review and do not reopen it without an explicit user request.\n\n"
        "For a bounded Review- or verifier-driven source change, author strict IntentPatch V1 JSON against the exact "
        "current source hash, then preview before applying:\n\n"
        "```bash\n"
        "viewspec patch-preview viewspec.intent.json change.intentpatch.json --candidate-out candidate.intent.json --json\n"
        "viewspec patch-apply viewspec.intent.json change.intentpatch.json --approval <exact-preview-token> --json\n"
        "```\n\n"
        "Treat Review feedback and verifier repairs as proposal evidence, never as approval. Inspect the preview's "
        "semantic diff and candidate, obtain explicit approval for that exact token, and do not reuse a token after "
        "the source changes. IntentPatch may replace only its nine declared semantic fields; it cannot address DOM, "
        "CSS, generated files, arbitrary JSON paths, or missing fields.\n\n"
        "For repeated Review or verifier repairs, automatically use Converge Sessions as the controller: start from "
        "exact proposal context, author only from the returned legal operation menu, submit the IntentPatch, and "
        "resume after ViewSpec re-verifies it. The human workflow is only: open Review, inspect semantic before/after "
        "and progress proof, then approve or reject. Never ask the human to manage hashes, task ids, operation names, "
        "or approval tokens; never use `--show-authority`, discover authority from private state, or approve your own "
        "proposal. `converge-start`, `converge-submit`, and `converge-status` are expert/debug equivalents of agent "
        "workflow primitives, not required human commands.\n\n"
        "When comparing imported HTML revisions, run:\n\n"
        "```bash\n"
        "viewspec diff old.html new.html --json\n"
        "```\n\n"
        "Optional local contract assets for schema-aware editors and agents:\n\n"
        "```bash\n"
        f"{AGENT_ASSET_EXPORT_COMMAND}\n"
        f"{AGENT_ASSET_CHECK_COMMAND}\n"
        "```\n\n"
        "This writes `.viewspec/agent-assets.json`, `.viewspec/agent-system-prompt.txt`, "
        "`.viewspec/agent-intent-bundle.schema.json`, `.viewspec/agent-intent-example.dashboard.json`, "
        "`.viewspec/agent-app-bundle.schema.json`, `.viewspec/agent-app-example.internal-tool.json`, "
        "`.viewspec/intent-patch.schema.json`, `.viewspec/intent-patch-example.dashboard.json`, "
        "`.viewspec/converge-task.schema.json`, and `.viewspec/converge-task-example.dashboard.json`. "
        f"The manifest uses agent asset schema version `{AGENT_ASSET_SCHEMA_VERSION}` and declares the "
        f"`{AGENT_ASSET_CONTRACT_PROFILE}` contract profile plus the export/check commands. Run the check command "
        "before reusing cached assets. Use the examples only for valid IntentBundle/AppBundle/IntentPatch/Converge Task wire shape; replace all "
        "sample content with the user's actual intent.\n\n"
        "Check local SDK and agent integration readiness:\n\n"
        "```bash\n"
        "viewspec doctor --agents\n"
        "```\n\n"
        "Rules:\n\n"
        "- `viewspec.intent.json` is editable source; compiled output directories such as `dist/` or `react-output/` contain generated artifacts.\n"
        "- If starting from a blank repo, `viewspec init-intent --out viewspec.intent.json` may be used only as a scaffold; replace all sample content with the user's actual intent.\n"
        "- If starting a multi-screen internal tool, `viewspec init-app --out viewspec.app.json` may be used only as a scaffold; keep routes static, use `--resource-binding fixture-readonly-v0` only for exact scalar visibility proofs, and use `viewspec compile-app` instead of hand-writing a router.\n"
        "- For a runnable React internal tool, prefer `viewspec init-app --template react-app`, change only `viewspec.app.json`, and regenerate `react-tailwind-app` output instead of patching generated React.\n"
        "- Use `viewspec prove --out .viewspec-proof` for a quick first proof before broader manual review; read `.viewspec-proof/PROOF.md` first and keep `.viewspec-proof/support_bundle.json` for redacted failure triage.\n"
        "- Use at most one view-level aesthetic profile token when the user asks for governed art direction; do not invent CSS or multiple profiles.\n"
        "- If `DESIGN.md` is missing, run `viewspec init-design --out DESIGN.md` before compiling.\n"
        "- If validation fails, regenerate the full IntentBundle using the correction prompt.\n"
        "- Use raw HTML tools only when importing existing HTML.\n"
        "- For visual review, prefer `viewspec review`; acknowledge a feedback batch only after its event ids and content are durably captured.\n"
        "- For an approved bounded revision, use `patch-preview` and pass only its exact current approval token to `patch-apply`; never treat feedback or verifier output as approval.\n"
        "- For repeated bounded revisions, automatically use Converge Session proposal/status tools and leave every approval or rejection to the human in Review.\n"
        "- Never patch or recursively compile generated artifacts such as `dist/index.html` or `react-output/ViewSpecView.tsx`.\n"
        "- Do not upload, share, call hosted APIs, or use remote services unless the user explicitly asks.\n"
        "- Use `DESIGN.md` only through ViewSpec. Do not inject arbitrary CSS, HTML, or script from it.\n"
        "- If `viewspec check` fails, fix `viewspec.intent.json` or `DESIGN.md`, then re-run validate, compile, and check.\n"
        f"{END_MARKER}\n"
    )


def _merge_instruction_block(current: str, block: str, path: Path) -> tuple[str, str]:
    begin_count = current.count(BEGIN_MARKER)
    end_count = current.count(END_MARKER)
    if begin_count != end_count or begin_count > 1:
        raise NativeAgentError(
            "MARKER_CONFLICT",
            f"{path} has malformed or duplicated ViewSpec instruction markers; no files were changed.",
        )
    if begin_count == 0:
        separator = "" if not current else "\n" if current.endswith("\n") else "\n\n"
        return f"{current}{separator}{block}", "append"
    begin = current.find(BEGIN_MARKER)
    end = current.find(END_MARKER)
    if end < begin:
        raise NativeAgentError(
            "MARKER_CONFLICT",
            f"{path} has reversed ViewSpec instruction markers; no files were changed.",
        )
    end += len(END_MARKER)
    if current.startswith("\r\n", end):
        end += 2
    elif end < len(current) and current[end] in "\r\n":
        end += 1
    next_text = f"{current[:begin]}{block}{current[end:]}"
    return (next_text, "unchanged") if next_text == current else (next_text, "replace")


def _expand_targets(target: str) -> tuple[str, ...]:
    if target == "all":
        return tuple(TARGET_PATHS)
    if target not in TARGET_PATHS:
        raise ValueError(f"Unknown init-agent target: {target}")
    return (target,)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


__all__ = [
    "BEGIN_MARKER",
    "END_MARKER",
    "TARGET_PATHS",
    "VALID_TARGETS",
    "NativeAgentError",
    "agent_instruction_block",
    "init_agent_instructions",
    "plan_agent_instruction_changes",
]
