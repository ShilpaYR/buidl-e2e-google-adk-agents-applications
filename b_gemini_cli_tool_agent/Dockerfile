# ADK agent + Gemini CLI in one container, for Cloud Run.
FROM python:3.12-slim

# git (cloning) + Node.js 22 (Gemini CLI)
RUN apt-get update && apt-get install -y --no-install-recommends curl git ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @google/gemini-cli \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gemini_cli_agent ./gemini_cli_agent

# Non-root user; Gemini CLI writes its settings/cache into $HOME.
RUN useradd -m agent && mkdir -p /tmp/gemini_cli_workspace && chown -R agent /app /tmp/gemini_cli_workspace
USER agent

# On Cloud Run, Gemini CLI authenticates through Vertex AI with the service
# account (ADC). GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION are passed at deploy time.
ENV GOOGLE_GENAI_USE_VERTEXAI=true \
    GEMINI_CLI_APPROVAL_MODE=plan \
    PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "adk web --host 0.0.0.0 --port ${PORT} ."]
