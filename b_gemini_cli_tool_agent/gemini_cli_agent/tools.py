"""Tools that wrap the Gemini CLI (headless mode) and git.

Hardening compared with the reference implementation:
  * no `shell=True` — arguments are passed as a list, so a prompt or URL can't
    inject shell commands;
  * only https://github.com/<owner>/<repo> URLs are accepted;
  * shallow clones into an isolated workspace, keyed by owner/repo;
  * Gemini CLI runs with `--approval-mode plan` (read-only) by default — it can
    read the code but not edit files or run commands unless you opt in;
  * `-o json` output is parsed, errors are returned as structured dicts;
  * timeouts and output truncation protect the agent's context window;
  * async subprocesses, so the ParallelAgent analysts really run concurrently.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from google.adk.tools import ToolContext

WORKSPACE = Path(os.getenv("GEMINI_CLI_WORKSPACE", "/tmp/gemini_cli_workspace"))
GEMINI_BIN = os.getenv("GEMINI_CLI_BIN", "gemini")
APPROVAL_MODE = os.getenv("GEMINI_CLI_APPROVAL_MODE", "plan")  # plan | default | auto_edit | yolo
CLI_MODEL = os.getenv("GEMINI_CLI_MODEL", "")  # empty = Gemini CLI default
CLI_TIMEOUT_S = int(os.getenv("GEMINI_CLI_TIMEOUT", "600"))
CLONE_TIMEOUT_S = 300
MAX_OUTPUT_CHARS = int(os.getenv("GEMINI_CLI_MAX_OUTPUT", "20000"))

GITHUB_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def parse_github_url(url: str) -> Optional[tuple[str, str]]:
    match = GITHUB_URL.match((url or "").strip())
    if not match or ".." in match.group(0):
        return None
    return match.group(1), match.group(2)


async def _run(cmd: list[str], cwd: Optional[Path], timeout: int) -> tuple[int, str, str]:
    env = os.environ.copy()
    if env.get("GEMINI_API_KEY"):
        env.pop("GOOGLE_API_KEY", None)  # Gemini CLI warns (and prefers GOOGLE_API_KEY) when both are set
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n\n[... truncated {len(text) - MAX_OUTPUT_CHARS} characters]"


async def clone_repository(github_url: str, tool_context: ToolContext) -> dict:
    """Shallow-clone a public GitHub repository so Gemini CLI can analyse it.

    Args:
        github_url: Repository URL, e.g. https://github.com/google/adk-python

    Returns:
        status, local path and a short overview of the repository.
    """
    parsed = parse_github_url(github_url)
    if not parsed:
        return {"status": "error", "message": "Only https://github.com/<owner>/<repo> URLs are allowed."}
    owner, repo = parsed
    target = WORKSPACE / f"{owner}__{repo}"
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    if not (target / ".git").exists():
        shutil.rmtree(target, ignore_errors=True)
        try:
            code, _, err = await _run(
                ["git", "clone", "--depth", "1", f"https://github.com/{owner}/{repo}.git", str(target)],
                None, CLONE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return {"status": "error", "message": "git clone timed out."}
        if code != 0:
            return {"status": "error", "message": f"git clone failed: {err.strip()[:500]}"}

    files = [p for p in target.rglob("*") if p.is_file() and ".git" not in p.parts]
    by_ext: dict[str, int] = {}
    for p in files:
        by_ext[p.suffix or "(none)"] = by_ext.get(p.suffix or "(none)", 0) + 1
    top_level = sorted(p.name for p in target.iterdir() if p.name != ".git")[:40]

    tool_context.state["repo_url"] = f"https://github.com/{owner}/{repo}"
    tool_context.state["repo_path"] = str(target)
    tool_context.state["repo_name"] = f"{owner}/{repo}"
    return {
        "status": "ok",
        "repo": f"{owner}/{repo}",
        "local_path": str(target),
        "file_count": len(files),
        "top_level_entries": top_level,
        "file_types": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])[:10]),
    }


async def gemini_cli(task: str, tool_context: ToolContext, github_url: str = "") -> dict:
    """Run Gemini CLI in headless mode inside the cloned repository.

    Gemini CLI explores the codebase itself (reads files, greps, follows
    imports) and answers the task with full-repository context.

    Args:
        task: What to do, e.g. "Explain the architecture of this codebase".
        github_url: Optional repository URL. If omitted, the repository cloned
            earlier in this session is used.

    Returns:
        status and Gemini CLI's response text.
    """
    if github_url:
        cloned = await clone_repository(github_url, tool_context)
        if cloned["status"] != "ok":
            return cloned
    repo_path = tool_context.state.get("repo_path")
    if not repo_path or not Path(repo_path).exists():
        return {"status": "error", "message": "No repository cloned yet. Provide a GitHub URL first."}
    if shutil.which(GEMINI_BIN) is None:
        return {"status": "error", "message": f"'{GEMINI_BIN}' not found. Install: npm install -g @google/gemini-cli"}

    cmd = [GEMINI_BIN, "-p", task, "-o", "json", "--approval-mode", APPROVAL_MODE, "--skip-trust"]
    if CLI_MODEL:
        cmd += ["-m", CLI_MODEL]
    try:
        code, out, err = await _run(cmd, Path(repo_path), CLI_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"status": "error", "message": f"Gemini CLI timed out after {CLI_TIMEOUT_S}s."}

    payload = None
    for stream in (out, err):  # success JSON is on stdout; Gemini CLI writes error JSON to stderr
        try:
            payload = json.loads(stream)
            break
        except json.JSONDecodeError:
            continue

    if isinstance(payload, dict) and payload.get("error"):
        return {"status": "error", "message": payload["error"].get("message", "Gemini CLI error")}
    if code != 0 and not (isinstance(payload, dict) and payload.get("response")):
        lines = [l for l in (err or out).strip().splitlines() if l.strip() and not l.lstrip().startswith(("at ", "Warning:"))]
        return {"status": "error", "message": "\n".join(lines[:6])[:1500]}  # drop JS stack frames

    response = payload.get("response", "") if isinstance(payload, dict) else out
    result = {"status": "ok", "repo": tool_context.state.get("repo_name"), "response": _truncate(response)}
    stats = payload.get("stats") if isinstance(payload, dict) else None
    if isinstance(stats, dict):
        result["stats"] = {k: v for k, v in stats.items() if k in ("models", "tools", "files")}
    return result
