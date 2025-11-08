#!/usr/bin/env python3

import json
import os
from pathlib import Path
from typing import Any

from openhands.agent_server.api import api


def generate_openapi_schema() -> dict[str, Any]:
    """Generate an OpenAPI schema"""
    openapi = api.openapi()
    return openapi


if __name__ == "__main__":
    schema_path = Path(os.environ["SCHEMA_PATH"])
    schema = generate_openapi_schema()
    schema_path.write_text(json.dumps(schema, indent=2))
    print(f"Wrote {schema_path}")
