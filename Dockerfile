# 1. Build Stage
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# 2. Runtime Stage
FROM python:3.12-slim

WORKDIR /app

# Non-root 사용자 생성 (보안 강화)
RUN groupadd -r appuser && useradd -r -g appuser appuser

COPY --from=builder /root/.local /home/appuser/.local
COPY src/ /app/src/
COPY sql/ /app/sql/

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
