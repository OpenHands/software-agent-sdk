"""
Test that discriminated union schemas in OpenAPI have proper discriminator fields.

This ensures that Swagger UI can properly display discriminated unions instead of
showing them as "object | object | object...".
"""

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app


@pytest.fixture
def client():
    """Create a test client for the API."""
    return TestClient(create_app())


def test_action_schema_has_discriminator(client):
    """Test that Action schema has proper discriminator field."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_schema = response.json()

    # Check that Action schema exists
    assert "components" in openapi_schema
    assert "schemas" in openapi_schema["components"]
    schemas = openapi_schema["components"]["schemas"]

    assert "Action" in schemas, "Action schema should be in components/schemas"
    action_schema = schemas["Action"]

    # Check that it has oneOf
    assert "oneOf" in action_schema, "Action should have oneOf field"
    assert len(action_schema["oneOf"]) > 0, "Action should have at least one variant"

    # Check that all variants are $ref (not inline)
    for variant in action_schema["oneOf"]:
        assert "$ref" in variant, f"Each variant should be a $ref, got: {variant}"

    # Check that it has discriminator
    assert "discriminator" in action_schema, (
        "Action should have discriminator field for proper OpenAPI documentation"
    )

    # Check discriminator structure
    discriminator = action_schema["discriminator"]
    assert "propertyName" in discriminator, (
        "discriminator should have propertyName field"
    )
    assert discriminator["propertyName"] == "kind", (
        "discriminator propertyName should be 'kind'"
    )

    # Optionally check for mapping (though not strictly required)
    # if "mapping" in discriminator:
    #     # Mapping should have entries for each variant
    #     assert len(discriminator["mapping"]) > 0


def test_observation_schema_has_discriminator(client):
    """Test that Observation schema has proper discriminator field."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_schema = response.json()
    schemas = openapi_schema["components"]["schemas"]

    # Observation schema should also exist and have discriminator
    if "Observation" in schemas:
        observation_schema = schemas["Observation"]

        if "oneOf" in observation_schema:
            # Check that it has discriminator
            assert "discriminator" in observation_schema, (
                "Observation should have discriminator field"
            )

            discriminator = observation_schema["discriminator"]
            assert "propertyName" in discriminator, (
                "discriminator should have propertyName field"
            )
            assert discriminator["propertyName"] == "kind", (
                "discriminator propertyName should be 'kind'"
            )


def test_event_schema_has_discriminator(client):
    """Test that Event schema has proper discriminator field if it uses oneOf."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_schema = response.json()
    schemas = openapi_schema["components"]["schemas"]

    # Event schema might also be a discriminated union
    if "Event" in schemas:
        event_schema = schemas["Event"]

        if "oneOf" in event_schema:
            # Check that it has discriminator
            assert "discriminator" in event_schema, (
                "Event should have discriminator field"
            )

            discriminator = event_schema["discriminator"]
            assert "propertyName" in discriminator, (
                "discriminator should have propertyName field"
            )
            assert discriminator["propertyName"] == "kind", (
                "discriminator propertyName should be 'kind'"
            )


def test_action_variants_have_proper_schemas(client):
    """Test that Action variants (FinishAction, etc.) have proper schemas."""
    response = client.get("/openapi.json")
    assert response.status_code == 200

    openapi_schema = response.json()
    schemas = openapi_schema["components"]["schemas"]

    action_schema = schemas.get("Action", {})
    one_of = action_schema.get("oneOf", [])

    # Extract action type names from $refs
    action_types = []
    for variant in one_of:
        ref = variant.get("$ref", "")
        if ref.startswith("#/components/schemas/"):
            type_name = ref.split("/")[-1]
            action_types.append(type_name)

    # Check that referenced schemas exist and are proper objects
    for action_type in action_types:
        assert action_type in schemas, f"{action_type} should be in schemas"

        type_schema = schemas[action_type]

        # Should be an object
        assert type_schema.get("type") == "object", f"{action_type} should be an object"

        # Should have properties
        assert "properties" in type_schema, f"{action_type} should have properties"

        # Should have kind field with const value matching the type name
        properties = type_schema["properties"]
        assert "kind" in properties, f"{action_type} should have 'kind' field"

        kind_field = properties["kind"]
        assert "const" in kind_field or "enum" in kind_field, (
            f"{action_type}.kind should have const or enum"
        )

        # If const, it should match the type name
        if "const" in kind_field:
            assert kind_field["const"] == action_type, (
                f"{action_type}.kind const should be '{action_type}'"
            )

        # Should have title
        assert "title" in type_schema, (
            f"{action_type} should have title for better docs"
        )
