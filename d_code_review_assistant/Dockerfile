FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY code_review_assistant/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY code_review_assistant ./code_review_assistant
RUN useradd -m agent && chown -R agent /app
USER agent
EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
