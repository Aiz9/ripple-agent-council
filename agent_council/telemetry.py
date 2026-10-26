"""Session tracing: spans for the session, each phase, and each agent call.

Spans are always appended to the session's ``trace.jsonl`` (no dependencies).
When the optional ``otel`` extra is installed, the same spans also go to
OpenTelemetry: Council configures an exporter itself only when the standard
``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` variables
are set, or ``OTEL_TRACES_EXPORTER=console``. An application that has already
installed its own tracer provider keeps it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
import uuid

_PROVIDER_LOCK = threading.Lock()
_CONFIGURED = False


def _otel_tracer():
    """Return an OpenTelemetry tracer, or None when the API is not installed."""
    global _CONFIGURED
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    with _PROVIDER_LOCK:
        if not _CONFIGURED:
            _CONFIGURED = True
            wanted = os.environ.get("OTEL_TRACES_EXPORTER", "").strip().lower()
            endpoint = (os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
            default = type(trace.get_tracer_provider()).__name__ in ("ProxyTracerProvider", "NoOpTracerProvider")
            if default and wanted != "none" and (endpoint or wanted in ("otlp", "console")):
                try:
                    from opentelemetry.sdk.resources import Resource
                    from opentelemetry.sdk.trace import TracerProvider
                    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
                    if wanted == "console":
                        exporter = ConsoleSpanExporter()
                    else:
                        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                        exporter = OTLPSpanExporter()
                    service = os.environ.get("OTEL_SERVICE_NAME", "agent-council")
                    provider = TracerProvider(resource=Resource.create({"service.name": service}))
                    provider.add_span_processor(BatchSpanProcessor(exporter))
                    trace.set_tracer_provider(provider)
                except ImportError:
                    pass  # API without the SDK/exporter: spans become no-ops.
    return trace.get_tracer("agent_council")


def _otel_value(value):
    return value if isinstance(value, (str, bool, int, float)) else json.dumps(value, default=str)


class Span:
    def __init__(self, tracer: Tracer, name: str, parent: Span | None, attributes: dict):
        self.tracer, self.name, self.parent = tracer, name, parent
        self.span_id = uuid.uuid4().hex[:16]
        self.start = time.time()
        self.attributes = dict(attributes)
        self.status, self.ended = "ok", False
        self.otel = None
        if tracer.otel is not None:
            from opentelemetry import trace
            context = trace.set_span_in_context(parent.otel) if parent is not None and parent.otel is not None else None
            self.otel = tracer.otel.start_span(name, context=context)
            for key, value in self.attributes.items():
                if value is not None:
                    self.otel.set_attribute(f"council.{key}", _otel_value(value))

    def set(self, **attributes) -> None:
        self.attributes.update(attributes)
        if self.otel is not None:
            for key, value in attributes.items():
                if value is not None:
                    self.otel.set_attribute(f"council.{key}", _otel_value(value))

    def end(self, status: str = "ok", **attributes) -> None:
        if self.ended:
            return
        self.ended = True
        self.status = status
        self.set(**attributes)
        end = time.time()
        if self.otel is not None:
            from opentelemetry.trace import Status, StatusCode
            if status != "ok":
                self.otel.set_status(Status(StatusCode.ERROR, status))
            self.otel.end()
        self.tracer.write({"trace_id": self.tracer.trace_id, "span_id": self.span_id,
                           "parent_id": self.parent.span_id if self.parent else None, "name": self.name,
                           "start": round(self.start, 6), "end": round(end, 6),
                           "duration_ms": round((end - self.start) * 1000, 3), "status": status,
                           "attributes": self.attributes})


class Tracer:
    """Thread-safe span writer for one council invocation (a resume is a new trace)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.trace_id = uuid.uuid4().hex
        self.otel = _otel_tracer()
        self._lock = threading.Lock()

    def start(self, name: str, parent: Span | None = None, **attributes) -> Span:
        return Span(self, name, parent, attributes)

    def write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)


def read_trace(path: Path) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    spans = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a crash can leave a partial final line
        if isinstance(record, dict):
            spans.append(record)
    return spans
