"""Docker development workspace with on-the-fly image building capability."""

from pydantic import Field, model_validator

from openhands.sdk.workspace import PlatformType, TargetType

from .workspace import DockerWorkspace


class DockerDevWorkspace(DockerWorkspace):
    """Docker workspace with on-the-fly image building capability.

    This workspace extends DockerWorkspace to support building Docker images
    on-the-fly from a base image. This is useful for development and testing
    scenarios where you need to customize the agent server environment.

    Note: This class requires the OpenHands SDK workspace structure and should
    only be used within the OpenHands development environment or when you have
    the full SDK source code available.

    For production use cases with pre-built images, use DockerWorkspace instead.

    Example:
        with DockerDevWorkspace(
            base_image="python:3.12",
            target="source"
        ) as workspace:
            result = workspace.execute_command("ls -la")
    """

    # Add base_image support
    base_image: str | None = Field(
        default=None,
        description=(
            "Base Docker image to build the agent server from. "
            "Mutually exclusive with server_image."
        ),
    )

    # Add build-specific options
    target: TargetType = Field(
        default="source", description="Build target for the Docker image."
    )

    @model_validator(mode="after")
    def _validate_images(self):
        """Ensure exactly one of base_image or server_image is provided."""
        if (self.base_image is None) == (self.server_image is None):
            raise ValueError(
                "Exactly one of 'base_image' or 'server_image' must be set."
            )
        if self.base_image and "ghcr.io/openhands/agent-server" in self.base_image:
            raise ValueError(
                "base_image cannot be a pre-built agent-server image. "
                "Use server_image=... instead."
            )
        return self

    @staticmethod
    def _build_image_from_base(
        *, base_image: str, target: TargetType, platform: PlatformType
    ) -> str:
        """Build a Docker image from a base image.

        Args:
            base_image: The base Docker image to build from.
            target: The build target (e.g., 'source', 'dev').
            platform: The platform to build for (e.g., 'linux/amd64').

        Returns:
            The built Docker image tag.

        Raises:
            RuntimeError: If the base_image is a pre-built agent-server image
                or if the build fails.
        """
        from openhands.agent_server.docker.build import BuildOptions, build

        if "ghcr.io/openhands/agent-server" in base_image:
            raise RuntimeError(
                "base_image cannot be a pre-built agent-server image. "
                "Use server_image=... instead."
            )

        build_opts = BuildOptions(
            base_image=base_image,
            target=target,
            platforms=[platform],
            push=False,
        )
        tags = build(opts=build_opts)
        if not tags:
            raise RuntimeError("Build failed, no image tags returned")
        return tags[0]

    def get_image(self) -> str:
        """Build the image if base_image is provided, otherwise use server_image.

        This overrides the parent method to add on-the-fly image building
        capability.

        Returns:
            The Docker image tag to use.
        """
        if self.base_image:
            # Build the image from base_image
            return self._build_image_from_base(
                base_image=self.base_image,
                target=self.target,
                platform=self.platform,
            )
        elif self.server_image:
            # Use pre-built image
            return self.server_image
        else:
            raise ValueError("Either base_image or server_image must be set")
