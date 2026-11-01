# (e) Production-quality e-commerce agent — "Finn" for GenAI Sports

A shopping assistant that searches products semantically, manages a shopping list, finds nearby stores and
places / changes / cancels orders — with the **customer's identity enforced outside the LLM**.

```mermaid
flowchart LR
  B[Browser<br/>Google Sign-In] -->|ID token| API[app.py · FastAPI<br/>verifies token, streams]
  API --> RUN[ADK Runner<br/>sessions keyed by verified user] --> F[finn LlmAgent]
  F -->|MCP · ToolboxToolset<br/>forwards ID token| TB[(MCP Toolbox<br/>Cloud Run)]
  TB -->|authServices: email from token| DB[(AlloyDB<br/>pgvector + embedding())]
```

**Why identity matters:** user-scoped tools (`show-shopping-list`, `place-order`, `cancel-order`, …) take
`user_email`. In production Toolbox fills it from the **verified Google ID token** (`authServices` in
`toolbox/tools.yaml`); locally the agent binds it client-side (`bound_params`). Either way the parameter is
**removed from the schema the model sees**, so a prompt-injected "cancel Sam's order" cannot work, and every
order statement also checks ownership + `pending` status in SQL.

13 MCP tools: semantic/keyword product search, by brand, by size, product details, store inventory,
add to / show shopping list, nearest stores (haversine / PostGIS), delivery methods, place order,
order status, change delivery method (recomputes total), cancel order.

## Run locally (no cloud needed)

```bash
psql -U postgres -f db/schema.sql && psql -U postgres -f db/seed.sql
npx @toolbox-sdk/server --config toolbox/tools.local.yaml --port 5001      # terminal 1
pip install -r ../requirements.txt && cp ../.env.example .env              # terminal 2
uvicorn app:app --port 8080        # web app → http://localhost:8080 (demo-user picker)
# or: adk web                      # ADK dev UI, acts as DEMO_USER_EMAIL
```

Demo script (from the codelab, adapted):
1. `I'm looking for running shoes for an ultra-trail` → 2. `Tell me more about the Speedgoat 6`
3. `Add the Speedgoat 6 size 10 in Blue to my shopping list` → 4. `Show my shopping list`
5. `Find stores near me` → 6. `What delivery methods does GenAI Sports Downtown San Jose have?`
7. `Place an order at GenAI Sports Downtown San Jose` (Finn asks to confirm) → `yes`
8. `Show my orders` → 9. `Change it to Express Delivery` → 10. switch demo user to Sam → `Show my orders` (empty)

## Production deployment (Google Cloud)

```bash
./deploy.sh alloydb      # VPC peering, AlloyDB cluster + instance, Vertex AI permission
./deploy.sh load-data    # run db/schema.sql, db/seed.sql, db/alloydb_extras.sql in AlloyDB Studio
./deploy.sh toolbox      # MCP Toolbox on Cloud Run, tools.yaml + DB password from Secret Manager
./deploy.sh app          # Finn on Cloud Run (AUTH_MODE=google), invoker permission on Toolbox
```

## Tests

```bash
python -m pytest -q -rs   # e2e tests run when Toolbox is on :5001
```
