"""Typed failures exposed by recorder services."""


class FlightRecorderError(Exception):
    """Base recorder failure."""

    code = "flight_recorder_error"


class TraceNotFound(FlightRecorderError):
    """A requested trace does not exist."""

    code = "trace_not_found"


class SpanNotFound(FlightRecorderError):
    """A requested span does not exist."""

    code = "span_not_found"


class InvalidBundle(FlightRecorderError):
    """A trace bundle violates the portable contract."""

    code = "invalid_bundle"


class UnsupportedFormat(InvalidBundle):
    """A trace bundle uses an unsupported format version."""

    code = "unsupported_format"


class RecorderClosed(FlightRecorderError):
    """Capture was attempted after recorder shutdown."""

    code = "recorder_closed"


class DiagnosticFailure(FlightRecorderError):
    """Diagnosis failed without affecting trace access."""

    code = "diagnostic_failure"
