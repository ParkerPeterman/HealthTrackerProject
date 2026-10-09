# One image for the API (+ built tracker app), dashboard, scheduler, and CLI.

# ---- web app build
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- python runtime
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

ARG TARGETARCH=amd64
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256_amd64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_arm64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
 && if [ "$TARGETARCH" = "arm64" ]; then sum="$SUPERCRONIC_SHA256_arm64"; else sum="$SUPERCRONIC_SHA256_amd64"; fi \
 && echo "${sum}  /usr/local/bin/supercronic" | sha256sum -c - \
 && chmod +x /usr/local/bin/supercronic

WORKDIR /app
# Dependencies first so code changes don't reinstall them.
COPY pyproject.toml README.md ./
RUN mkdir -p src/healthtracker && echo '__version__ = "0.1.0"' > src/healthtracker/__init__.py \
 && pip install . && pip uninstall -y healthtracker
COPY src ./src
RUN pip install --no-deps .
COPY db ./db
COPY docker/crontab ./docker/crontab
COPY .streamlit ./.streamlit
COPY --from=web /web/dist ./web/dist

ENV MIGRATIONS_DIR=/app/db/migrations WEB_DIST=/app/web/dist
CMD ["healthtracker", "--help"]
