"""Offline test harness shared by all five agents.

`ScriptedLlm` is a drop-in `BaseLlm` that never calls Google. It lets the unit
tests execute the *real* ADK agent graphs (Sequential / Parallel / Loop agents,
callbacks, state hand-offs, tools) deterministically and for free.

Default behaviour:
  * If the request carries a response schema (an agent with `output_schema`),
    it returns JSON that satisfies that schema.
  * Otherwise it returns a short text reply.
A test can pass `responder=` to script function calls or specific answers.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Callable, Optional

from google.adk.agents import BaseAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

Responder = Callable[[LlmRequest], Optional[LlmResponse]]


def text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def call_response(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
        )
    )


def system_text(req: LlmRequest) -> str:
    si = req.config.system_instruction if req.config else None
    if si is None:
        return ""
    if isinstance(si, str):
        return si
    if isinstance(si, types.Content):
        return "".join(p.text or "" for p in si.parts or [])
    return str(si)


def last_part(req: LlmRequest) -> Optional[types.Part]:
    for content in reversed(req.contents or []):
        for part in reversed(content.parts or []):
            return part
    return None


def declared_tools(req: LlmRequest) -> list[str]:
    return list((req.tools_dict or {}).keys())


# ---------------------------------------------------------------- schema -> sample
def _sample(schema: dict, defs: dict, depth: int = 0) -> Any:
    if "$ref" in schema:
        return _sample(defs[schema["$ref"].split("/")[-1]], defs, depth)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"]
            return _sample(options[0], defs, depth) if options else None
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {k: _sample(v, defs, depth + 1) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [_sample(schema.get("items", {"type": "string"}), defs, depth + 1) for _ in range(2)]
    if kind == "integer":
        return 1
    if kind == "number":
        return 0.9
    if kind == "boolean":
        return True
    title = schema.get("title", "value").lower().replace(" ", "_")
    if "url" in title or "source" in title:
        return "https://example.com/source"
    return f"sample_{title}"


def sample_for_schema(response_schema: Any) -> Any:
    if hasattr(response_schema, "model_json_schema"):
        js = response_schema.model_json_schema()
    elif isinstance(response_schema, dict):
        js = response_schema
    else:  # google.genai.types.Schema
        js = response_schema.model_dump(exclude_none=True)
    return _sample(js, js.get("$defs", {}))


# ---------------------------------------------------------------- fake model
class ScriptedLlm(BaseLlm):
    """A deterministic fake Gemini. `model` keeps a Gemini-looking name so
    built-in tools (google_search, code execution) accept it."""

    model: str = "gemini-3.8-flash"
    responder: Optional[Responder] = None
    requests: list = []

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"scripted-.*"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        response = self.responder(llm_request) if self.responder else None
        if response is None:
            schema = llm_request.config.response_schema if llm_request.config else None
            if schema is not None:
                response = text_response(json.dumps(sample_for_schema(schema)))
            else:
                response = text_response("OK")
        yield response


MODEL_ATTRS = ("model", "fast_model", "advanced_model", "worker_model", "critic_model")


def swap_models(agent: BaseAgent, llm: BaseLlm) -> None:
    """Recursively replace every model reference in an agent tree."""
    for attr in MODEL_ATTRS:
        if attr in type(agent).model_fields:
            setattr(agent, attr, llm)
    for tool in getattr(agent, "tools", []) or []:
        inner = getattr(tool, "agent", None)  # AgentTool
        if isinstance(inner, BaseAgent):
            swap_models(inner, llm)
    for sub in agent.sub_agents:
        swap_models(sub, llm)


async def run_agent(agent: BaseAgent, message: str, state: Optional[dict] = None,
                    app_name: str = "test_app"):
    """Run an agent once with an in-memory runner. Returns (events, final_state)."""
    from google.adk.runners import InMemoryRunner

    runner = InMemoryRunner(agent=agent, app_name=app_name)
    session = await runner.session_service.create_session(
        app_name=app_name, user_id="tester", state=state or {}
    )
    events = []
    async for event in runner.run_async(
        user_id="tester",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        events.append(event)
    final = await runner.session_service.get_session(
        app_name=app_name, user_id="tester", session_id=session.id
    )
    return events, final.state, runner, session.id
