"""Forward and log each native MCP request within the benchmark's tool scope."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

config = json.loads(Path(sys.argv[1]).read_text())
os.environ.update(config["env"])
out = Path(config["log"]).open("a", buffering=1)  # noqa: SIM115 -- closed with the backend in finally
backend = subprocess.Popen(config["cmd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=config["cwd"])
prefix = config["prefix"]
allowed = set(config["tools"])
blocked_args = {"extraArgs", "session", "cwd", "filename", "options", "paths"}
calls = 0
backend_tools = {}


def result(message):
    print(json.dumps(message, ensure_ascii=False), flush=True)


try:
    for line in sys.stdin.buffer:
        incoming = json.loads(line)
        method = incoming.get("method", "")
        if method == "tools/call":
            calls += 1
            params = incoming["params"]
            name = params["name"]
            args = params.get("arguments", {})
            url = args.get("url")
            target_ok = not url or (urlsplit(url).scheme, urlsplit(url).netloc) == (
                "http",
                urlsplit(config["origin"]).netloc,
            )
            blocked = name not in backend_tools or not target_ok or args.keys() & blocked_args
            if calls > config["budget"] or blocked:
                result(
                    {
                        "jsonrpc": "2.0",
                        "id": incoming["id"],
                        "result": {
                            "isError": True,
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Browser tool budget exhausted."
                                    if calls > config["budget"]
                                    else "Tool or arguments outside the benchmark scope.",
                                }
                            ],
                        },
                    }
                )
                out.write(json.dumps({"tool": name, "arguments": args, "rejected": True}) + "\n")
                continue
        backend.stdin.write(line)
        backend.stdin.flush()
        if "id" not in incoming:
            continue
        for response in backend.stdout:
            message = json.loads(response)
            if message.get("id") == incoming["id"]:
                break
        else:
            raise RuntimeError("Native MCP server exited without replying")
        if method == "tools/list":
            tools = message.get("result", {}).get("tools", [])
            tools = [tool for tool in tools if tool["name"].removeprefix(prefix) in allowed]
            for tool in tools:
                properties = tool.get("inputSchema", {}).get("properties", {})
                for key in blocked_args:
                    properties.pop(key, None)
                backend_tools[tool["name"]] = tool
            message["result"]["tools"] = tools
        if method == "tools/call":
            out.write(
                json.dumps({"tool": params["name"], "arguments": args, "response": message}, ensure_ascii=False) + "\n"
            )
        result(message)
finally:
    out.close()
    backend.stdin.close()
    try:
        backend.wait(5)
    except subprocess.TimeoutExpired:
        backend.terminate()
        backend.wait(5)
