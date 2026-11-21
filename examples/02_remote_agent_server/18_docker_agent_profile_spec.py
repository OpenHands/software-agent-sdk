"""Select a local Docker image from an agent profile's sandbox specification.

Run with no arguments for the mapped image, or pass `--default-image` to
leave image selection to DockerWorkspace. No model call is made.
"""

import argparse
import os
import platform
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from openhands.sdk.profiles import (
    AgentProfileStore,
    OpenHandsAgentProfile,
    ProfileSandboxSettings,
)
from openhands.sdk.workspace.models import PlatformType
from openhands.workspace import DockerWorkspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--spec-id", default="python-tools")
    options.add_argument("--default-image", action="store_true")
    args = parser.parse_args()

    spec_id = None if args.default_image else args.spec_id
    with TemporaryDirectory() as profiles_dir:
        store = AgentProfileStore(base_dir=Path(profiles_dir))
        store.save(
            OpenHandsAgentProfile(
                name="local-development",
                llm_profile_ref="default",
                sandbox=(
                    ProfileSandboxSettings(spec_id=spec_id)
                    if spec_id is not None
                    else None
                ),
            )
        )
        profile = store.load("local-development")

    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"
    docker_platform: PlatformType = "linux/arm64" if arch == "arm64" else "linux/amd64"
    sha = os.getenv("SDK_SHA") or os.getenv("GITHUB_SHA")
    ci_image = (
        f"ghcr.io/openhands/agent-server:{sha[:7]}-python-{arch}" if sha else None
    )
    # The launcher owns the catalog; a spec ID is not itself an image name.
    images = {
        "python-tools": ci_image or "ghcr.io/openhands/agent-server:latest-python"
    }
    image = ci_image
    if profile.sandbox is not None:
        selected_spec = profile.sandbox.spec_id
        if selected_spec not in images:
            parser.error(f"Unknown sandbox spec ID: {selected_spec!r}")
        image = images[selected_spec]

    workspace = (
        DockerWorkspace(platform=docker_platform, server_image=image)
        if image is not None
        else DockerWorkspace(platform=docker_platform)
    )
    with workspace:
        result = workspace.execute_command("hostname")
        container_id = result.stdout.strip()
        # Docker defaults the container's hostname to its short ID.
        if result.exit_code != 0 or not re.fullmatch(r"[a-f0-9]{12}", container_id):
            raise RuntimeError(f"Could not identify the sandbox container: {result}")
        actual_image = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "container",
                "inspect",
                "--format",
                "{{.Config.Image}}",
                container_id,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        expected_image = image or "ghcr.io/openhands/agent-server:latest-python"
        if actual_image != expected_image:
            raise RuntimeError(f"Expected {expected_image}, got {actual_image}")
        print(f"Using Docker image: {actual_image}")
    print("EXAMPLE_COST: 0")


if __name__ == "__main__":
    main()
