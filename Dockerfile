FROM node:22-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS data-validator
WORKDIR /build
RUN pip install --no-cache-dir Pillow==11.3.0
COPY scripts/validate_data.py scripts/validate_data.py
COPY src/ src/
COPY data/ data/
RUN python scripts/validate_data.py data/clubs.json

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CREST_QUEST_ENV=production \
    CREST_QUEST_FRONTEND_DIST=/app/frontend/dist \
    CREST_QUEST_DATA_PATH=/app/data/clubs.json \
    CREST_QUEST_LEADERBOARD_PATH=/data/leaderboard.sqlite3
WORKDIR /app

RUN addgroup --system crestquest && adduser --system --ingroup crestquest crestquest
COPY pyproject.toml README.md ./
COPY server/ server/
COPY src/ src/
RUN pip install --no-cache-dir .
COPY --from=data-validator /build/data/ data/
COPY --from=frontend-builder /build/frontend/dist frontend/dist/
RUN mkdir -p /data && chown -R crestquest:crestquest /app /data

USER crestquest
EXPOSE 8000
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
