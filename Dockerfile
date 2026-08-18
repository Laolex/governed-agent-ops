FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY ops ./ops
COPY console ./console
COPY scripts ./scripts

RUN pip install --no-cache-dir \
      fastapi uvicorn google-cloud-firestore google-auth requests \
      "google-cloud-aiplatform[agent_engines,adk]"

# Cloud Run supplies PORT. The console and the API share one origin, so a
# viewer's browser never needs a second host and there is no CORS surface.
ENV PORT=8080
CMD exec uvicorn ops.service:app --host 0.0.0.0 --port ${PORT}
