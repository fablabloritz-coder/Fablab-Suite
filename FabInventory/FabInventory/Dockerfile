FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/uploads

ENV PORT=5590
ENV DB_PATH=/data/fabinventory.db
ENV UPLOAD_FOLDER=/data/uploads

EXPOSE 5590

CMD ["gunicorn", "--bind", "0.0.0.0:5590", "--workers", "2", "--timeout", "120", "app:app"]
