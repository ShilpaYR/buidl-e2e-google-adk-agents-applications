#!/usr/bin/env bash
# Production deployment on Google Cloud (mirrors the codelab architecture):
#   AlloyDB (private IP)  <-  MCP Toolbox (Cloud Run, tools.yaml in Secret Manager)
#                          <-  Finn backend + UI (Cloud Run)  <-  browser with Google Sign-In
#
# Prereqs: gcloud auth login; billing enabled; an OAuth 2.0 Web client ID
# (APIs & Services > Credentials) whose authorised JavaScript origin will be the
# Finn Cloud Run URL. Usage: ./deploy.sh alloydb | load-data | toolbox | app
set -euo pipefail
cd "$(dirname "$0")"
PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
CLUSTER="${ALLOYDB_CLUSTER:-alloydb-cluster}"
INSTANCE="${ALLOYDB_INSTANCE:-alloydb-inst}"
STEP="${1:-help}"

case "$STEP" in
  alloydb)
    gcloud services enable alloydb.googleapis.com compute.googleapis.com servicenetworking.googleapis.com \
      run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
      aiplatform.googleapis.com --project "$PROJECT"
    gcloud compute addresses create psa-range --global --purpose=VPC_PEERING --prefix-length=16 \
      --network=default --project "$PROJECT" || true
    gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com --ranges=psa-range \
      --network=default --project "$PROJECT" || true
    read -rsp "Choose an AlloyDB postgres password: " DBPW; echo
    printf '%s' "$DBPW" | gcloud secrets create finn-db-password --data-file=- --project "$PROJECT"
    gcloud alloydb clusters create "$CLUSTER" --region "$REGION" --password "$DBPW" --network default --project "$PROJECT"
    gcloud alloydb instances create "$INSTANCE" --cluster "$CLUSTER" --region "$REGION" \
      --instance-type PRIMARY --cpu-count 2 --project "$PROJECT"
    # let AlloyDB call Vertex AI for embedding()
    PN=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
    gcloud projects add-iam-policy-binding "$PROJECT" \
      --member "serviceAccount:service-$PN@gcp-sa-alloydb.iam.gserviceaccount.com" --role roles/aiplatform.user ;;

  load-data)
    # Run from AlloyDB Studio or a VM in the VPC: schema, seed, then vector embeddings.
    echo "In AlloyDB Studio run, in order: db/schema.sql, db/seed.sql, db/alloydb_extras.sql" ;;

  toolbox)
    : "${GOOGLE_OAUTH_CLIENT_ID:?set GOOGLE_OAUTH_CLIENT_ID}"
    gcloud iam service-accounts create toolbox-identity --project "$PROJECT" || true
    for ROLE in roles/alloydb.client roles/serviceusage.serviceUsageConsumer roles/secretmanager.secretAccessor; do
      gcloud projects add-iam-policy-binding "$PROJECT" \
        --member "serviceAccount:toolbox-identity@$PROJECT.iam.gserviceaccount.com" --role "$ROLE" >/dev/null
    done
    gcloud secrets create finn-tools --data-file=toolbox/tools.yaml --project "$PROJECT" 2>/dev/null || \
      gcloud secrets versions add finn-tools --data-file=toolbox/tools.yaml --project "$PROJECT"
    gcloud run deploy finn-toolbox \
      --image us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.15.0 \
      --service-account "toolbox-identity@$PROJECT.iam.gserviceaccount.com" \
      --region "$REGION" --project "$PROJECT" --network default --subnet default \
      --set-secrets "/app/tools.yaml=finn-tools:latest,DB_PASS=finn-db-password:latest" \
      --set-env-vars "PROJECT_ID=$PROJECT,REGION=$REGION,ALLOYDB_CLUSTER=$CLUSTER,ALLOYDB_INSTANCE=$INSTANCE,GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID" \
      --args="--config=/app/tools.yaml","--address=0.0.0.0","--port=8080" \
      --no-allow-unauthenticated ;;

  app)
    : "${GOOGLE_OAUTH_CLIENT_ID:?set GOOGLE_OAUTH_CLIENT_ID}"
    TOOLBOX_URL=$(gcloud run services describe finn-toolbox --region "$REGION" --project "$PROJECT" --format='value(status.url)')
    gcloud iam service-accounts create finn-agent --project "$PROJECT" || true
    gcloud projects add-iam-policy-binding "$PROJECT" \
      --member "serviceAccount:finn-agent@$PROJECT.iam.gserviceaccount.com" --role roles/aiplatform.user >/dev/null
    gcloud run services add-iam-policy-binding finn-toolbox --region "$REGION" --project "$PROJECT" \
      --member "serviceAccount:finn-agent@$PROJECT.iam.gserviceaccount.com" --role roles/run.invoker
    gcloud run deploy finn --source . --region "$REGION" --project "$PROJECT" \
      --service-account "finn-agent@$PROJECT.iam.gserviceaccount.com" \
      --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,AUTH_MODE=google,GOOGLE_OAUTH_CLIENT_ID=$GOOGLE_OAUTH_CLIENT_ID,ECOMMERCE_TOOLBOX_URL=$TOOLBOX_URL" \
      --allow-unauthenticated
    echo "Add the Finn URL above as an authorised JavaScript origin of your OAuth client." ;;

  *) echo "usage: $0 alloydb | load-data | toolbox | app" ;;
esac
