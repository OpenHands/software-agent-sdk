#!/usr/bin/env python3
"""
Generate a .env file containing all config options
"""

import argparse

from openhands.agent_server.config import load_config
from openhands.agent_server.env_parser import to_env


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a .env file containing all config options"
    )
    parser.add_argument("--file", default=".env", help="File path")
    args = parser.parse_args()
    print(f"🛠️ Building: {args.file}")
    with open(args.file, "w") as f:
        content = to_env(load_config(), "OH")
        f.write(content)
