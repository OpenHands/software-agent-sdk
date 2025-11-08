"""Test that OpenAPI schema has no duplicate titles."""

from collections import defaultdict

from openhands.agent_server.api import api


def test_openapi_schema_no_duplicate_titles():
    """Ensure each schema title appears only once in the OpenAPI spec.

    This prevents Swagger UI from showing duplicate entries like:
    - Agent, Agent (expand all -> both show 'object')
    - AgentContext, AgentContext

    The deduplication logic in utils.py should remove any duplicates caused by
    Pydantic generating both base and mode-specific (*-Input, *-Output) schemas.
    """
    schema = api.openapi()
    schemas = schema.get("components", {}).get("schemas", {})

    # Group schemas by their title
    title_to_names = defaultdict(list)
    for schema_name, schema_def in schemas.items():
        if isinstance(schema_def, dict):
            title = schema_def.get("title", schema_name)
            title_to_names[title].append(schema_name)

    # Find any duplicates
    duplicates = {
        title: names for title, names in title_to_names.items() if len(names) > 1
    }

    assert not duplicates, (
        f"Found schemas with duplicate titles: {duplicates}. "
        "Each title should appear only once in the OpenAPI schema."
    )
