#!/usr/bin/env bash
# Usage: ./deploy.sh local | cloud-run | agent-engine
set -euo pipefail
cd "$(dirname "$0")"
MODE="${1:-local}"
PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE="code-review-assistant"
BUCKET="${ARTIFACT_BUCKET:-${PROJECT}-code-review-artifacts}"

case "$MODE" in
  local)
    # In-memory sessions, ADK dev UI on http://localhost:8080
    exec uvicorn main:app --reload --port 8080 ;;

  cloud-run)
    INSTANCE="${CLOUD_SQL_INSTANCE_NAME:-code-review-sessions}"
    gcloud services enable run.googleapis.com sqladmin.googleapis.com aiplatform.googleapis.com \
      cloudbuild.googleapis.com secretmanager.googleapis.com --project "$PROJECT"

    # 1. artifacts bucket
    gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
      gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --project "$PROJECT"

    # 2. Cloud SQL (PostgreSQL) for persistent sessions
    if ! gcloud sql instances describe "$INSTANCE" --project "$PROJECT" >/dev/null 2>&1; then
      gcloud sql instances create "$INSTANCE" --database-version POSTGRES_16 --tier db-f1-micro \
        --region "$REGION" --project "$PROJECT"
      gcloud sql databases create adk_sessions --instance "$INSTANCE" --project "$PROJECT"
      DBPW="$(openssl rand -base64 24)"
      gcloud sql users create adk --instance "$INSTANCE" --password "$DBPW" --project "$PROJECT"
      printf '%s' "$DBPW" | gcloud secrets create code-review-db-password --data-file=- --project "$PROJECT"
    fi
    CONN="$(gcloud sql instances describe "$INSTANCE" --project "$PROJECT" --format='value(connectionName)')"

    # 3. build from source and deploy (password comes from Secret Manager, never from env files)
    gcloud run deploy "$SERVICE" --source . --region "$REGION" --project "$PROJECT" \
      --add-cloudsql-instances "$CONN" \
      --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,GOOGLE_GENAI_USE_VERTEXAI=TRUE,DB_USER=adk,DB_NAME=adk_sessions,CLOUD_SQL_CONNECTION_NAME=$CONN,ARTIFACT_BUCKET=$BUCKET" \
      --set-secrets "DB_PASSWORD=code-review-db-password:latest" \
      --memory 1Gi --timeout 600 --no-allow-unauthenticated
    echo "Open with: gcloud run services proxy $SERVICE --region $REGION --project $PROJECT" ;;

  agent-engine)
    # Fully managed runtime with built-in VertexAiSessionService.
    adk deploy agent_engine --project "$PROJECT" --region "$REGION" \
      --display_name "Code Review Assistant" ${AGENT_ENGINE_ID:+--agent_engine_id "$AGENT_ENGINE_ID"} \
      code_review_assistant ;;

  *) echo "unknown mode: $MODE"; exit 1 ;;
esac
