import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from opentelemetry import trace
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field

try:
    from apps.collector.src.db import (
        DatabaseUnavailable,
        EventRepository,
        close_db_pools,
        get_repository,
        init_db_pools,
    )
    from apps.collector.src.telemetry import setup_tracing
except ModuleNotFoundError:
    from src.db import (
        DatabaseUnavailable,
        EventRepository,
        close_db_pools,
        get_repository,
        init_db_pools,
    )
    from src.telemetry import setup_tracing


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
        current_span = trace.get_current_span()
        if current_span:
            span_context = current_span.get_span_context()
            if span_context and span_context.is_valid:
                log_record["trace_id"] = f"{span_context.trace_id:032x}"
                log_record["span_id"] = f"{span_context.span_id:016x}"

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing(app)
    init_db_pools()
    yield
    close_db_pools()


app = FastAPI(
    title="Mobility Event Collector API",
    version="0.1.0",
    description="3거점 하이브리드 모빌리티 데이터 수집 및 헬스체크 API",
    lifespan=lifespan,
)


@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    method = request.method
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time

    route = request.scope.get("route")
    endpoint = route.path if route else "unmatched"

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
    speed_kmh: float | None = Field(None, ge=0, le=400, description="속도")
    payload: dict = Field(default_factory=dict, description="상세 원격 측정 데이터")


@app.get("/healthz", status_code=status.HTTP_200_OK)
def healthz():
    """Liveness probe: 애플리케이션 프로세스 생존 여부"""
    return {"status": "ok", "site": os.getenv("SITE_ID", "local")}


@app.get("/ready", status_code=status.HTTP_200_OK)
def ready(repo: Annotated[EventRepository, Depends(get_repository)]):
    """Readiness probe: 트래픽 수신 준비 여부"""
    try:
        repo.ping()
        return {"status": "ready", "site": os.getenv("SITE_ID", "local")}
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "reason": "database"},
        )


@app.get("/metrics")
def metrics():
    """Prometheus Scrape Endpoint"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/events", status_code=status.HTTP_201_CREATED)
def collect_event(
    event: MobilityEvent,
    repo: Annotated[EventRepository, Depends(get_repository)],
):
    """차량 원격 측정 이벤트 수집"""
    valid_regions = {"site-a", "site-b", "site-c", "aws"}
    if event.region not in valid_regions:
        EVENTS_COLLECTED.labels(region=event.region, status="rejected").inc()
        logger.warning(f"Invalid region rejected: {event.region}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid region '{event.region}'. Allowed: {sorted(valid_regions)}",
        )

    site = os.getenv("SITE_ID", "local")
    try:
        is_new = repo.insert(event=event, site=site)
    except DatabaseUnavailable as exc:
        logger.error(f"Database error during event insertion: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc

    if is_new:
        EVENTS_COLLECTED.labels(region=event.region, status="accepted").inc()
        logger.info(
            f"Event received: event_id={event.event_id}, region={event.region}, vehicle={event.vehicle_id}"
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content={
                "status": "accepted",
                "event_id": event.event_id,
                "region": event.region,
            },
        )

    EVENTS_COLLECTED.labels(region=event.region, status="duplicate").inc()
    logger.info(
        f"Duplicate event received: event_id={event.event_id}, region={event.region}, vehicle={event.vehicle_id}"
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "duplicate",
            "event_id": event.event_id,
            "region": event.region,
        },
    )


@app.get("/api/v1/events", status_code=status.HTTP_200_OK)
def list_events(
    repo: Annotated[EventRepository, Depends(get_repository)],
    region: str | None = None,
    vehicle_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
):
    """차량 원격 측정 이벤트 목록 조회"""
    clean_region = region if region else None
    clean_vehicle_id = vehicle_id if vehicle_id else None
    try:
        items = repo.list(region=clean_region, vehicle_id=clean_vehicle_id, limit=limit)
        return {"items": items, "count": len(items)}
    except DatabaseUnavailable as exc:
        logger.error(f"Database error during event list: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc


@app.get("/api/v1/events/{event_id}", status_code=status.HTTP_200_OK)
def get_event(
    event_id: str,
    repo: Annotated[EventRepository, Depends(get_repository)],
):
    """차량 원격 측정 이벤트 단건 조회"""
    try:
        record = repo.get(event_id)
    except DatabaseUnavailable as exc:
        logger.error(f"Database error during event get: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="event not found",
        )
    return record
