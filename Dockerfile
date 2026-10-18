# Stage 1: build the single page app
FROM node:22-alpine AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: API + static files in one container
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY --from=web /app/frontend/dist frontend/dist
RUN useradd -m lantern && mkdir -p data/uploads logs/operations && chown -R lantern:lantern /app
USER lantern
ENV APP_ENV=production DATABASE_URL=sqlite:////app/data/lantern.db UPLOAD_DIR=/app/data/uploads OPERATION_LOG_DIR=/app/logs/operations
EXPOSE 8000
WORKDIR /app/backend
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
