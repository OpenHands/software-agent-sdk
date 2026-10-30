"""A local OTLP/HTTP trace collector: what an OpenTelemetry collector (Jaeger,
Tempo, an OTel Collector) receives, kept in memory for a program to inspect.

    with Collector() as collector:
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = collector.endpoint
        ...                       # import and use the SDK
        collector.wait_for(lambda spans: ...)
        collector.spans           # [{"name", "trace_id", "span_id", "parent",
                                  #   "attributes", "kind"}]

Set the endpoint (and `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf`)
before the SDK is imported: observability is decided at import.
"""

from __future__ import annotations

import gzip
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)


def _value(v: Any) -> Any:
    kind = v.WhichOneof("value")
    if kind == "array_value":
        return [_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _value(kv.value) for kv in v.kvlist_value.values}
    return getattr(v, kind) if kind else None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        owner = self.server.owner  # type: ignore[attr-defined]
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.headers.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
        request = ExportTraceServiceRequest()
        request.ParseFromString(raw)
        with owner.lock:
            owner.requests.append({"path": self.path, "headers": dict(self.headers)})
            for rs in request.resource_spans:
                resource = {kv.key: _value(kv.value) for kv in rs.resource.attributes}
                for ss in rs.scope_spans:
                    for s in ss.spans:
                        owner.spans.append(
                            {
                                "name": s.name,
                                "trace_id": s.trace_id.hex(),
                                "span_id": s.span_id.hex(),
                                "parent": s.parent_span_id.hex() or None,
                                "kind": s.kind,
                                "scope": ss.scope.name,
                                "attributes": {
                                    kv.key: _value(kv.value) for kv in s.attributes
                                },
                                "resource": resource,
                            }
                        )
        body = ExportTraceServiceResponse().SerializeToString()
        self.send_response(200)
        self.send_header("content-type", "application/x-protobuf")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Collector:
    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def __enter__(self) -> Collector:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.owner = self  # type: ignore[attr-defined]
        self.endpoint = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/traces"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def wait_for(self, predicate, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if predicate(list(self.spans)):
                    return True
            time.sleep(0.2)
        return False


def ancestors(spans: list[dict[str, Any]], span: dict[str, Any]) -> list[str]:
    """Names of a span's ancestors, nearest first."""
    by_id = {s["span_id"]: s for s in spans}
    names, seen = [], set()
    while span.get("parent") in by_id and span["span_id"] not in seen:
        seen.add(span["span_id"])
        span = by_id[span["parent"]]
        names.append(span["name"])
    return names


def summarize(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Facts a recipe checks: names, the tree, and selected attributes."""
    by_id = {s["span_id"]: s for s in spans}

    def ancestors(span: dict[str, Any]) -> list[str]:
        names, seen = [], set()
        while (
            span.get("parent")
            and span["parent"] in by_id
            and span["span_id"] not in seen
        ):
            seen.add(span["span_id"])
            span = by_id[span["parent"]]
            names.append(span["name"])
        return names

    return {
        "count": len(spans),
        "names": sorted({s["name"] for s in spans}),
        "traces": len({s["trace_id"] for s in spans}),
        "ancestry": {s["name"]: ancestors(s) for s in spans},
    }
