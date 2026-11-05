import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(app: FastAPI) -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

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

    psycopg_inst = PsycopgInstrumentor()
    if not psycopg_inst.is_instrumented_by_opentelemetry:
        psycopg_inst.instrument()

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=".*(/metrics|/healthz|/ready).*",
    )
