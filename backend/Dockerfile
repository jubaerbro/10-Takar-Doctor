# Runs the backend on any host that accepts a Dockerfile.
# Mount a persistent disk at /data: it holds the database and the doctors' uploaded documents.
FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
ENV DATABASE_PATH=/data/app.db UPLOAD_DIR=/data/uploads PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --factory --host 0.0.0.0 --port ${PORT}"]
