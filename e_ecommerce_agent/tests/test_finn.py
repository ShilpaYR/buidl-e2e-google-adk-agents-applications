"""Unit tests + end-to-end tests (browser API → ADK → MCP Toolbox → PostgreSQL).
The e2e tests need: toolbox --config toolbox/tools.local.yaml --port 5001"""

import json
import urllib.request
from pathlib import Path

import httpx
import pytest
import yaml

from common.adk_testing import ScriptedLlm, call_response, declared_tools, last_part, swap_models, text_response

ROOT = Path(__file__).resolve().parent.parent


def _toolbox_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:5001/", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


needs_toolbox = pytest.mark.skipif(not _toolbox_up(), reason="store MCP Toolbox not running on :5001")


@pytest.mark.parametrize("cfg", ["tools.local.yaml", "tools.yaml"])
def test_toolbox_config_parameters_match_sql(cfg):
    tools = yaml.safe_load((ROOT / "toolbox" / cfg).read_text())["tools"]
    assert len(tools) == 13
    for name, tool in tools.items():
        n = len(tool.get("parameters", []))
        for i in range(1, n + 1):
            assert f"${i}" in tool["statement"], f"{cfg}:{name} never uses ${i}"
        assert f"${n + 1}" not in tool["statement"]


def test_production_config_takes_identity_from_google_token():
    data = yaml.safe_load((ROOT / "toolbox" / "tools.yaml").read_text())
    assert data["authServices"]["google_signin"]["kind"] == "google"
    for name, tool in data["tools"].items():
        for p in tool.get("parameters", []):
            if p["name"] == "user_email":
                assert p["authServices"][0] == {"name": "google_signin", "field": "email"}, name
    # every order-changing statement is scoped to the signed-in user
    for name in ("update-order-delivery-method", "cancel-order"):
        assert "u.email" in data["tools"][name]["statement"]


def test_google_mode_rejects_anonymous_requests(monkeypatch):
    import app as web

    monkeypatch.setattr(web, "AUTH_MODE", "google")

    class Req:
        headers = {}

    with pytest.raises(Exception) as err:
        web.identify(Req())
    assert getattr(err.value, "status_code", None) == 401


@needs_toolbox
@pytest.mark.asyncio
async def test_model_never_sees_user_email():
    from finn_agent.agent import root_agent

    tools = await root_agent.tools[0].get_tools()
    for tool in tools:
        decl = tool._get_declaration()
        props = (decl.parameters.properties or {}) if decl.parameters else {}
        assert "user_email" not in props, tool.name
    assert len(tools) == 13


def scripted_shopper(req):
    """Fake Gemini: maps the user's words to one tool call, then replies with the result."""
    if "add-product-to-shopping-list" not in declared_tools(req):
        return None
    part = last_part(req)
    if part and part.function_response:
        return text_response("RESULT:" + json.dumps(part.function_response.response))
    said = part.text.lower() if part and part.text else ""
    if said.startswith("add"):
        return call_response("add-product-to-shopping-list",
                             {"product_name": "Manduka PRO Yoga Mat", "size": "One Size", "color": "Black Magic"})
    if "list" in said:
        return call_response("show-shopping-list", {})
    if "stores" in said:
        return call_response("find-stores-near-user", {})
    return text_response("Hi! I'm Finn.")


async def _chat(client, user, message, session_id=None):
    r = await client.post("/chat", json={"message": message, "session_id": session_id},
                          headers={"X-Demo-User": user})
    assert r.status_code == 200, r.text
    return r.text, r.headers["X-Session-Id"]


@needs_toolbox
@pytest.mark.asyncio
async def test_e2e_users_are_isolated():
    import app as web

    swap_models(web.root_agent, ScriptedLlm(requests=[], responder=scripted_shopper))
    transport = httpx.ASGITransport(app=web.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://finn") as client:
        cfg = (await client.get("/config")).json()
        assert cfg["auth_mode"] == "demo"

        added, priya_sid = await _chat(client, "priya.nair@example.com", "add the yoga mat")
        assert "Added Manduka PRO Yoga Mat" in added

        priya_list, sid2 = await _chat(client, "priya.nair@example.com", "show my list", priya_sid)
        assert sid2 == priya_sid and "Manduka PRO Yoga Mat" in priya_list

        sam_list, sam_sid = await _chat(client, "sam.lee@example.com", "show my list")
        assert "Manduka" not in sam_list                          # Sam can't see Priya's list

        # Sam tries to reuse Priya's session id: gets a fresh session of his own instead
        _, hijack_sid = await _chat(client, "sam.lee@example.com", "show my list", priya_sid)
        assert hijack_sid != priya_sid

        stores, _ = await _chat(client, "alex.rivera@example.com", "stores near me")
        assert "GenAI Sports Downtown San Jose" in stores          # nearest to Alex (SJSU)

        r = await client.post("/chat", json={"message": "hi"}, headers={"X-Demo-User": "mallory@evil.com"})
        assert r.status_code == 403
