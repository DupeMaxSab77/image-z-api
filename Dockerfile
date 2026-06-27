FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium \
    && playwright install-deps chromium

COPY . .

RUN mkdir -p output

EXPOSE 8080

ENV API_HOST=0.0.0.0
ENV API_PORT=8080

CMD ["python", "server.py"]
