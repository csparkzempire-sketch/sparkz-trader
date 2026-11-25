# SPARKZ TRADER V2 as one always-on web service: the dashboard (built here) and the API, robot loop, market
# assistant and MT5 bridge endpoints. Market data comes from your MT5 through bridge/mt5_bridge.py on your PC.
#
#   docker build -t sparkz-v2 SPARKZ-TRADER-V2 && docker run -p 8000:8000 --env-file .env sparkz-v2
#
# Secrets (SPARKZ_BRIDGE_TOKEN, SPARKZ_DASHBOARD_PASSWORD, TELEGRAM_BOT_TOKEN) are set on the host, never here.

FROM node:20-slim AS dashboard
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/ backend/
COPY config/ config/
COPY reports/studies/ reports/studies/
COPY --from=dashboard /app/frontend/dist frontend/dist
RUN mkdir -p data && useradd -m sparkz && chown -R sparkz /app
USER sparkz
WORKDIR /app/backend
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
