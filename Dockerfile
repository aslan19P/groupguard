FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system groupguard && adduser --system --ingroup groupguard groupguard

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src

RUN pip install --upgrade pip && pip install .

USER groupguard

CMD ["sh", "-c", "alembic upgrade head && exec python -m groupguard.main"]
