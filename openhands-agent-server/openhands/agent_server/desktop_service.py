"""Desktop service for launching VNC desktop via desktop_launch.sh script."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from openhands.agent_server.config import get_default_config
from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env


logger = get_logger(__name__)


class DesktopService:
    """Simple desktop service that launches desktop_launch.sh script."""

    def __init__(self, connection_token: str | None = None):
        self._proc: asyncio.subprocess.Process | None = None
        self.novnc_port: int = int(os.getenv("NOVNC_PORT", "8002"))
        self.connection_token: str | None = connection_token
        self.token_file: Path | None = None

    async def start(self) -> bool:
        """Start the VNC desktop stack."""
        if self.is_running():
            logger.info("Desktop already running")
            return True

        # --- Env defaults (match bash behavior) ---
        env = sanitized_env()
        display = env.get("DISPLAY", ":1")
        user = env.get("USER") or env.get("USERNAME") or "openhands"
        home = Path(env.get("HOME") or f"/home/{user}")
        vnc_geometry = env.get("VNC_GEOMETRY", "1280x800")
        # Use websockify directly for token authentication support
        websockify_bin = Path("/usr/bin/websockify")
        novnc_web = Path("/usr/share/novnc")

        # --- Dirs & ownership (idempotent) ---
        try:
            for p in (home / ".vnc", home / ".config", home / "Downloads"):
                p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed preparing directories/ownership: %s", e)
            return False

        # --- Generate connection token if not set ---
        if self.connection_token is None:
            self.connection_token = os.urandom(32).hex()
            logger.info("Generated VNC connection token")

        # --- Create token file for websockify authentication ---
        try:
            self.token_file = home / ".vnc" / "websockify-tokens.conf"
            # Token file format: <token>: <host>:<port>
            # Note: space after colon is important
            self.token_file.write_text(f"{self.connection_token}: 127.0.0.1:5901\n")
            self.token_file.chmod(0o600)
            logger.info("Created websockify token file at %s", self.token_file)
        except Exception as e:
            logger.error("Failed creating websockify token file: %s", e)
            return False

        # --- xstartup for XFCE (create once) ---
        xstartup = home / ".vnc" / "xstartup"
        if not xstartup.exists():
            try:
                xstartup.write_text(
                    "#!/bin/sh\n"
                    "unset SESSION_MANAGER\n"
                    "unset DBUS_SESSION_BUS_ADDRESS\n"
                    "exec startxfce4\n"
                )
                xstartup.chmod(0o755)
            except Exception as e:
                logger.error("Failed writing xstartup: %s", e)
                return False

        # --- Start TigerVNC if not running (bind to loopback; novnc proxies) ---
        try:
            # Roughly equivalent to: pgrep -f "Xvnc .*:1"
            xvnc_running = (
                subprocess.run(
                    ["pgrep", "-f", f"Xvnc .*{display}"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    env=env,
                ).returncode
                == 0
            )
        except Exception:
            xvnc_running = False

        if not xvnc_running:
            logger.info("Starting TigerVNC on %s (%s)...", display, vnc_geometry)
            # vncserver <DISPLAY> -geometry <geom> -depth 24 -localhost yes
            # Note: We use -localhost yes to ensure VNC only listens on localhost
            # Authentication is handled by websockify token plugin
            rc = subprocess.run(
                [
                    "vncserver",
                    display,
                    "-geometry",
                    vnc_geometry,
                    "-depth",
                    "24",
                    "-localhost",
                    "yes",
                    "-SecurityTypes",
                    "None",
                ],
                env=env,
            ).returncode
            if rc != 0:
                logger.error("vncserver failed with rc=%s", rc)
                return False

        # --- Start websockify/noVNC proxy (as our foreground/managed process) ---
        # Check if websockify is already running on this port
        try:
            websockify_running = (
                subprocess.run(
                    ["pgrep", "-f", rf"websockify.*{self.novnc_port}"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    env=env,
                ).returncode
                == 0
            )
        except Exception:
            websockify_running = False

        if websockify_running:
            logger.info("websockify already running on port %d", self.novnc_port)
            self._proc = None  # we didn't start it; don't own its lifecycle
        else:
            if not websockify_bin.exists():
                logger.error("websockify not found at %s", websockify_bin)
                return False
            logger.info(
                (
                    "Starting websockify/noVNC proxy on 0.0.0.0:%d with token "
                    "authentication (token file: %s) ..."
                ),
                self.novnc_port,
                self.token_file,
            )
            try:
                # Store this as the managed long-running process
                # Use token-based authentication with TokenFile plugin
                # Format: websockify [options] --token-plugin=CLASS [host:]port
                self._proc = await asyncio.create_subprocess_exec(
                    str(websockify_bin),
                    f"0.0.0.0:{self.novnc_port}",
                    "--web",
                    str(novnc_web),
                    "--token-plugin",
                    "TokenFile",
                    "--token-source",
                    str(self.token_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
            except Exception as e:
                logger.error("Failed to start websockify: %s", e)
                return False

        logger.info(
            "noVNC URL: http://localhost:%d/vnc.html?autoconnect=1&resize=remote",
            self.novnc_port,
        )

        # Small grace period so callers relying on your old sleep(2) don't break
        await asyncio.sleep(2)

        # Final sanity: either our managed noVNC is alive or Xvnc is alive
        if (self._proc and self._proc.returncode is None) or self.is_running():
            logger.info("Desktop started successfully")
            return True

        logger.error("Desktop failed to start (noVNC/Xvnc not healthy)")
        return False

    async def stop(self) -> None:
        """Stop the desktop process."""
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
                logger.info("Desktop stopped")
            except TimeoutError:
                logger.warning("Desktop did not stop gracefully, killing process")
                self._proc.kill()
                await self._proc.wait()
            except Exception as e:
                logger.error("Error stopping desktop: %s", e)
            finally:
                self._proc = None

    def is_running(self) -> bool:
        """Check if desktop is running."""
        if self._proc and self._proc.returncode is None:
            return True

        # Check if VNC server is running
        try:
            result = subprocess.run(
                ["pgrep", "-f", "Xvnc"],
                capture_output=True,
                text=True,
                timeout=3,
                env=sanitized_env(),
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_vnc_url(self, base: str = "http://localhost:8003") -> str | None:
        """Get the noVNC URL for desktop access with authentication token.

        With websockify TokenFile plugin, the token must be in the WebSocket path.
        noVNC uses the 'path' parameter to construct the WebSocket URL, so we
        include the token as a query parameter within the path value itself.

        Args:
            base: Base URL for the noVNC server

        Returns:
            noVNC URL with token if available, None otherwise
        """
        if not self.is_running():
            return None
        if self.connection_token is None:
            return None
        return (
            f"{base}/vnc.html?"
            f"path=websockify?token={self.connection_token}&"
            f"autoconnect=1&resize=remote"
        )


# ------- module-level accessor -------

_desktop_service: DesktopService | None = None


def get_desktop_service() -> DesktopService | None:
    """Get the desktop service instance if VNC is enabled."""
    global _desktop_service
    config = get_default_config()

    if not config.enable_vnc:
        logger.info("VNC desktop is disabled in configuration")
        return None

    if _desktop_service is None:
        connection_token = None
        if config.session_api_keys:
            connection_token = config.session_api_keys[0]
        _desktop_service = DesktopService(connection_token=connection_token)
    return _desktop_service
