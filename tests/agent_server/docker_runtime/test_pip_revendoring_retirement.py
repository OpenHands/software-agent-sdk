"""Retirement guard for the Dockerfile's temporary `pip-revendored` stage.

Probes unmodified upstream pip in the `python-runtime` image, not the final
image where the workaround has already patched it.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
from packaging.version import Version


pytestmark = pytest.mark.docker_live

DOCKERFILE = (
    Path(__file__).resolve().parents[3]
    / "openhands-agent-server/openhands/agent_server/docker/Dockerfile"
)
FIXED_VERSIONS = {"urllib3": "2.8.0", "msgpack": "1.2.1"}

PROBE = """
import importlib
import json
import pip

versions = {"pip": pip.__version__}
for name in ("urllib3", "msgpack", "pkg_resources"):
    module_name = "pip._vendor." + name
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        versions[name] = None
    else:
        versions[name] = getattr(module, "__version__", "vendored")
print(json.dumps(versions))
"""


def test_upstream_pip_still_needs_revendoring():
    match = re.search(
        r"^FROM\s+(\S+)\s+AS\s+python-runtime\s*$",
        DOCKERFILE.read_text(),
        flags=re.MULTILINE | re.IGNORECASE,
    )
    assert match is not None, "Reassess the upstream Python image selection"
    upstream_image = match.group(1)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=always",
            "--network=none",
            "--entrypoint",
            "python",
            upstream_image,
            "-c",
            PROBE,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    versions = json.loads(result.stdout)

    assert all(versions[name] is not None for name in FIXED_VERSIONS), (
        f"Upstream pip's vendoring layout changed: {versions}. "
        "Reassess whether the pip-revendored workaround is still needed."
    )
    assert versions["pkg_resources"] is not None or any(
        Version(versions[name]) < Version(fixed)
        for name, fixed in FIXED_VERSIONS.items()
    ), (
        f"Upstream {upstream_image} now supplies patched pip vendors: {versions}. "
        "Remove the pip-revendored stage and custom pip-wheel installation; "
        "then rerun Trivy and the pip installation/HTTP-cache smoke tests. "
        "Remove this retirement test once the workaround is retired."
    )
