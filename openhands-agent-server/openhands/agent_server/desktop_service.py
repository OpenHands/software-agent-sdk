"""Desktop service for launching VNC desktop via desktop_launch.sh script."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from openhands.agent_server.config import get_default_config
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class DesktopService:
    """Simple desktop service that launches desktop_launch.sh script."""

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self.novnc_port: int = int(os.getenv("NOVNC_PORT", "8002"))

    async def start(self) -> bool:
        """Start the VNC desktop stack."""
        if self.is_running():
            logger.info("Desktop already running")
            return True

        # --- Env defaults (match bash behavior) ---
        env = os.environ.copy()
        display = env.get("DISPLAY", ":1")
        user = env.get("USER") or env.get("USERNAME") or "openhands"
        home = Path(env.get("HOME") or f"/home/{user}")
        vnc_geometry = env.get("VNC_GEOMETRY", "1280x800")
        novnc_proxy = Path("/usr/share/novnc/utils/novnc_proxy")
        novnc_web = Path(env.get("NOVNC_WEB", "/opt/novnc-web"))

        # --- Dirs & ownership (idempotent) ---
        try:
            for p in (home / ".vnc", home / ".config", home / "Downloads"):
                p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed preparing directories/ownership: %s", e)
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
                ).returncode
                == 0
            )
        except Exception:
            xvnc_running = False

        if not xvnc_running:
            logger.info("Starting TigerVNC on %s (%s)...", display, vnc_geometry)
            # vncserver <DISPLAY> -geometry <geom> -depth 24 -localhost yes
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

        # --- Start noVNC proxy (as our foreground/managed process) ---
        # Equivalent to: pgrep -f "[n]ovnc_proxy .*--listen .*<port>"
        try:
            novnc_running = (
                subprocess.run(
                    ["pgrep", "-f", rf"novnc_proxy .*--listen .*{self.novnc_port}"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                ).returncode
                == 0
            )
        except Exception:
            novnc_running = False

        if novnc_running:
            logger.info("noVNC already running on port %d", self.novnc_port)
            self._proc = None  # we didn't start it; don't own its lifecycle
        else:
            if not novnc_proxy.exists():
                logger.error("noVNC proxy not found at %s", novnc_proxy)
                return False
            logger.info(
                "Starting noVNC proxy on 0.0.0.0:%d -> 127.0.0.1:5901 ...",
                self.novnc_port,
            )
            try:
                # Store this as the managed long-running process
                self._proc = await asyncio.create_subprocess_exec(
                    str(novnc_proxy),
                    "--listen",
                    f"0.0.0.0:{self.novnc_port}",
                    "--vnc",
                    "127.0.0.1:5901",
                    "--web",
                    str(novnc_web),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )
            except Exception as e:
                logger.error("Failed to start noVNC proxy: %s", e)
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
                ["pgrep", "-f", "Xvnc"], capture_output=True, text=True, timeout=3
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_vnc_url(self, base: str = "http://localhost:8003") -> str | None:
        """Get the noVNC URL for desktop access."""
        if not self.is_running():
            return None
        return f"{base}/vnc.html?autoconnect=1&resize=remote"


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
        _desktop_service = DesktopService()
    return _desktop_service
