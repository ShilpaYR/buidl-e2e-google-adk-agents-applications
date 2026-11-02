#!/usr/bin/env python3
"""Warm-up: one short LIVE conversation per agent, with PASS/FAIL per agent.

    python scripts/warmup.py                 # all agents
    python scripts/warmup.py --only c e      # some agents
    python scripts/warmup.py --pick-model    # find a Gemini model your key can use and write it to every .env

Each agent runs in its own subprocess, from its own folder, so it loads its own
.env exactly as `adk web` would. A run passes when the expected tool was called,
that tool did not report an error, and the agent produced a final answer.
A report is written to .run/warmup_report.md.

WARMUP_FAKE=1 swaps Gemini for the scripted test model (plumbing check, no API key).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIRS = {
    "a": ("a_lead_generation_agent", "lead_generation_agent"),
    "b": ("b_gemini_cli_tool_agent", "gemini_cli_agent"),
    "c": ("c_mcp_bug_assistant", "bug_assistant"),
    "d": ("d_code_review_assistant", "code_review_assistant"),
    "e": ("e_ecommerce_agent", "finn_agent"),
}
DFS = """def dfs_search_v1(graph, start, target):
    visited = set()
    stack = start
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current not in visited:
            visited.add(current)
            for neighbor in graph[current]:
                if neighbor not in visited:
                    stack.append(neighbor)
    return False"""


@dataclass
class Spec:
    title: str
    prompt: str
    expect_any: list[str]
    max_llm_calls: int
    timeout_s: int
    fake_calls: list[tuple[str, dict]] = field(default_factory=list)


SPECS = {
    "a": Spec("(a) Lead generation", "Find fintech leads in Thailand.",
              ["intent_extractor_agent"], 12, 240,
              [("intent_extractor_agent", {"request": "Find fintech leads in Thailand."})]),
    "b": Spec("(b) Gemini CLI tool", "Using Gemini CLI, explain in 5 short bullets what "
              "https://github.com/derrickchwong/gemini-cli-on-adk does and how its gemini_cli tool works.",
              ["gemini_cli"], 10, 600,
              [("gemini_cli", {"task": "Explain this codebase in 5 bullets",
                               "github_url": "https://github.com/derrickchwong/gemini-cli-on-adk"})]),
    "c": Spec("(c) MCP bug assistant", "Users say the login page freezes after 3 wrong passwords. "
              "Is there already a ticket for this in our ticket database?",
              ["search-tickets", "get-tickets-by-status"], 12, 240,
              [("search-tickets", {"query": "login page freezes after failed attempts"})]),
    "d": Spec("(d) Code review", f"Please review this code:\n```python\n{DFS}\n```",
              ["check_code_style"], 40, 420,
              [("transfer_to_agent", {"agent_name": "CodeReviewPipeline"}),
               ("analyze_code_structure", {}), ("check_code_style", {})]),
    "e": Spec("(e) Finn e-commerce", "I'm looking for running shoes for an ultra-trail.",
              ["search-products-by-description"], 10, 240,
              [("search-products-by-description", {"description": "running shoes for an ultra-trail"})]),
}


# ------------------------------------------------------------------ one agent (child process)
async def run_one(key: str) -> dict:
    folder, package = AGENT_DIRS[key]
    spec = SPECS[key]
    if os.getenv("WARMUP_SIMULATE_ERROR"):  # test hook for the error/quota handling
        raise RuntimeError(os.environ["WARMUP_SIMULATE_ERROR"])
    os.chdir(ROOT / folder)
    sys.path[:0] = [str(ROOT / folder), str(ROOT)]

    from google.adk.agents.run_config import RunConfig
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    agent = __import__(f"{package}.agent", fromlist=["root_agent"]).root_agent

    if os.getenv("WARMUP_FAKE") == "1":
        from common.adk_testing import ScriptedLlm, call_response, declared_tools, last_part, swap_models, text_response

        def responder(req):
            part = last_part(req)
            if part and part.function_response:
                return text_response("Done: " + json.dumps(part.function_response.response, default=str)[:300])
            tools = declared_tools(req)
            for name, args in spec.fake_calls:
                if name in tools:
                    return call_response(name, args)
            return None

        swap_models(agent, ScriptedLlm(requests=[], responder=responder))

    runner = InMemoryRunner(agent=agent, app_name="warmup")
    session = await runner.session_service.create_session(app_name="warmup", user_id="warmup")
    called, errors, final = [], [], ""
    message = types.Content(role="user", parts=[types.Part(text=spec.prompt)])
    async for event in runner.run_async(user_id="warmup", session_id=session.id, new_message=message,
                                        run_config=RunConfig(max_llm_calls=spec.max_llm_calls)):
        for fc in event.get_function_calls():
            called.append(fc.name)
        for fr in event.get_function_responses():
            resp = fr.response or {}
            if fr.name in spec.expect_any and isinstance(resp, dict) and resp.get("status") == "error":
                errors.append(f"{fr.name}: {resp.get('message', resp)}")
        if event.content and event.content.parts and not event.get_function_calls():
            text = "".join(p.text or "" for p in event.content.parts if not p.thought)
            if text.strip():
                final = text
    await runner.close() if hasattr(runner, "close") else None
    return {"called": called, "errors": errors, "final": final}


# ------------------------------------------------------------------ model picker
def pick_model() -> int:
    if os.getenv("WARMUP_FAKE") == "1":
        print("WARMUP_FAKE=1: skipping model check")
        return 0
    sys.path.insert(0, str(ROOT))
    from dotenv import dotenv_values
    from google import genai

    env = dotenv_values(ROOT / "a_lead_generation_agent" / ".env")
    api_key = os.getenv("GOOGLE_API_KEY") or env.get("GOOGLE_API_KEY")
    if not api_key:
        print("No GOOGLE_API_KEY found (env or a_lead_generation_agent/.env).")
        return 1
    client = genai.Client(api_key=api_key)
    available = [m.name.removeprefix("models/") for m in client.models.list()
                 if "generateContent" in (m.supported_actions or [])]
    preferred = [env.get("FAST_MODEL"), "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                 "gemini-3.5-flash-lite", "gemini-2.5-flash"]
    candidates = [m for m in preferred if m and m in available]
    candidates += sorted((m for m in available if "flash" in m and "image" not in m and "tts" not in m
                          and "live" not in m and m not in candidates), reverse=True)
    for model in candidates:
        try:
            client.models.generate_content(model=model, contents="Reply with the single word: ok")
        except Exception as exc:  # quota / permission / not served
            print(f"  {model}: not usable ({str(exc)[:80]})")
            continue
        print(f"Using {model}")
        for folder, _ in AGENT_DIRS.values():
            path = ROOT / folder / ".env"
            if not path.exists():
                continue
            lines = [l for l in path.read_text().splitlines()
                     if not l.startswith(("FAST_MODEL=", "ADVANCED_MODEL="))]
            lines += [f"FAST_MODEL={model}", f"ADVANCED_MODEL={model}"]
            path.write_text("\n".join(lines) + "\n")
        return 0
    print("No usable Gemini model found for this key. Check the key at https://aistudio.google.com/apikey")
    return 1


# ------------------------------------------------------------------ parent
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", choices=sorted(AGENT_DIRS), default=sorted(AGENT_DIRS))
    parser.add_argument("--pick-model", action="store_true")
    parser.add_argument("--child", choices=sorted(AGENT_DIRS), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.pick_model:
        return pick_model()
    if args.child:
        try:
            result = asyncio.run(run_one(args.child))
        except BaseException as exc:  # report the real cause, not trailing library warnings
            import traceback
            log = ROOT / ".run" / f"warmup-{args.child}.log"
            log.parent.mkdir(exist_ok=True)
            log.write_text("".join(traceback.format_exception(exc)))
            msg = f"{type(exc).__name__}: {exc}"
            if "API key not valid" in msg or "API_KEY_INVALID" in msg:
                msg += "  → check GOOGLE_API_KEY in this agent's .env"
            elif "PERMISSION_DENIED" in msg:
                msg += f"  → full trace: .run/warmup-{args.child}.log"
            elif "Proxy" in msg or "ClientConnector" in msg or "Cannot connect" in msg:
                msg += "  → no connection to the Gemini API (internet / VPN / firewall)"
            elif any(t in msg for t in ("RESOURCE_EXHAUSTED", "ResourceExhausted", "429")):
                msg = ("QUOTA: Gemini free-tier limit reached for this key. Per-minute limits clear in ~1 min; "
                       "daily limits reset at midnight Pacific. See https://aistudio.google.com/rate-limit")
            elif "404" in msg and "model" in msg.lower():
                msg += "  → run: python scripts/warmup.py --pick-model"
            result = {"called": [], "errors": [msg[:600]], "final": ""}
        print("WARMUP_RESULT " + json.dumps(result), flush=True)
        return 0

    rows, details = [], []
    quota_stop = False
    for key in args.only:
        spec, folder = SPECS[key], AGENT_DIRS[key][0]
        if quota_stop:  # don't spend more of a used-up quota
            print(f"→ {spec.title} … skipped (quota)")
            rows.append(f"| {spec.title} | SKIPPED | — | quota used up; run later with --only {key} |")
            continue
        print(f"→ {spec.title} …", flush=True)
        started = time.monotonic()
        for attempt in (1, 2):  # one retry when Google is busy (503) or rate-limiting (429)
            try:
                child_env = {**os.environ, "GEMINI_RETRY_ATTEMPTS": os.getenv("GEMINI_RETRY_ATTEMPTS", "3")}
                proc = subprocess.run([sys.executable, __file__, "--child", key], cwd=ROOT / folder,
                                      capture_output=True, text=True, timeout=spec.timeout_s, env=child_env)
                line = next((l for l in proc.stdout.splitlines() if l.startswith("WARMUP_RESULT ")), None)
                if line is None:
                    out = (proc.stderr or proc.stdout).strip()
                    raise RuntimeError(out.splitlines()[-1] if out else "no output")
                result = json.loads(line.removeprefix("WARMUP_RESULT "))
            except subprocess.TimeoutExpired:
                result = {"called": [], "errors": [f"timed out after {spec.timeout_s}s"], "final": ""}
            except Exception as exc:
                result = {"called": [], "errors": [str(exc)[:400]], "final": ""}
            quota = any(e.startswith("QUOTA:") for e in result["errors"])
            busy = any(t in e for e in result["errors"] for t in ("503", "UNAVAILABLE"))
            if attempt == 1 and (busy or quota):
                wait = int(os.getenv("WARMUP_RETRY_WAIT", "65" if quota else "30"))
                print(f"   Gemini {'quota hit' if quota else 'is busy'} — retrying once in {wait} s …", flush=True)
                time.sleep(wait)
                continue
            break
        secs = time.monotonic() - started

        hit = [t for t in spec.expect_any if t in result["called"]]
        problems = list(result["errors"])
        if not hit and not problems:
            problems.append(f"expected a call to {' or '.join(spec.expect_any)}; called {result['called'] or 'nothing'}")
        if not result["final"].strip() and not problems:
            problems.append("no final answer")
        status = "PASS" if not problems else "FAIL"
        print(f"   {status} in {secs:.0f}s — tools: {', '.join(dict.fromkeys(result['called'])) or 'none'}")
        for p in problems:
            short = " | ".join(l.strip() for l in str(p).splitlines()[:2])
            print(f"   ! {short[:300]}")
        if status == "FAIL" and any(str(p).startswith("QUOTA:") for p in problems):
            quota_stop = True
        rows.append(f"| {spec.title} | {status} | {secs:.0f} s | {', '.join(dict.fromkeys(result['called'])) or '—'} |")
        details.append(f"### {spec.title} — {status}\n\n**Prompt:** {spec.prompt.splitlines()[0]}\n\n"
                       + ("".join(f"- problem: {p}\n" for p in problems) + "\n" if problems else "")
                       + f"**Answer (first 1,500 chars):**\n\n{result['final'][:1500] or '_none_'}\n")

    report = ROOT / ".run" / "warmup_report.md"
    report.parent.mkdir(exist_ok=True)
    report.write_text("# Warm-up report\n\n| Agent | Result | Time | Tools called |\n| --- | --- | --- | --- |\n"
                      + "\n".join(rows) + "\n\n" + "\n".join(details))
    passed = sum("| PASS |" in r for r in rows)
    print(f"\n{passed}/{len(rows)} agents passed. Report: {report}")
    if quota_stop:
        todo = " ".join(k for k, r in zip(args.only, rows) if "| PASS |" not in r)
        print("Quota used up. Check https://aistudio.google.com/rate-limit — after it resets (daily limits:\n"
              f"midnight Pacific) run only the remaining agents:  .venv/bin/python scripts/warmup.py --only {todo}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
