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

# Meme serveur de production que les autres applications de la suite.
CMD ["waitress-serve", "--listen=0.0.0.0:5590", "app:app"]
