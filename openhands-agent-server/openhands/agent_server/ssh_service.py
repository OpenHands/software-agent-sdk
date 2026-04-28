"""SSH service for managing sshd in the agent server."""

import asyncio
import os
import subprocess
from pathlib import Path

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

SSH_PORT = 2222
SSH_PUBLIC_KEYS_ENV = "OH_SSH_PUBLIC_KEYS"
SSH_HOST_KEYS_DIR = Path("/etc/ssh/host_keys")


class SSHServiceStatus:
    """Status information for the SSH service."""

    def __init__(
        self,
        enabled: bool = True,
        running: bool = False,
        error: str | None = None,
        port: int = SSH_PORT,
    ):
        self.enabled = enabled
        self.running = running
        self.error = error
        self.port = port

    def to_dict(self) -> dict:
        """Convert status to dictionary for API responses."""
        return {
            "enabled": self.enabled,
            "running": self.running,
            "error": self.error,
            "port": self.port,
        }


class SSHService:
    """Service to manage SSH server startup."""

    def __init__(self, port: int = SSH_PORT):
        """Initialize SSH service.

        Args:
            port: Port to run SSH server on (default: 2222)
        """
        self.port: int = port
        self.process: asyncio.subprocess.Process | None = None
        self._status = SSHServiceStatus(enabled=True, port=port)

    def _generate_host_keys(self) -> bool:
        """Generate SSH host keys if they don't exist.

        Host keys are generated per-container at startup for security.
        Each container gets unique keys to ensure proper host identity verification.

        Returns:
            True if keys exist or were generated successfully, False otherwise
        """
        key_types = [
            ("ed25519", "ssh_host_ed25519_key"),
            ("rsa", "ssh_host_rsa_key"),
            ("ecdsa", "ssh_host_ecdsa_key"),
        ]

        try:
            SSH_HOST_KEYS_DIR.mkdir(parents=True, exist_ok=True)

            for key_type, key_name in key_types:
                key_path = SSH_HOST_KEYS_DIR / key_name
                if not key_path.exists():
                    logger.info(f"Generating SSH host key: {key_name}")
                    result = subprocess.run(
                        [
                            "ssh-keygen",
                            "-t",
                            key_type,
                            "-f",
                            str(key_path),
                            "-N",
                            "",  # No passphrase
                            "-q",  # Quiet mode
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        logger.error(
                            f"Failed to generate {key_type} host key: {result.stderr}"
                        )
                        return False

            logger.info("SSH host keys ready")
            return True

        except Exception as e:
            logger.error(f"Failed to generate SSH host keys: {e}")
            return False

    def _setup_authorized_keys(self) -> int:
        """Set up authorized_keys file from environment variable.

        Returns the number of keys added.
        """
        ssh_keys_str = os.environ.get(SSH_PUBLIC_KEYS_ENV, "")
        if not ssh_keys_str:
            return 0

        # SSH keys are passed as newline-separated values
        ssh_keys = [k.strip() for k in ssh_keys_str.split("\n") if k.strip()]
        if not ssh_keys:
            return 0

        # SSH directory for openhands user
        ssh_dir = Path("/home/openhands/.ssh")

        # Create .ssh directory if it doesn't exist
        ssh_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(ssh_dir, 0o700)

        # Write authorized_keys file
        authorized_keys_path = ssh_dir / "authorized_keys"
        with open(authorized_keys_path, "w") as f:
            for key in ssh_keys:
                f.write(f"{key}\n")

        os.chmod(authorized_keys_path, 0o600)

        # Try to set correct ownership (may fail if not root)
        try:
            import pwd

            pw = pwd.getpwnam("openhands")
            os.chown(ssh_dir, pw.pw_uid, pw.pw_gid)
            os.chown(authorized_keys_path, pw.pw_uid, pw.pw_gid)
        except (KeyError, PermissionError):
            pass

        logger.info(f"Added {len(ssh_keys)} SSH public key(s) to authorized_keys")
        return len(ssh_keys)

    async def start(self) -> bool:
        """Start the SSH server.

        Returns:
            True if started successfully, False otherwise
        """
        try:
            # Check if sshd binary exists
            if not self._check_sshd_available():
                error_msg = "SSH server (sshd) is not installed in this container"
                logger.warning(f"{error_msg}, SSH will be disabled")
                self._status.error = error_msg
                return False

            # Check if port is available
            if not await self._is_port_available():
                error_msg = f"SSH port {self.port} is already in use"
                logger.warning(f"{error_msg}, SSH will be disabled")
                self._status.error = error_msg
                return False

            # Generate host keys if they don't exist (per-container for security)
            if not self._generate_host_keys():
                error_msg = "Failed to generate SSH host keys"
                logger.warning(f"{error_msg}, SSH will be disabled")
                self._status.error = error_msg
                return False

            # Set up authorized_keys from environment variable
            num_keys = self._setup_authorized_keys()

            if num_keys == 0:
                error_msg = (
                    "No SSH public keys configured. "
                    "Add your SSH public key in Settings > SSH Keys to enable access."
                )
                logger.warning(f"{error_msg} SSH server will not start.")
                self._status.error = error_msg
                return False

            # Start sshd in the foreground (will be managed by this process)
            await self._start_sshd_process()

            logger.info(
                f"SSH server started on port {self.port} with {num_keys} "
                f"authorized key(s). Connect using: ssh -p {self.port} openhands@<host>"
            )
            self._status.running = True
            self._status.error = None
            return True

        except Exception as e:
            error_msg = f"Failed to start SSH server: {e}"
            logger.error(error_msg)
            self._status.error = error_msg
            return False

    async def stop(self) -> None:
        """Stop the SSH server."""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                logger.info("SSH server stopped successfully")
            except TimeoutError:
                logger.warning("SSH server did not stop gracefully, killing process")
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.error(f"Error stopping SSH server: {e}")
            finally:
                self.process = None
                self._status.running = False

    def is_running(self) -> bool:
        """Check if SSH server is running.

        Returns:
            True if running, False otherwise
        """
        return self.process is not None and self.process.returncode is None

    def get_status(self) -> SSHServiceStatus:
        """Get the current SSH service status.

        Returns:
            SSHServiceStatus with current state information
        """
        # Update running status in case process exited unexpectedly
        if self._status.running and not self.is_running():
            self._status.running = False
            self._status.error = "SSH server process exited unexpectedly"
        return self._status

    def _check_sshd_available(self) -> bool:
        """Check if sshd binary is available.

        Returns:
            True if available, False otherwise
        """
        return Path("/usr/sbin/sshd").exists()

    async def _is_port_available(self) -> bool:
        """Check if the specified port is available.

        Returns:
            True if port is available, False otherwise
        """
        try:
            # Try to bind to the port
            server = await asyncio.start_server(
                lambda _r, _w: None, "0.0.0.0", self.port
            )
            server.close()
            await server.wait_closed()
            return True
        except OSError:
            return False

    async def _start_sshd_process(self) -> None:
        """Start the sshd server process."""
        # Run sshd in foreground mode (-D) on the specified port
        cmd = f"/usr/sbin/sshd -D -p {self.port}"

        # Start the process
        self.process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        # Give sshd a moment to start and check if it's still running
        await asyncio.sleep(0.5)

        if self.process.returncode is not None:
            # Process already exited - there was an error
            if self.process.stdout:
                output = await self.process.stdout.read()
                logger.error(f"sshd failed to start: {output.decode()}")
            raise RuntimeError(f"sshd exited with code {self.process.returncode}")


# Global SSH service instance
_ssh_service: SSHService | None = None
# Track if SSH was explicitly disabled in config
_ssh_disabled_in_config: bool = False


def get_ssh_service() -> SSHService | None:
    """Get the global SSH service instance.

    Returns:
        SSH service instance if enabled, None if disabled
    """
    global _ssh_service, _ssh_disabled_in_config
    if _ssh_service is None and not _ssh_disabled_in_config:
        from openhands.agent_server.config import get_default_config

        config = get_default_config()

        if not config.enable_ssh:
            logger.info("SSH is disabled in configuration")
            _ssh_disabled_in_config = True
            return None
        else:
            _ssh_service = SSHService(port=config.ssh_port)
    return _ssh_service


def get_ssh_status() -> dict:
    """Get the current SSH service status.

    Returns a dictionary with SSH status information that can be used
    by the frontend to show appropriate error messages.

    Returns:
        Dictionary with keys:
        - enabled: Whether SSH is enabled in config
        - running: Whether SSH server is currently running
        - error: Error message if SSH failed to start, or None
        - port: The SSH port number
    """
    service = get_ssh_service()

    if _ssh_disabled_in_config:
        return {
            "enabled": False,
            "running": False,
            "error": "SSH is disabled in server configuration",
            "port": SSH_PORT,
        }

    if service is None:
        return {
            "enabled": False,
            "running": False,
            "error": "SSH service not initialized",
            "port": SSH_PORT,
        }

    return service.get_status().to_dict()
