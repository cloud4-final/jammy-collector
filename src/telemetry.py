import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(
    app: FastAPI,
    tracer_provider: TracerProvider | None = None,
) -> TracerProvider | None:
    """OpenTelemetry 분산 추적 초기화.

    psycopg DB 드라이버 계측 및 FastAPI HTTP 요청 계측을 수행하며,
    /metrics, /healthz, /ready 등의 헬스/메트릭 경로는 추적에서 제외한다.
    """
    if tracer_provider is not None:
        provider = tracer_provider
    else:
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            return None

        traces_endpoint = endpoint
        if not traces_endpoint.endswith("/v1/traces"):
            traces_endpoint = f"{traces_endpoint.rstrip('/')}/v1/traces"

        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "mobility-collector"),
                "deployment.environment": os.getenv("DEPLOY_ENV", "dev"),
                "site": os.getenv("SITE_ID", "local"),
            }
        )

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=traces_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    # psycopg DB 드라이버 계측 (ConnectionPool 생성 전에 적용되어야 커넥션 래핑 작동)
    psycopg_inst = PsycopgInstrumentor()
    if not psycopg_inst.is_instrumented_by_opentelemetry:
        psycopg_inst.instrument(tracer_provider=provider)

    # FastAPI 계측 재설정 지원
    if getattr(app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.uninstrument_app(app)

    excluded_urls = os.getenv(
        "OTEL_PYTHON_EXCLUDED_URLS",
        ".*(/metrics|/healthz|/ready).*",
    )
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=excluded_urls,
    )
    return provider
