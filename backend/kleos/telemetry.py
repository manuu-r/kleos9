import json
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import trace
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter

SECRET_KEYS = re.compile(r"(api.?key|secret|password|authorization|cookie|token|credential)", re.I)
TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|Bearer\s+\S+)", re.I)
known_secrets: tuple[str, ...] = ()
correlation_id = ContextVar("kleos_trace", default=None)


def redact(value):
    if isinstance(value, dict):
        if value.get("part_kind") in {"thinking", "redacted-thinking"}:
            return {"part_kind": value["part_kind"], "content": "[private reasoning omitted]"}
        return {k: "[redacted]" if SECRET_KEYS.search(k) and not isinstance(v, (int, float))
                else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = TOKEN.sub("[redacted]", value)
        for secret in known_secrets:
            value = value.replace(secret, "[redacted]")
        def clean_url(match):
            try:
                parsed = urlsplit(match.group())
                return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
            except ValueError:
                # User/model text may contain incomplete URLs. Drop the entire
                # token rather than leaking credentials or interrupting capture.
                return "[redacted URL]"
        return re.sub(r"https?://[^\s<>\"']+", clean_url, value)
    return value


tracer = trace.get_tracer("kleos")


def trace_id():
    if correlation_id.get():
        return correlation_id.get()
    span = trace.get_current_span().get_span_context()
    return format(span.trace_id, "032x") if span.is_valid else None


@contextmanager
def linked_span(name, logical_trace_id, **attributes):
    context = None
    if logical_trace_id:
        parent = trace.SpanContext(trace_id=int(logical_trace_id.replace("-", ""), 16), span_id=1,
                                   is_remote=True, trace_flags=trace.TraceFlags(1))
        context = trace.set_span_in_context(trace.NonRecordingSpan(parent))
    token = correlation_id.set(logical_trace_id)
    try:
        with tracer.start_as_current_span(name, context=context, attributes=attributes) as span:
            yield span
    finally:
        correlation_id.reset(token)


class SafeFormatter(logging.Formatter):
    def format(self, record):
        # Exception bodies and HTTP headers can contain provider credentials or user content.
        return json.dumps({"level": record.levelname, "logger": record.name,
                           "message": redact(record.getMessage()), "traceId": trace_id()}, default=str)


class SafeAccessFormatter(logging.Formatter):
    """Uvicorn owns a separate handler; never interpolate its raw request target."""

    def format(self, record):
        fields = {"level": record.levelname, "logger": "uvicorn.access", "traceId": trace_id()}
        if isinstance(record.args, tuple) and len(record.args) == 5:
            _address, method, target, _version, status = record.args
            fields.update(method=str(method), path=str(target).split("?", 1)[0].split("#", 1)[0],
                          status=status)
        else:
            fields["message"] = "HTTP access event"
        return json.dumps(redact(fields), default=str)


def metric(name, value, unit="Count", **dimensions):
    """CloudWatch Embedded Metric Format; no per-project dimension cardinality."""
    print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
        "Namespace": "Kleos9", "Dimensions": [list(dimensions)],
        "Metrics": [{"Name": name, "Unit": unit}]}]}, **dimensions, name: value}), flush=True)


def trace_attributes(attributes):
    """Sanitize automatic instrumentation as well as application-owned spans."""
    safe = {}
    for key, value in (attributes or {}).items():
        if key in {"exception.message", "exception.stacktrace", "db.statement", "db.query.text", "url.query"}:
            continue
        if key.startswith(("gen_ai.input", "gen_ai.output", "gen_ai.prompt", "gen_ai.completion",
                           "http.request.header", "http.response.header")):
            continue  # Detailed AI records have their own redacted, audited storage.
        if key in {"http.target", "url.path"} and isinstance(value, str):
            value = value.split("?", 1)[0].split("#", 1)[0]
        safe[key] = redact({key: list(value) if isinstance(value, tuple) else value})[key]
    return safe


class RedactingSpanExporter(SpanExporter):
    """Prevent callback codes, signed URLs and exception bodies reaching collectors."""

    def __init__(self, delegate):
        self.delegate = delegate

    def export(self, spans):
        sanitized = [ReadableSpan(
            name=redact(span.name), context=span.context, parent=span.parent,
            resource=span.resource, attributes=trace_attributes(span.attributes),
            events=[Event(redact(event.name), trace_attributes(event.attributes), event.timestamp)
                    for event in span.events],
            links=[trace.Link(link.context, trace_attributes(link.attributes)) for link in span.links],
            kind=span.kind, status=trace.Status(span.status.status_code),
            start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        ) for span in spans]
        return self.delegate.export(sanitized)

    def shutdown(self):
        return self.delegate.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.delegate.force_flush(timeout_millis)


def configure(endpoint: str | None, settings=None):
    global known_secrets
    if settings:
        known_secrets = tuple(value.get_secret_value() for name in settings.__class__.model_fields
                              if (value := getattr(settings, name)) and hasattr(value, "get_secret_value")
                              and len(value.get_secret_value()) >= 8)
    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SafeFormatter())
    access = logging.getLogger("uvicorn.access")
    # Uvicorn's non-propagating handler is not covered by the root formatter.
    for handler in access.handlers:
        handler.setFormatter(SafeAccessFormatter())
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider = TracerProvider(resource=Resource.create({"service.name": "kleos-backend"}))
        provider.add_span_processor(BatchSpanProcessor(RedactingSpanExporter(OTLPSpanExporter(endpoint=endpoint))))
        trace.set_tracer_provider(provider)
