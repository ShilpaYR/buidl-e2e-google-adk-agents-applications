#!/usr/bin/env bash
# One-command local setup for all five agents (macOS or Linux; bash 3.2 compatible).
#
#   ./setup.sh            everything: Python env, Gemini CLI, .env files, Postgres + MCP Toolbox, tests, live warm-up
#   ./setup.sh infra      only (re)start Postgres + both Toolbox servers
#   ./setup.sh warmup     only the live warm-up runs
#   ./setup.sh stop       stop Toolbox servers (and the Docker Postgres)
#
# Your API key is read once, written only to git-ignored .env files (chmod 600), never printed.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
RUN="$ROOT/.run"
mkdir -p "$RUN"
CMD="${1:-all}"
TOOLBOX_PKG="@toolbox-sdk/server@1.15.0"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
up()   { curl -s -o /dev/null -m 2 "http://127.0.0.1:$1/"; }

# ------------------------------------------------------------------ python
python_env() {
  say "Python environment"
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if have "$c" && "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    have brew || die "Python 3.10-3.13 not found. Install it (brew install python@3.12) and re-run."
    warn "Python 3.10-3.13 not found — installing python@3.12 with Homebrew"
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  fi
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
  ok "$(.venv/bin/python --version) in .venv with requirements installed"
}

# ------------------------------------------------------------------ node + gemini cli
gemini_cli() {
  say "Node.js and Gemini CLI"
  if ! have node || ! node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)'; then
    have brew || die "Node.js 20+ is required (https://nodejs.org) — install it and re-run."
    warn "Node.js 20+ not found — installing with Homebrew"
    brew install node
  fi
  ok "node $(node --version)"
  GEMINI_BIN="gemini"
  if have gemini; then
    ok "Gemini CLI $(gemini --version 2>/dev/null | tail -1) already installed"
  elif npm install -g @google/gemini-cli >"$RUN/npm-gemini.log" 2>&1; then
    ok "Gemini CLI installed globally"
  else
    warn "global npm install not permitted — installing Gemini CLI inside .run/"
    npm install --prefix "$RUN/npm" @google/gemini-cli >"$RUN/npm-gemini.log" 2>&1 || die "npm install failed, see $RUN/npm-gemini.log"
    GEMINI_BIN="$RUN/npm/node_modules/.bin/gemini"
    ok "Gemini CLI at $GEMINI_BIN"
  fi
}

# ------------------------------------------------------------------ keys + .env files
env_files() {
  say "API keys and .env files"
  KEY="${GOOGLE_API_KEY:-}"
  if [ -z "$KEY" ] && [ -f a_lead_generation_agent/.env ]; then
    KEY="$(grep -E '^GOOGLE_API_KEY=' a_lead_generation_agent/.env | head -1 | cut -d= -f2- || true)"
    case "$KEY" in your-*|"") KEY="" ;; esac
  fi
  if [ -z "$KEY" ]; then
    echo "    Paste your Gemini API key (get one at https://aistudio.google.com/apikey)."
    printf '    GOOGLE_API_KEY (hidden): '; read -rs KEY; echo
  fi
  [ -n "$KEY" ] || die "an API key is required"
  GH="${GITHUB_PERSONAL_ACCESS_TOKEN:-}"
  if [ -z "$GH" ] && [ -f c_mcp_bug_assistant/.env ]; then
    GH="$(grep -E '^GITHUB_PERSONAL_ACCESS_TOKEN=' c_mcp_bug_assistant/.env | head -1 | cut -d= -f2- || true)"
  fi
  if [ -z "$GH" ] && [ -t 0 ]; then
    printf '    Optional GitHub token for agent (c), read-only public repos (Enter to skip): '; read -rs GH; echo
  fi
  KEY="$KEY" GH="$GH" GEMINI_BIN="${GEMINI_BIN:-gemini}" .venv/bin/python - <<'PY'
import os
from pathlib import Path
key, gh, gem = os.environ["KEY"], os.environ["GH"], os.environ["GEMINI_BIN"]
extra = {
    "b_gemini_cli_tool_agent": {"GEMINI_API_KEY": key, **({"GEMINI_CLI_BIN": gem} if gem != "gemini" else {})},
    "c_mcp_bug_assistant": {"MCP_TOOLBOX_URL": "http://127.0.0.1:5000", **({"GITHUB_PERSONAL_ACCESS_TOKEN": gh} if gh else {})},
    "e_ecommerce_agent": {"ECOMMERCE_TOOLBOX_URL": "http://127.0.0.1:5001", "AUTH_MODE": "demo",
                          "DEMO_USER_EMAIL": "alex.rivera@example.com"},
}
for folder in ["a_lead_generation_agent", "b_gemini_cli_tool_agent", "c_mcp_bug_assistant",
               "d_code_review_assistant", "e_ecommerce_agent"]:
    path = Path(folder) / ".env"
    old = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                old[k] = v
    values = {"GOOGLE_GENAI_USE_VERTEXAI": "FALSE", "GOOGLE_API_KEY": key,
              "FAST_MODEL": old.get("FAST_MODEL", "gemini-3.8-flash"),
              "ADVANCED_MODEL": old.get("ADVANCED_MODEL", "gemini-3.8-flash"), **extra.get(folder, {})}
    path.write_text("# generated by setup.sh — git-ignored, do not commit\n"
                    + "".join(f"{k}={v}\n" for k, v in values.items()))
    path.chmod(0o600)
