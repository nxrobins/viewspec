"""Local agent handoff text and accessible copy controls shared by Studio rooms."""

from __future__ import annotations

import shlex
from pathlib import Path

AGENT_ACTIVITY_SECONDS = 60


def creation_handoff(root: Path, task_path: Path, state_root: Path) -> str:
    command = shlex.join(
        [
            "python",
            "-m",
            "viewspec",
            "studio-agent-task",
            str(task_path),
            "--state-dir",
            str(state_root),
            "--json",
        ]
    )
    return (
        "Continue this local ViewSpec Studio creation in the project below.\n"
        f"cd -- {shlex.quote(str(root))}\n{command}\n\n"
        "Run that command now and again at least every 60 seconds while working. It checks you in "
        "and returns the preserved task, exact brief, optional local reference, candidate path and schema. "
        "Read the task and author its candidate for the brief; do not edit the task or copy unchanged starter content. "
        "Save only the candidate. Studio checks it automatically and opens the checked product in the same tab. "
        "If a check fails, fix the candidate and save again. Preserve the brief and reference; do not start another task. "
        "Tell me if you cannot access this project or run the command."
    )


def review_handoff(source: Path, state_root: Path, convergence_state_root: str | None) -> str:
    command = shlex.join(
        [
            "python",
            "-m",
            "viewspec",
            "review-poll",
            str(source),
            "--state-dir",
            str(state_root),
            "--timeout-ms",
            "30000",
            "--json",
        ]
    )
    convergence = (
        f" Use {shlex.join(['--state-dir', convergence_state_root])} for Converge commands."
        if convergence_state_root is not None
        else ""
    )
    return (
        "Continue this existing local ViewSpec Studio review.\n"
        f"cd -- {shlex.quote(str(source.parent))}\n{command}\n\n"
        "Keep polling after timeouts; do not open a second session. A returned batch may already have been delivered: "
        "resume it without duplicating changes. Use its exact source and semantic identities to propose a bounded "
        "change through converge-start and converge-submit; inspect those commands with --help if needed."
        f"{convergence} Leave approval to me in Studio. Do not edit generated HTML or React output. "
        "Acknowledge the exact batch with review-poll --ack and a brief --agent-reply when handled, then keep polling. "
        "An unchanged repeat poll preserves the outstanding batch. Tell me if you cannot access the project or resume it."
    )


HANDOFF_HTML = """
<dialog id=agent-handoff aria-labelledby=agent-handoff-title>
<h2 id=agent-handoff-title>Continue with your coding agent</h2>
<p>Open this project in your coding agent, then paste these instructions. Studio does not start an agent for you.</p>
<p class=handoff-disclosure>Includes this local project's location and task or session commands. No access tokens.</p>
<label for=agent-instructions>Instructions for your agent</label>
<textarea id=agent-instructions readonly rows=10></textarea>
<p id=agent-copy-status role=status aria-live=polite></p>
<div class=handoff-actions><button id=agent-copy type=button>Copy agent instructions</button>
<button id=agent-close type=button>Close instructions</button></div>
</dialog>
"""

HANDOFF_CSS = """
#agent-handoff{width:min(620px,calc(100% - 32px));max-height:calc(100% - 32px);overflow:auto;
padding:24px;border:1px solid #29313d;border-radius:16px;background:#10141a;color:#f7f8fa;
font:14px/1.5 system-ui,sans-serif;box-sizing:border-box}
#agent-handoff::backdrop{background:rgba(0,0,0,.65)}#agent-handoff h2{font-size:22px;margin:0}
#agent-handoff p{color:#b9c2ce}.handoff-disclosure{font-size:12px}
#agent-handoff label{display:block;margin-bottom:6px}#agent-instructions{display:block;width:100%;
box-sizing:border-box;padding:12px;border:1px solid #465367;border-radius:8px;background:#080a0d;
color:#f7f8fa;font:13px/1.5 system-ui,sans-serif;resize:vertical;overflow-wrap:anywhere}
.handoff-actions{display:flex;flex-wrap:wrap;gap:8px}.handoff-actions button,#agent-connect{
min-height:40px;padding:8px 14px;border:1px solid #465367;border-radius:8px;background:#151b23;
color:#f7f8fa;font:inherit;cursor:pointer}#agent-copy{background:#ffbd4a;color:#211400;font-weight:700}
#agent-handoff button:focus-visible,#agent-instructions:focus-visible,#agent-connect:focus-visible{
outline:2px solid #ffbd4a;outline-offset:2px}#agent-recovery{color:#b9c2ce;font-size:13px}
"""


def handoff_script(endpoint: str) -> str:
    # Endpoint is server-owned, never derived from user input.
    import json

    return """
(()=>{const dialog=document.getElementById('agent-handoff'),connect=document.getElementById('agent-connect'),
text=document.getElementById('agent-instructions'),copy=document.getElementById('agent-copy'),
status=document.getElementById('agent-copy-status');
connect.addEventListener('click',async()=>{dialog.showModal();text.value='';copy.disabled=true;
status.textContent='Preparing instructions…';try{const response=await fetch(ENDPOINT,{cache:'no-store'});
if(!response.ok)throw new Error();const result=await response.json();text.value=result.instructions;
copy.disabled=false;status.textContent='Paste these into the agent for this project.';}catch{
status.textContent='Studio could not prepare the handoff. Close this panel and try again.';}});
copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(text.value);
status.textContent='Copied. Paste into your coding agent to continue.';}catch{text.focus();text.select();
status.textContent='Copy is unavailable. The instructions are selected; press Ctrl+C or ⌘C, then paste into your agent.';}});
document.getElementById('agent-close').addEventListener('click',()=>dialog.close());
dialog.addEventListener('close',()=>connect.focus());})();
""".replace("ENDPOINT", json.dumps(endpoint))
