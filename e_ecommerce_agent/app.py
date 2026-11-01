"""Finn web backend (FastAPI) — what runs on Cloud Run in front of the agent.

Endpoints
  GET  /          chat UI (frontend/index.html)
  GET  /config    auth mode + OAuth client id for the UI
  POST /chat      {message, session_id?} -> streamed text; header X-Session-Id
  GET  /healthz   liveness probe

Production details
  * Google ID token verified server-side (signature, audience, email_verified).
  * Sessions are keyed by the verified user, so a leaked session id is useless
    to anyone else. Set SESSION_DB_URL for persistent (Cloud SQL/AlloyDB) sessions.
  * One Runner + session service for the whole process (the reference created a
    new InMemorySessionService per request, which silently dropped history).
  * Input size limit, errors logged server-side and never leaked to the browser.

Run:  uvicorn app:app --port 8080
"""

import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.genai import types

from finn_agent.agent import AUTH_MODE, root_agent
from finn_agent.identity import DEMO_USER_EMAIL, current_id_token, current_user_email

log = logging.getLogger("finn")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

APP_NAME = "finn"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
MAX_MESSAGE_CHARS = 4000
DEMO_USERS = {
    "alex.rivera@example.com": "Alex",
    "priya.nair@example.com": "Priya",
    "sam.lee@example.com": "Sam",
}
FRONTEND = Path(__file__).parent / "frontend" / "index.html"

session_service = (DatabaseSessionService(db_url=os.environ["SESSION_DB_URL"])
                   if os.getenv("SESSION_DB_URL") else InMemorySessionService())
runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)
app = FastAPI(title="Finn — GenAI Sports shopping agent")


def identify(request: Request) -> tuple[str, str, str]:
    """Return (email, raw_id_token, first_name) for the caller or raise 401/403."""
    if AUTH_MODE == "google":
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(401, "Sign in with Google first.")
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        try:
            info = id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
        except ValueError:
            raise HTTPException(401, "Invalid or expired Google ID token.")
        if not info.get("email_verified"):
            raise HTTPException(403, "Google account email is not verified.")
        return info["email"], token, info.get("given_name", "")
    email = request.headers.get("X-Demo-User") or DEMO_USER_EMAIL
    if email not in DEMO_USERS:
        raise HTTPException(403, "Unknown demo user.")
    return email, "", DEMO_USERS[email]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND)


@app.get("/config")
def config() -> dict:
    return {"auth_mode": AUTH_MODE, "google_client_id": GOOGLE_CLIENT_ID,
            "demo_users": DEMO_USERS if AUTH_MODE != "google" else {}}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "Empty message.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(413, "Message too long.")
    email, token, first_name = identify(request)

    session_id = str(body.get("session_id") or uuid.uuid4())
    session = await session_service.get_session(app_name=APP_NAME, user_id=email, session_id=session_id)
    if session is None:
        session = await session_service.create_session(
            app_name=APP_NAME, user_id=email, state={"user_first_name": first_name})
        session_id = session.id

    async def stream():
        email_token = current_user_email.set(email)
        id_token_token = current_id_token.set(token)
        try:
            async for event in runner.run_async(
                user_id=email, session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=message)]),
            ):
                if event.author != root_agent.name or not event.content:
                    continue
                for part in event.content.parts or []:
                    if part.text and not part.thought:
                        yield part.text
        except Exception:
            log.exception("chat failed for session %s", session_id)
            yield "\n\nSorry — something went wrong on our side. Please try again."
        finally:
            current_user_email.reset(email_token)
            current_id_token.reset(id_token_token)

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8",
                             headers={"X-Session-Id": session_id, "Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
