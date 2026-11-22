"""Analyze successful and failed attempts without conflating their costs."""

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

import tiktoken

root = Path(sys.argv[1]).resolve()
encoding = tiktoken.get_encoding("o200k_base")
by = defaultdict(list)
rows = []
for result in sorted(root.glob("*/result.json")):
    row = json.loads(result.read_text())
    row["suite"] = (
        "Books revised"
        if row.get("protocol") == "books-evidence-v1" and row["task"] in {"wa-68", "wa-69"}
        else "WebArena"
        if row["task"].startswith("wa-")
        else "Fixture"
    )
    records = [json.loads(line) for line in (result.parent / "tools.jsonl").read_text().splitlines()]
    row["forwarded_calls"] = sum("response" in record for record in records)
    row["rejected_calls"] = sum(bool(record.get("rejected")) for record in records)
    row["input"] = row["usage"].get("input_tokens")
    row["cached_input"] = row["usage"].get("cached_input_tokens")
    row["output"] = row["usage"].get("output_tokens")
    row["total"] = row["input"] + row["output"] if row["input"] is not None and row["output"] is not None else None
    tool_bytes = []
    for e in (result.parent / "codex.jsonl").read_text().splitlines():
        try:
            event = json.loads(e)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call" and item.get("result"):
            # Count the response payload once in its logged, compact JSON representation.
            tool_bytes.append(
                len(encoding.encode_ordinary(json.dumps(item["result"], ensure_ascii=False, separators=(",", ":"))))
            )
    row["response_tokens"] = sum(tool_bytes)
    rows.append(row)
    by[row["tool"], row["suite"]].append(row)
    by[row["tool"], "Overall"].append(row)
for key, group in sorted(by.items()):
    completed = [r for r in group if r["success"]]
    measured = [r for r in completed if r["total"] is not None]

    def med(field, completed=completed):
        values = [r[field] for r in completed if r[field] is not None]
        return round(median(values), 2) if values else None

    total_consumed = sum(r["total"] or 0 for r in group)
    print(
        *key,
        f"{len(completed)}/{len(group)}",
        "false_success",
        sum(r["false_success"] for r in group),
        "unintended",
        sum(r["unintended_writes"] for r in group),
        "infra",
        sum(r["infra"] for r in group),
        "timeout",
        sum(r["timed_out"] for r in group),
        "outside",
        sum(bool(r["outside"]) for r in group),
        "medians",
        {field: med(field) for field in ("total", "response_tokens", "calls", "seconds")},
        "budget per success",
        round(total_consumed / len(completed)) if completed else None,
        "usage_rows",
        len(measured),
    )
    print(dict(sorted(Counter(r["task"] for r in completed).items())))
out = root.parent / "results"
out.mkdir(exist_ok=True)
keys = (
    "protocol",
    "tool",
    "suite",
    "task",
    "trial",
    "success",
    "false_success",
    "unintended_writes",
    "seconds",
    "calls",
    "forwarded_calls",
    "rejected_calls",
    "reference_match",
    "schema_valid",
    "input",
    "cached_input",
    "output",
    "total",
    "response_tokens",
    "infra",
    "timed_out",
)
with (out / "attempts.csv").open("w", newline="") as file:
    writer = csv.DictWriter(file, keys, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
with (out / "attempts.jsonl").open("w") as file:
    for row in rows:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")
