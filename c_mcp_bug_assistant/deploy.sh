#!/usr/bin/env bash
# Google Cloud deployment: Cloud SQL (tickets + vector search) <- MCP Toolbox (Cloud Run) <- agent (Cloud Run)
#   ./deploy.sh database   # Cloud SQL instance, schema, seed, embeddings
#   ./deploy.sh toolbox    # MCP Toolbox on Cloud Run (tools.yaml from Secret Manager)
#   ./deploy.sh agent      # the ADK agent + web UI on Cloud Run
set -euo pipefail
cd "$(dirname "$0")"
PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

case "${1:-help}" in
  database)
    gcloud services enable sqladmin.googleapis.com aiplatform.googleapis.com run.googleapis.com \
      secretmanager.googleapis.com cloudbuild.googleapis.com --project "$PROJECT"
    read -rsp "Choose a Postgres password: " DBPW; echo
    printf '%s' "$DBPW" | gcloud secrets create tickets-db-password --data-file=- --project "$PROJECT"
    gcloud sql instances create software-assistant --database-version POSTGRES_16 --tier db-custom-1-3840 \
      --region "$REGION" --root-password "$DBPW" --database-flags cloudsql.enable_google_ml_integration=on \
      --enable-google-ml-integration --project "$PROJECT"
    SA=$(gcloud sql instances describe software-assistant --project "$PROJECT" --format='value(serviceAccountEmailAddress)')
    gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role roles/aiplatform.user >/dev/null
    echo "Now connect (gcloud sql connect software-assistant --user postgres) and run:"
    echo "  \\i db/schema.sql   \\i db/seed.sql   \\i db/cloud_embeddings.sql" ;;

  toolbox)
    gcloud iam service-accounts create toolbox-identity --project "$PROJECT" || true
    for ROLE in roles/cloudsql.client roles/secretmanager.secretAccessor; do
      gcloud projects add-iam-policy-binding "$PROJECT" \
        --member "serviceAccount:toolbox-identity@$PROJECT.iam.gserviceaccount.com" --role "$ROLE" >/dev/null
    done
    gcloud secrets create tickets-tools --data-file=mcp-toolbox/tools.yaml --project "$PROJECT" 2>/dev/null || \
      gcloud secrets versions add tickets-tools --data-file=mcp-toolbox/tools.yaml --project "$PROJECT"
    gcloud run deploy toolbox --image us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.15.0 \
      --service-account "toolbox-identity@$PROJECT.iam.gserviceaccount.com" --region "$REGION" --project "$PROJECT" \
      --set-secrets "/app/tools.yaml=tickets-tools:latest,DB_PASS=tickets-db-password:latest" \
      --set-env-vars "PROJECT_ID=$PROJECT,REGION=$REGION,DB_USER=postgres" \
      --args="--config=/app/tools.yaml","--address=0.0.0.0","--port=8080" --no-allow-unauthenticated ;;

  agent)
    TOOLBOX_URL=$(gcloud run services describe toolbox --region "$REGION" --project "$PROJECT" --format='value(status.url)')
    adk deploy cloud_run --project "$PROJECT" --region "$REGION" --service_name software-bug-assistant \
      --with_ui --trace_to_cloud \
      --env "MCP_TOOLBOX_URL=$TOOLBOX_URL" --env "GOOGLE_GENAI_USE_VERTEXAI=TRUE" \
      bug_assistant
    echo "Grant the agent's service account roles/run.invoker on the toolbox service, and store"
    echo "GITHUB_PERSONAL_ACCESS_TOKEN in Secret Manager (gcloud run services update ... --set-secrets)." ;;

  *) echo "usage: $0 database | toolbox | agent" ;;
esac
