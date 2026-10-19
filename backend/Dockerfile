FROM python:3.12-slim

WORKDIR /code
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# DejaVu fonts let the health passport PDF print names like "Ụzọamaka" correctly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# uv reads dependencies straight from pyproject.toml, so this layer
# is cached until pyproject.toml changes.
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /bin/uv
COPY pyproject.toml .
RUN uv pip install --system --no-cache -r pyproject.toml

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
