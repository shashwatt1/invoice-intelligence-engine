#!/usr/bin/env bash
# Creates /opt/edihor/<env>/ with compose.yaml, db.env and app.env for one environment.
# Usage: setup_env.sh production|staging
# Never overwrites existing env files; secrets are generated here and never printed.
set -euo pipefail

ENV_NAME="${1:?production or staging}"
case "$ENV_NAME" in
  production) PROJECT=invoice-prod;    PORT=8100; APP_MEM=1536m; APP_CPU=1.0; DB_MEM=1g;   DB_CPU=1.0; SHARED_BUF=256MB; APP_ENV=production ;;
  staging)    PROJECT=invoice-staging; PORT=8101; APP_MEM=768m;  APP_CPU=0.5; DB_MEM=512m; DB_CPU=0.5; SHARED_BUF=128MB; APP_ENV=staging ;;
  *) echo "unknown environment: $ENV_NAME" >&2; exit 1 ;;
esac

DIR=/opt/edihor/$ENV_NAME
mkdir -p "$DIR"
cd "$DIR"
umask 077

cat > compose.yaml <<EOF
# $ENV_NAME - Invoice Intelligence Engine on the shared OCI VM.
# App listens on 127.0.0.1:$PORT only (Nginx proxies to it). PostgreSQL has no host port.
name: $PROJECT

services:
  db:
    image: postgres:17-alpine
    container_name: $PROJECT-db
    restart: unless-stopped
    env_file: db.env
    command: ["postgres", "-c", "shared_buffers=$SHARED_BUF", "-c", "max_connections=40"]
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \$\$POSTGRES_USER -d \$\$POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 5
    mem_limit: $DB_MEM
    cpus: $DB_CPU

  app:
    image: \${APP_IMAGE:-invoice-engine:current}
    container_name: $PROJECT-app
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    env_file: app.env
    ports:
      - "127.0.0.1:$PORT:8000"
    volumes:
      - uploads:/app/uploads
    mem_limit: $APP_MEM
    cpus: $APP_CPU

volumes:
  pgdata:
  uploads:
EOF

if [[ -e db.env || -e app.env ]]; then
  echo "$DIR: env files already exist - left unchanged"
else
  DB_PW=$(openssl rand -hex 24)
  cat > db.env <<EOF
POSTGRES_USER=invoice
POSTGRES_DB=invoice
POSTGRES_PASSWORD=$DB_PW
EOF
  cat > app.env <<EOF
APP_ENV=$APP_ENV
APP_NAME=Invoice Intelligence Platform
DEBUG=false
ALLOWED_ORIGINS=

DATABASE_URL=postgresql+asyncpg://invoice:$DB_PW@db:5432/invoice
DATABASE_URL_SYNC=postgresql+psycopg2://invoice:$DB_PW@db:5432/invoice
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5

STORAGE_BACKEND=local
UPLOAD_PATH=/app/uploads

OCR_PROVIDER=google_vision
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o
# Fill these two in before processing invoices:
GOOGLE_VISION_API_KEY=
OPENAI_API_KEY=

SECRET_KEY=$(openssl rand -hex 32)
ACCESS_TOKEN_EXPIRE_MINUTES=60

LOG_LEVEL=INFO
# stdout only: Docker rotates it (10 MB x 3)
LOG_FILE_ENABLED=false
EOF
  echo "$DIR: env files created (mode 600, secrets not shown)"
fi
ls -l "$DIR"
