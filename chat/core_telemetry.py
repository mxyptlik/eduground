import logging
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logger = logging.getLogger(__name__)

def setup_telemetry():
    """Initialize OpenTelemetry tracing"""
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    logger.info("OpenTelemetry tracing initialized")
    return tracer_provider

def instrument_app(app):
    """Instrument FastAPI app with OpenTelemetry"""
    FastAPIInstrumentor.instrument_app(app)
    logger.info("FastAPI instrumented with OpenTelemetry")

def shutdown_telemetry(tracer_provider):
    """Shutdown telemetry gracefully"""
    tracer_provider.shutdown()
    logger.info("OpenTelemetry tracer provider shut down")