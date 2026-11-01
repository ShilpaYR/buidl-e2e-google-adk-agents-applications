"""Production entrypoint for Cloud Run.

* Sessions  — Cloud SQL (PostgreSQL) when DB_* env vars are set, so conversations
  and `user:` history survive restarts and scale-out; in-memory otherwise.
* Artifacts — Google Cloud Storage bucket (review reports, fixed code).
* Tracing   — OpenTelemetry → Cloud Trace.
* Cost cap  — max_llm_calls per invocation.

Local:  uvicorn main:app --port 8080
"""

import os
from urllib.parse import quote_plus

import uvicorn
from google.adk.cli.fast_api import get_fast_api_app

AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))

DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")  # inject from Secret Manager
DB_NAME = os.environ.get("DB_NAME")
CLOUD_SQL_CONNECTION_NAME = os.environ.get("CLOUD_SQL_CONNECTION_NAME")  # project:region:instance
ARTIFACT_BUCKET = os.environ.get("ARTIFACT_BUCKET")

if all([DB_USER, DB_PASSWORD, DB_NAME, CLOUD_SQL_CONNECTION_NAME]):
    session_uri = (f"postgresql+psycopg2://{DB_USER}:{quote_plus(DB_PASSWORD)}@/{DB_NAME}"
                   f"?host=/cloudsql/{CLOUD_SQL_CONNECTION_NAME}")
    print("Sessions: Cloud SQL")
else:
    session_uri = None
    print("Sessions: in-memory (set DB_* and CLOUD_SQL_CONNECTION_NAME for persistence)")

app = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    session_service_uri=session_uri,
    artifact_service_uri=f"gs://{ARTIFACT_BUCKET}" if ARTIFACT_BUCKET else None,
    allow_origins=os.environ.get("ALLOW_ORIGINS", "").split(",") if os.environ.get("ALLOW_ORIGINS") else None,
    web=os.environ.get("SERVE_WEB_UI", "true").lower() == "true",
    trace_to_cloud=bool(os.environ.get("GOOGLE_CLOUD_PROJECT")) and os.environ.get("TRACE_TO_CLOUD", "true") == "true",
    max_llm_calls=int(os.environ.get("MAX_LLM_CALLS", "60")),
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
