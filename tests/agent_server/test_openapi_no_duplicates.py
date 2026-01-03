"""Test that OpenAPI schema has no duplicate titles."""

from collections import defaultdict

from openhands.agent_server.api import api


def test_openapi_schema_no_duplicate_titles():
    """Ensure each schema title appears only once in the OpenAPI spec.

    This prevents Swagger UI from showing duplicate entries like:
    - Agent, Agent (expand all -> both show 'object')
    - AgentContext, AgentContext

    Note: Pydantic generates separate schemas for Input and Output serialization modes
    (e.g., Agent-Input, Agent-Output) which share the same title. These are expected
    and acceptable duplicates. We only flag unexpected duplicates.
    """
    schema = api.openapi()
    schemas = schema.get("components", {}).get("schemas", {})

    # Group schemas by their title
    title_to_names = defaultdict(list)
    for schema_name, schema_def in schemas.items():
        if isinstance(schema_def, dict):
            title = schema_def.get("title", schema_name)
            title_to_names[title].append(schema_name)

    # Find duplicates, but filter out expected Input/Output pairs
    duplicates = {}
    for title, names in title_to_names.items():
        if len(names) > 1:
            # Check if all duplicates are expected Input/Output variants
            input_output_only = all(
                name.endswith("-Input") or name.endswith("-Output") for name in names
            )
            if not input_output_only:
                # Only flag as duplicate if there are non-Input/Output variants
                duplicates[title] = names

    assert not duplicates, (
        f"Found schemas with unexpected duplicate titles: {duplicates}. "
        "Each title should appear only once in the OpenAPI schema (Input/Output pairs are expected)."
    )
