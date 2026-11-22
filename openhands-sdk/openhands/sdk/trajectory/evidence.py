from typing import Any

from openhands.sdk.trajectory.journal import digest, encode


def model_input(data: dict[str, Any], payloads: tuple[Any, ...]) -> dict[str, Any]:
    tools, *inputs = payloads
    count = data["input_count"]
    if count is not None and len(inputs) != count:
        raise ValueError("Missing ordered model input")
    return {
        **data["parameters"],
        "tools": tools,
        data["input_key"]: data["input_value"] if count is None else inputs,
    }


def boundary_evidence(
    kind: str, run_id: str | None, data: dict[str, Any], payloads: tuple[Any, ...]
) -> tuple[str, str] | None:
    values = {key: value for key, value in data.items() if key != "payload_refs"}
    match kind:
        case "run_start" | "run_end":
            key = run_id
        case "attempt_start":
            key = data["attempt_id"]
            values = {
                "run_id": run_id,
                "request_id": data["request_id"],
                "attempt": data.get("attempt", 1),
                "adapter": data["adapter"],
                "input": model_input(data, payloads),
                "unsupported_options": data.get("unsupported_options", []),
            }
        case "attempt_response":
            key = data["attempt_id"]
            values = payloads[0]
        case "attempt_end" | "stream_limit":
            key = data["attempt_id"]
        case "stream_fragment":
            key = f"{data['attempt_id']}:{data['index']}"
            values = payloads[0]
        case "request_end":
            key = data["request_id"]
        case "event" | "model_event" | "tool_result":
            key = data["event_id"]
            if kind == "event":
                values = payloads[0]
            elif kind == "tool_result":
                values = {**values, "value": payloads[0]}
        case "tool_start":
            key = data["execution_id"]
        case "capture_gap":
            key = data["gap_id"]
        case _:
            return None
    return f"{kind}:{key}", digest(encode(values))
