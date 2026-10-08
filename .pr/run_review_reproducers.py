"""Run VascoSch92's F1-F7 reproducers in isolated subprocesses with cleanup."""

import json
import re
import subprocess
import sys


comments = json.loads(
    subprocess.check_output(
        ["gh", "api", "--paginate", "repos/OpenHands/software-agent-sdk/pulls/5567/comments"],
        text=True,
    )
)
for comment in comments:
    if comment["user"]["login"] != "VascoSch92":
        continue
    blocks = re.findall(r"```python\n(.*?)```", comment["body"], re.DOTALL)
    for code in blocks:
        wrapper = (
            "namespace = {}\n"
            "try:\n"
            f"    exec({code!r}, namespace)\n"
            "finally:\n"
            "    for name in ('ex', 'pool', 'session'):\n"
            "        resource = namespace.get(name)\n"
            "        if resource is not None:\n"
            "            try:\n"
            "                resource.close()\n"
            "            except Exception:\n"
            "                pass\n"
        )
        print(f"\n=== {comment['html_url']} ===", flush=True)
        result = subprocess.run(
            [sys.executable, "-I", "-c", wrapper],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=75,
        )
        print(result.stdout, flush=True)
        print(f"process exit: {result.returncode}", flush=True)
