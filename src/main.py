import json
import logging
import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# JSON 구조화 로거 설정
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "site": os.getenv("SITE_ID", "local"),
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)

handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger = logging.getLogger("mobility-collector")
logger.setLevel(logging.INFO)
logger.handlers = [handler]
logger.propagate = False

# Prometheus 메트릭 정의
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint"],
)
EVENTS_COLLECTED = Counter(
    "mobility_events_collected_total",
    "Total mobility events collected",
    ["region", "status"],
)

app = FastAPI(
    title="Mobility Event Collector API",
    version="0.1.0",
    description="3거점 하이브리드 모빌리티 데이터 수집 및 헬스체크 API",
)

@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    endpoint = request.url.path
    method = request.method
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    # 내부 메트릭/헬스 엔드포인트는 과다 메트릭 기록 방지
    if endpoint not in ("/metrics", "/healthz", "/ready"):
        REQUEST_COUNT.labels(
            method=method, endpoint=endpoint, status_code=response.status_code
        ).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

    return response

# 스키마
class MobilityEvent(BaseModel):
    event_id: str = Field(..., description="차량 이벤트 고유 식별자 (UUID 등)")
    region: str = Field(..., description="거점 식별자 (site-a, site-b, site-c, aws)")
    vehicle_id: str = Field(..., description="차량 식별 번호")
    speed_kmh: Optional[float] = Field(None, ge=0, le=400, description="속도")
    payload: dict = Field(default_factory=dict, description="상세 원격 측정 데이터")

@app.get("/healthz", status_code=status.HTTP_200_OK)
def healthz():
    """Liveness probe: 애플리케이션 프로세스 생존 여부"""
    return {"status": "ok", "site": os.getenv("SITE_ID", "local")}

@app.get("/ready", status_code=status.HTTP_200_OK)
def ready():
    """Readiness probe: 트래픽 수신 준비 여부"""
    # 추후 DB 또는 브로커 연결 상태 체크 연동 가능
    return {"status": "ready", "site": os.getenv("SITE_ID", "local")}

@app.get("/metrics")
def metrics():
    """Prometheus Scrape Endpoint"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/api/v1/events", status_code=status.HTTP_201_CREATED)
def collect_event(event: MobilityEvent):
    """차량 원격 측정 이벤트 수집"""
    valid_regions = {"site-a", "site-b", "site-c", "aws"}
    if event.region not in valid_regions:
        EVENTS_COLLECTED.labels(region=event.region, status="rejected").inc()
        logger.warning(f"Invalid region rejected: {event.region}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid region '{event.region}'. Allowed: {sorted(list(valid_regions))}",
        )

    # 멱등성 및 이벤트 수집 로깅 (향후 DB 저장 연동)
    EVENTS_COLLECTED.labels(region=event.region, status="accepted").inc()
    logger.info(
        f"Event received: event_id={event.event_id}, region={event.region}, vehicle={event.vehicle_id}"
    )
    return {
        "status": "accepted",
        "event_id": event.event_id,
        "region": event.region,
    }
