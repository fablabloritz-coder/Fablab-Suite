FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data static/icons

# Fail fast during build if a Python file has syntax/indentation errors.
RUN python -m py_compile app.py models.py

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_DEBUG=0

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:3000/')" || exit 1

CMD ["python", "-m", "waitress", "--host=0.0.0.0", "--port=3000", "app:app"]