PY
  ok ".env written in all 5 agent folders (git-ignored)"
  [ -n "$GH" ] || warn "no GitHub token: agent (c) runs without its GitHub MCP tools"
}

# ------------------------------------------------------------------ postgres
postgres() {
  say "PostgreSQL"
  if .venv/bin/python scripts/load_db.py --check >/dev/null 2>&1; then
    ok "Postgres already reachable on 127.0.0.1:5432 as postgres/postgres"
  elif have docker && docker info >/dev/null 2>&1; then
    docker compose up -d --wait postgres
    ok "Postgres 16 running in Docker"
  elif have brew; then
    if ! brew list postgresql@16 >/dev/null 2>&1; then
      warn "installing postgresql@16 with Homebrew (no Docker found)"
      brew install postgresql@16
    fi
    brew services start postgresql@16 >/dev/null
    i=0; until .venv/bin/python scripts/load_db.py --bootstrap-role >/dev/null 2>&1; do
      i=$((i+1)); [ $i -lt 20 ] || die "Homebrew Postgres did not start (brew services list)"; sleep 1
    done
    ok "Homebrew postgresql@16 running"
  else
    die "No Postgres found. Install Docker Desktop (https://docker.com) or Homebrew (https://brew.sh) and re-run."
  fi
  .venv/bin/python scripts/load_db.py | sed 's/^/    /'
}

# ------------------------------------------------------------------ mcp toolbox
start_toolbox() { # port config
  local port="$1" cfg="$2"
  if up "$port"; then ok "Toolbox already running on :$port"; return; fi
  DB_HOST=127.0.0.1 DB_USER=postgres DB_PASS=postgres nohup npx -y "$TOOLBOX_PKG" \
    --config "$cfg" --address 127.0.0.1 --port "$port" >"$RUN/toolbox-$port.log" 2>&1 &
  echo $! >"$RUN/toolbox-$port.pid"
  local i=0
  until up "$port"; do
    i=$((i+1)); [ $i -lt 90 ] || die "Toolbox on :$port did not start — see $RUN/toolbox-$port.log"; sleep 1
  done
  ok "MCP Toolbox on :$port ($(grep -o 'Initialized [0-9]* tools' "$RUN/toolbox-$port.log" | head -1))"
}

toolbox() {
  say "MCP Toolbox for Databases"
  start_toolbox 5000 "$ROOT/c_mcp_bug_assistant/mcp-toolbox/tools.local.yaml"
  start_toolbox 5001 "$ROOT/e_ecommerce_agent/toolbox/tools.local.yaml"
}

# ------------------------------------------------------------------ checks
tests() {
  say "Automated tests"
  if ( . .venv/bin/activate && ./run_tests.sh ) >"$RUN/tests.log" 2>&1; then
    grep -E 'passed|failed' "$RUN/tests.log" | sed 's/^/    /'; ok "all test suites passed"
  else
    grep -E '^===|passed|failed|error' "$RUN/tests.log" | sed 's/^/    /'; warn "some tests failed — see $RUN/tests.log"
  fi
}

warmup() {
  say "Live warm-up runs (real Gemini calls, a few cents at most)"
  if ! .venv/bin/python scripts/warmup.py --pick-model; then
    warn "could not find a Gemini model for this key — fix the key, then: ./setup.sh warmup"; return 0
  fi
  .venv/bin/python scripts/warmup.py || warn "some warm-ups failed — details in .run/warmup_report.md"
}

kill_tree() { # pid — npx starts the real server as a grandchild, so stop the whole tree
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$child"; done
  kill "$1" 2>/dev/null || true
}

stop() {
  say "Stopping"
  for f in "$RUN"/toolbox-*.pid; do
    [ -f "$f" ] || continue
    kill_tree "$(cat "$f")"; rm -f "$f"
  done
  ok "Toolbox servers stopped"
  if have docker && docker info >/dev/null 2>&1 && docker compose ps -q postgres 2>/dev/null | grep -q .; then
    docker compose stop postgres >/dev/null && ok "Docker Postgres stopped (data kept)"
  fi
}

case "$CMD" in
  all)    python_env; gemini_cli; env_files; postgres; toolbox; tests; warmup
          say "Done. Run an agent:  source .venv/bin/activate && cd a_lead_generation_agent && adk web"
          echo "    Finn's web app:     cd e_ecommerce_agent && uvicorn app:app --port 8080" ;;
  infra)  [ -x .venv/bin/python ] || python_env; postgres; toolbox ;;
  warmup) [ -x .venv/bin/python ] || python_env; warmup ;;
  stop)   stop ;;
  *)      echo "usage: ./setup.sh [all|infra|warmup|stop]"; exit 1 ;;
esac
