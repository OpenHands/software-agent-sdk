#!/usr/bin/env python3
"""Ratchet weak types in the public Agent Server OpenAPI contract."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWLIST = (
    REPO_ROOT / ".github" / "agent-server-openapi-weak-schema-allowlist.json"
)
HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "options",
    "head",
    "trace",
}
EXPECTED_DISCRIMINATORS = {
    "/components/schemas/MCPAuthCredential": "strategy",
    "/components/schemas/MCPServer/properties/auth/anyOf/0": "strategy",
    "/components/schemas/MCPServerPatch/properties/auth/anyOf/0": "strategy",
    "/components/schemas/MCPTestRequest/properties/server": "type",
    "/components/schemas/MCPTestResponse": "ok",
}
EXPECTED_STRUCTURED_LOCATIONS = {
    "/components/schemas/AgentSettings",
    "/components/schemas/AgentSettings/properties/mcp_config",
    "/components/schemas/AgentSettingsPatch",
    "/components/schemas/AgentSettingsPatch/properties/mcp_config/anyOf/0",
    "/components/schemas/MCPAuthCredential",
    "/components/schemas/MCPConfig",
    "/components/schemas/MCPConfigPatch",
    "/components/schemas/MCPOAuthStateResponse",
    "/components/schemas/MCPServer",
    "/components/schemas/MCPServerPatch",
    "/components/schemas/MCPTestRequest",
    "/components/schemas/MCPTestResponse",
    "/components/schemas/RemoteMCPServer",
    "/components/schemas/SettingsResponse/properties/agent_settings",
    (
        "/components/schemas/SettingsResponse/properties/agent_settings/"
        "properties/mcp_config"
    ),
    (
        "/components/schemas/SettingsUpdateRequest/properties/"
        "agent_settings_diff/anyOf/0/properties/mcp_config"
    ),
    "/components/schemas/StdioMCPServer",
}


@dataclass(frozen=True, order=True)
class WeakLocation:
    pointer: str
    kind: str


@dataclass(frozen=True)
class AllowlistEntry:
    pointer: str
    kind: str
    reason: str
    owner: str
    expiry: dt.date | None = None
    follow_up: str | None = None

    @property
    def key(self) -> WeakLocation:
        return WeakLocation(pointer=self.pointer, kind=self.kind)


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _schema_is_unconstrained(schema: object) -> bool:
    if not isinstance(schema, dict) or not schema:
        return True
    if "$ref" in schema:
        return False
    if schema.get("additionalProperties") is True and not schema.get("properties"):
        return True
    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list):
        non_null = [
            variant
            for variant in variants
            if not (isinstance(variant, dict) and variant.get("type") == "null")
        ]
        return bool(non_null) and all(
            _schema_is_unconstrained(variant) for variant in non_null
        )
    return False


def _resolve_pointer(document: object, pointer: str) -> object:
    current = document
    for raw_token in pointer.removeprefix("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise KeyError(pointer)
            current = current[token]
        elif isinstance(current, list):
            current = current[int(token)]
        else:
            raise KeyError(pointer)
    return current


def _walk_schema(
    schema: object,
    pointer: str,
    findings: set[WeakLocation],
) -> None:
    if not isinstance(schema, dict):
        return
    if not schema:
        findings.add(WeakLocation(pointer=pointer, kind="empty-object-schema"))
        return
    if schema.get("additionalProperties") is True:
        findings.add(
            WeakLocation(
                pointer=f"{pointer}/additionalProperties",
                kind="unrestricted-additional-properties",
            )
        )

    for keyword in ("oneOf", "anyOf", "allOf", "prefixItems"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            for index, variant in enumerate(variants):
                _walk_schema(variant, f"{pointer}/{keyword}/{index}", findings)

    for keyword in (
        "items",
        "additionalProperties",
        "contains",
        "not",
        "if",
        "then",
        "else",
    ):
        nested = schema.get(keyword)
        if isinstance(nested, dict):
            _walk_schema(nested, f"{pointer}/{keyword}", findings)

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, nested in properties.items():
            _walk_schema(
                nested,
                f"{pointer}/properties/{_escape_pointer_token(name)}",
                findings,
            )


def _walk_operation_schemas(
    document: dict[str, Any],
    findings: set[WeakLocation],
) -> None:
    for path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        escaped_path = _escape_pointer_token(path)
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_pointer = f"/paths/{escaped_path}/{method}"

            request_body = operation.get("requestBody")
            if isinstance(request_body, dict):
                content = request_body.get("content")
                if isinstance(content, dict):
                    for media_type, media in content.items():
                        schema_pointer = (
                            f"{operation_pointer}/requestBody/content/"
                            f"{_escape_pointer_token(media_type)}/schema"
                        )
                        schema = (
                            media.get("schema") if isinstance(media, dict) else None
                        )
                        if schema is None:
                            findings.add(
                                WeakLocation(
                                    pointer=schema_pointer,
                                    kind="missing-request-schema",
                                )
                            )
                        else:
                            _walk_schema(schema, schema_pointer, findings)

            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            for status_code, response in responses.items():
                if not str(status_code).startswith("2") or not isinstance(
                    response, dict
                ):
                    continue
                content = response.get("content")
                if not isinstance(content, dict):
                    continue
                for media_type, media in content.items():
                    schema_pointer = (
                        f"{operation_pointer}/responses/"
                        f"{_escape_pointer_token(str(status_code))}/content/"
                        f"{_escape_pointer_token(media_type)}/schema"
                    )
                    schema = media.get("schema") if isinstance(media, dict) else None
                    if schema is None:
                        findings.add(
                            WeakLocation(
                                pointer=schema_pointer,
                                kind="missing-success-response-schema",
                            )
                        )
                    else:
                        _walk_schema(schema, schema_pointer, findings)


def find_weak_locations(document: dict[str, Any]) -> set[WeakLocation]:
    findings: set[WeakLocation] = set()
    _walk_operation_schemas(document, findings)

    schemas = document.get("components", {}).get("schemas", {})
    if isinstance(schemas, dict):
        for name, schema in schemas.items():
            _walk_schema(
                schema,
                f"/components/schemas/{_escape_pointer_token(name)}",
                findings,
            )
    return findings


def find_contract_quality_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for pointer, property_name in EXPECTED_DISCRIMINATORS.items():
        try:
            schema = _resolve_pointer(document, pointer)
        except KeyError:
            errors.append(f"{pointer}: required discriminated union is missing")
            continue
        discriminator = (
            schema.get("discriminator") if isinstance(schema, dict) else None
        )
        if (
            not isinstance(discriminator, dict)
            or discriminator.get("propertyName") != property_name
        ):
            errors.append(
                f"{pointer}: expected discriminator property {property_name!r}"
            )

    for pointer in sorted(EXPECTED_STRUCTURED_LOCATIONS):
        try:
            schema = _resolve_pointer(document, pointer)
        except KeyError:
            errors.append(f"{pointer}: required known contract location is missing")
            continue
        if _schema_is_unconstrained(schema):
            errors.append(f"{pointer}: known contract location is unconstrained")
    return errors


def load_allowlist(path: Path) -> list[AllowlistEntry]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("OpenAPI weak-schema allowlist must be a JSON array")

    entries: list[AllowlistEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Allowlist entry {index} must be an object")
        missing = {
            field
            for field in ("pointer", "kind", "reason", "owner")
            if not isinstance(item.get(field), str) or not item[field].strip()
        }
        if missing:
            raise ValueError(
                f"Allowlist entry {index} is missing: {', '.join(sorted(missing))}"
            )
        expiry_raw = item.get("expiry")
        expiry = dt.date.fromisoformat(expiry_raw) if expiry_raw else None
        follow_up = item.get("follow_up")
        if follow_up is not None and not isinstance(follow_up, str):
            raise ValueError(f"Allowlist entry {index} follow_up must be a string")
        entries.append(
            AllowlistEntry(
                pointer=item["pointer"],
                kind=item["kind"],
                reason=item["reason"],
                owner=item["owner"],
                expiry=expiry,
                follow_up=follow_up,
            )
        )
    return entries


def check_allowlist(
    findings: set[WeakLocation],
    entries: list[AllowlistEntry],
    *,
    today: dt.date | None = None,
) -> list[str]:
    today = today or dt.date.today()
    entry_by_key: dict[WeakLocation, AllowlistEntry] = {}
    errors: list[str] = []
    for entry in entries:
        if entry.key in entry_by_key:
            errors.append(
                f"{entry.pointer}: duplicate allowlist entry for {entry.kind}"
            )
        entry_by_key[entry.key] = entry
        if entry.expiry is not None and entry.expiry < today:
            expiry = entry.expiry.isoformat()
            errors.append(f"{entry.pointer}: allowlist entry expired on {expiry}")

    for finding in sorted(findings - set(entry_by_key)):
        errors.append(f"{finding.pointer}: new weak schema ({finding.kind})")
    for stale in sorted(set(entry_by_key) - findings):
        errors.append(f"{stale.pointer}: stale allowlist entry ({stale.kind})")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        type=Path,
        help="Exported OpenAPI JSON. Builds the current contract when omitted.",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=DEFAULT_ALLOWLIST,
        help="JSON allowlist of intentionally weak schema locations.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Print detected weak locations without checking the allowlist.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.schema is not None:
        document = json.loads(args.schema.read_text())
    else:
        from openhands.agent_server.openapi import build_public_openapi

        document = build_public_openapi()
    findings = find_weak_locations(document)
    contract_errors = find_contract_quality_errors(document)
    if args.list_only:
        for finding in sorted(findings):
            print(f"{finding.kind}\t{finding.pointer}")
        for error in contract_errors:
            print(f"contract-error\t{error}")
        return int(bool(contract_errors))

    try:
        allowlist = load_allowlist(args.allowlist)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Invalid OpenAPI weak-schema allowlist: {exc}")
        return 1

    errors = contract_errors + check_allowlist(findings, allowlist)
    if errors:
        print("Agent Server OpenAPI type-quality check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Agent Server OpenAPI type-quality check passed "
        f"({len(findings)} allowlisted weak locations)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
