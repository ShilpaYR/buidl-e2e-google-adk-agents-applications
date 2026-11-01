#!/usr/bin/env bash
# Deploy the lead generation agent.
#   ./deployment/deploy.sh agent-engine   # Vertex AI Agent Engine (managed sessions, scaling)
#   ./deployment/deploy.sh cloud-run      # Cloud Run with the ADK web UI
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud services enable aiplatform.googleapis.com run.googleapis.com cloudbuild.googleapis.com --project "$PROJECT"

case "${1:-agent-engine}" in
  agent-engine)
    adk deploy agent_engine --project "$PROJECT" --region "$REGION" \
      --display_name "Lead Generation Deep Research Agent" \
      --description "Discovers pre-investment patterns, then finds look-alike leads." \
      ${AGENT_ENGINE_ID:+--agent_engine_id "$AGENT_ENGINE_ID"} \
      lead_generation_agent ;;
  cloud-run)
    adk deploy cloud_run --project "$PROJECT" --region "$REGION" \
      --service_name lead-gen-agent --with_ui --trace_to_cloud \
      lead_generation_agent ;;
  *) echo "usage: $0 agent-engine | cloud-run"; exit 1 ;;
esac
