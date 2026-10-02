"""Compare two dump_request.py outputs and report whether they are identical.

python .pr/compare_requests.py main.json branch.json
"""

import difflib
import json
import re
import sys


def load(path: str) -> dict:
    data = json.load(open(path, encoding="utf-8"))
    data["tools"] = {
        name: re.sub(r"working directory is: \S+", "working directory is: <tmp>", desc)
        for name, desc in data["tools"].items()
    }
    return data


main, branch = load(sys.argv[1]), load(sys.argv[2])
print(f"system message identical: {main['system'] == branch['system']}")
print(f"tool names and order identical: {list(main['tools']) == list(branch['tools'])}")
print(f"tool descriptions identical: {main['tools'] == branch['tools']}")
print(f"tool parameters identical: {main['tool_params'] == branch['tool_params']}")
for line in difflib.unified_diff(
    main["system"].splitlines(),
    branch["system"].splitlines(),
    "main",
    "branch",
    lineterm="",
):
    print(line)
