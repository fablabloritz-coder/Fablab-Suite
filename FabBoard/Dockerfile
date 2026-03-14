FROM python:3.11-slim

LABEL maintainer="Fablab Loritz"
LABEL description="FabBoard - Dashboard TV pour Fablab"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_DEBUG=0

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app \
    && apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY . /app

RUN mkdir -p /app/data /app/static/uploads \
    && chown -R app:app /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5580

ENTRYPOINT ["/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5580/')" || exit 1

CMD ["waitress-serve", "--listen=0.0.0.0:5580", "app:app"]
