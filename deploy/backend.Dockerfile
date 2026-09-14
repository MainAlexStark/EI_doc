FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TYPST_VERSION=0.12.0

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Typst — один статический бинарник, рендерит протоколы в PDF.
# Ни Excel, ни LibreOffice, ни headless-браузера на сервере нет и не будет.
RUN curl -fsSL "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz" \
      | tar -xJ -C /tmp \
    && mv /tmp/typst-x86_64-unknown-linux-musl/typst /usr/local/bin/typst \
    && chmod +x /usr/local/bin/typst \
    && typst --version

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

COPY backend /app
COPY --from=frontend-build /app/dist /app/frontend_dist

RUN useradd --create-home --uid 1000 app && mkdir -p /srv/media && chown -R app /app /srv/media
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
