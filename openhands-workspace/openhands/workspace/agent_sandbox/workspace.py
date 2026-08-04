"""agent-sandbox (Kubernetes) based remote workspace implementation.

Runs the OpenHands agent server inside a pod managed by the
`kubernetes-sigs/agent-sandbox <https://github.com/kubernetes-sigs/agent-sandbox>`_
controller. The pod is claimed from a ``SandboxWarmPool`` (sub-second when the pool
is pre-warmed), which removes the container cold-start latency that the Docker and
hosted-runtime backends pay per conversation.

Compared with the other remote backends this one adds:

* **Warm pools** — ``SandboxWarmPool`` hands out an already-running pod on claim.
* **Native pause/resume** — ``pause()`` / ``resume()`` flip the Sandbox
  ``spec.operatingMode`` between ``Suspended`` and ``Running``; with a persistent
  volume the workspace state survives the suspend.
* **Strong isolation** — the pod can run under gVisor / Kata via a ``runtimeClass``
  set on the ``SandboxTemplate`` (an infrastructure choice, not a Python knob).

Requires the optional dependency::

    pip install openhands-workspace[agent-sandbox]
"""

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Literal
from urllib.request import urlopen

from pydantic import Field, PrivateAttr

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace.docker.workspace import (
    check_port_available,
    find_available_tcp_port,
)


logger = get_logger(__name__)


class AgentSandboxWorkspace(RemoteWorkspace):
    """Remote workspace backed by a kubernetes-sigs/agent-sandbox Sandbox pod.

    Claims a pod running the OpenHands agent server from a ``SandboxWarmPool``,
    connects to it over HTTP, and manages its lifecycle (pause / resume / delete)
    through the agent-sandbox custom resources.

    Two connection modes are supported:

    * ``port_forward`` (default) — spawns ``kubectl port-forward`` to the pod, so
      it works from a laptop against a local kind / minikube cluster or any cluster
      your kubeconfig can reach.
    * ``direct`` — you provide ``host`` (e.g. a ``sandbox-router`` / Gateway URL, or
      the in-cluster DNS name when OpenHands itself runs in the cluster) and no
      port-forward is started.

    Example:
        with AgentSandboxWorkspace(warmpool="openhands-pool") as workspace:
            result = workspace.execute_command("ls -la")
    """

    # Override parent fields with defaults
    working_dir: str = Field(
        default="/workspace",
        description="Working directory inside the sandbox pod.",
    )
    host: str = Field(
        default="",
        description=(
            "Agent server URL. Set automatically in 'port_forward' mode; must be "
            "provided by the caller in 'direct' mode."
        ),
    )

    # agent-sandbox configuration
    warmpool: str = Field(
        description="Name of the SandboxWarmPool to claim the pod from.",
    )
    namespace: str = Field(
        default="default",
        description="Kubernetes namespace holding the warm pool and the sandbox pod.",
    )
    server_port: int = Field(
        default=8000,
        description="Port the agent server listens on inside the pod.",
    )
    connection: Literal["port_forward", "direct"] = Field(
        default="port_forward",
        description="How to reach the agent server: local kubectl port-forward, or a "
        "caller-provided 'host' URL.",
    )
    host_port: int | None = Field(
        default=None,
        description="Local port for port-forward. If None, an available port is used.",
    )
    kube_context: str | None = Field(
        default=None,
        description="kubectl context to use for port-forward (defaults to current).",
    )
    sandbox_ready_timeout: int = Field(
        default=180,
        description="Seconds to wait for the Sandbox to reach the Ready condition.",
    )
    health_check_timeout: float = Field(
        default=120.0,
        gt=0.0,
        description="Seconds to wait for the agent server /health endpoint to pass.",
    )
    shutdown_after_seconds: int | None = Field(
        default=None,
        description="Optional TTL; the controller auto-deletes the claim after this "
        "many seconds (a safety net against leaked sandboxes).",
    )
    labels: dict[str, str] | None = Field(
        default=None,
        description="Kubernetes labels to attach to the SandboxClaim object.",
    )
    pod_labels: dict[str, str] | None = Field(
        default=None,
        description="Labels stamped onto the running pod (readable via the Downward "
        "API from inside the sandbox).",
    )
    pod_annotations: dict[str, str] | None = Field(
        default=None,
        description="Annotations stamped onto the running pod.",
    )
    detach_logs: bool = Field(
        default=True,
        description="Whether to stream port-forward output in the background.",
    )

    _sandbox: Any = PrivateAttr(default=None)  # k8s_agent_sandbox.Sandbox handle
    _sb_client: Any = PrivateAttr(default=None)  # k8s_agent_sandbox.SandboxClient
    _pf_process: subprocess.Popen[str] | None = PrivateAttr(default=None)
    _logs_thread: threading.Thread | None = PrivateAttr(default=None)
    _stop_logs: threading.Event = PrivateAttr(default_factory=threading.Event)
    # The caller's explicit host_port, if any. `host_port` itself tracks the port
    # currently in use, so it cannot double as the preference across reconnects.
    _preferred_host_port: int | None = PrivateAttr(default=None)

    def model_post_init(self, context: Any) -> None:
        """Claim a sandbox pod, connect to the agent server, and initialize."""
        # Validate connection config here (not in a model_validator) to match the
        # sibling backends and avoid Pydantic validator/post-init ordering surprises.
        if self.connection == "direct" and not self.host:
            raise ValueError(
                "connection='direct' requires 'host' to be set to the agent-server URL."
            )

        self._preferred_host_port = self.host_port

        try:
            import k8s_agent_sandbox  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "AgentSandboxWorkspace requires the 'agent-sandbox' extra. Install "
                "with: pip install openhands-workspace[agent-sandbox]"
            ) from e

        # 1) Claim a pod from the warm pool (blocks until the Sandbox is Ready).
        self._sb_client = k8s_agent_sandbox.SandboxClient()
        logger.info(
            "Claiming a sandbox from warm pool %r in namespace %r...",
            self.warmpool,
            self.namespace,
        )
        self._sandbox = self._sb_client.create_sandbox(
            warmpool=self.warmpool,
            namespace=self.namespace,
            sandbox_ready_timeout=self.sandbox_ready_timeout,
            labels=self.labels,
            shutdown_after_seconds=self.shutdown_after_seconds,
            pod_labels=self.pod_labels,
            pod_annotations=self.pod_annotations,
        )
        logger.info(
            "Sandbox %r is ready (claim %r).",
            self._sandbox.sandbox_id,
            self._sandbox.claim_name,
        )

        # Everything past this point must clean up the claim on failure: the
        # constructor raising means the caller never gets an object to close, so
        # the claim (and its pod) would leak until GC -- or forever, since
        # shutdown_after_seconds is None by default.
        try:
            # 2) Establish the connection to the agent server and wait for health.
            if self.connection == "port_forward":
                self._connect_port_forward_with_retry(
                    preferred_port=self._preferred_host_port
                )
            else:
                # 'direct': self.host was provided by the caller.
                self._wait_for_health(timeout=self.health_check_timeout)
            logger.info("agent-sandbox workspace is ready at %s", self.host)

            # 3) Initialize the parent RemoteWorkspace against the agent server URL.
            super().model_post_init(context)
        except Exception:
            self.cleanup()
            raise

    def _connect_port_forward_with_retry(
        self, attempts: int = 5, *, preferred_port: int | None = None
    ) -> None:
        """Start a port-forward and wait for health, retrying transient failures.

        Right after a resume the pod's network namespace can still be churning, so
        kubectl port-forward may drop with "network namespace ... is closed". A
        dead forward is detected quickly, so retrying with a fresh local port is
        cheap and lets the pod settle.

        ``preferred_port`` is only honored on the first attempt; pass None (the
        default, used on resume) to always take a freshly allocated port, since
        the previous session's port may still be in TIME_WAIT.
        """
        last_error: Exception | None = None
        for i in range(attempts):
            # Honor a caller-provided port on the first try; auto-pick on retries.
            self.host_port = preferred_port if i == 0 else None
            try:
                self._start_port_forward()
                self._wait_for_health(timeout=self.health_check_timeout)
                return
            except Exception as e:
                last_error = e
                logger.warning(
                    "Agent server not reachable (attempt %d/%d): %s",
                    i + 1,
                    attempts,
                    e,
                )
                self._stop_port_forward()
                time.sleep(2)
        raise RuntimeError(
            f"Could not reach agent server after {attempts} attempts: {last_error}"
        )

    def _resolve_pod_name(self) -> str:
        """Read the current pod name from the live Sandbox object.

        The pod name can change across a suspend/resume cycle, so this always
        re-reads it rather than using ``Sandbox.get_pod_name()``, which caches the
        first value for the lifetime of the handle. Uses only public client API;
        if ``k8s_agent_sandbox`` grows a public refresh (e.g.
        ``get_pod_name(refresh=True)``), this can defer to it.
        """
        from k8s_agent_sandbox.constants import (  # type: ignore[import-not-found]
            POD_NAME_ANNOTATION,
        )

        sandbox_object = (
            self._sb_client.k8s_helper.get_sandbox(
                self._sandbox.sandbox_id, self.namespace
            )
            or {}
        )
        annotations = (sandbox_object.get("metadata") or {}).get("annotations") or {}
        return annotations.get(POD_NAME_ANNOTATION) or self._sandbox.sandbox_id

    def _start_port_forward(self) -> None:
        """Start (or restart) kubectl port-forward to the sandbox pod."""
        if self.host_port is None:
            self.host_port = find_available_tcp_port()
        elif not check_port_available(self.host_port):
            raise RuntimeError(f"Port {self.host_port} is not available")

        pod = self._resolve_pod_name()

        cmd = ["kubectl", "port-forward"]
        if self.kube_context:
            cmd += ["--context", self.kube_context]
        cmd += [
            "-n",
            self.namespace,
            f"pod/{pod}",
            f"{self.host_port}:{self.server_port}",
        ]
        logger.info("Starting port-forward: %s", " ".join(cmd))
        self._stop_logs = threading.Event()
        self._pf_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        object.__setattr__(self, "host", f"http://127.0.0.1:{self.host_port}")

        if self.detach_logs:
            self._logs_thread = threading.Thread(target=self._stream_logs, daemon=True)
            self._logs_thread.start()

    def _stream_logs(self) -> None:
        """Stream port-forward output to stdout in the background."""
        if not self._pf_process or not self._pf_process.stdout:
            return
        try:
            for line in iter(self._pf_process.stdout.readline, ""):
                if self._stop_logs.is_set():
                    break
                if line:
                    sys.stdout.write(f"[PORT-FORWARD] {line}")
                    sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Error streaming port-forward logs: {e}\n")
        finally:
            self._stop_logs.set()

    def _wait_for_health(self, *, timeout: float) -> None:
        """Wait for the agent server /health endpoint to return success."""
        start = time.time()
        health_url = f"{self.host.rstrip('/')}/health"
        while time.time() - start < timeout:
            try:
                with urlopen(health_url, timeout=1.0) as resp:
                    if 200 <= getattr(resp, "status", 200) < 300:
                        return
            except Exception:
                pass
            if self._pf_process and self._pf_process.poll() is not None:
                raise RuntimeError(
                    "kubectl port-forward exited unexpectedly with code "
                    f"{self._pf_process.returncode}"
                )
            time.sleep(1)
        raise RuntimeError("agent server failed to become healthy in time")

    def _patch_operating_mode(self, mode: str) -> None:
        """Patch the Sandbox spec.operatingMode ('Running' or 'Suspended').

        TODO: agent-sandbox #1160 (claim-level idle lifecycle) and #1296
        (traffic-triggered resume) will make this a claim-level concern; once they
        land, pause/resume should move to the claim API instead of patching the
        Sandbox directly.
        """
        from k8s_agent_sandbox.constants import (  # type: ignore[import-not-found]
            SANDBOX_API_GROUP,
            SANDBOX_API_VERSION,
            SANDBOX_PLURAL_NAME,
        )

        self._sb_client.k8s_helper.custom_objects_api.patch_namespaced_custom_object(
            group=SANDBOX_API_GROUP,
            version=SANDBOX_API_VERSION,
            namespace=self.namespace,
            plural=SANDBOX_PLURAL_NAME,
            name=self._sandbox.sandbox_id,
            body={"spec": {"operatingMode": mode}},
        )

    def pause(self) -> None:
        """Suspend the sandbox to conserve resources.

        Sets the Sandbox ``operatingMode`` to ``Suspended``; the controller
        terminates the pod while keeping any persistent volume. Resume with
        ``resume()``.
        """
        if self._sandbox is None:
            raise RuntimeError("Cannot pause: no active sandbox")
        logger.info("Suspending sandbox %r...", self._sandbox.sandbox_id)
        self._stop_port_forward()
        self._patch_operating_mode("Suspended")

    def resume(self) -> None:
        """Resume a suspended sandbox and reconnect to the agent server."""
        if self._sandbox is None:
            raise RuntimeError("Cannot resume: no active sandbox")
        logger.info("Resuming sandbox %r...", self._sandbox.sandbox_id)
        self._patch_operating_mode("Running")
        self._sb_client.k8s_helper.wait_for_sandbox_ready(
            self._sandbox.sandbox_id, self.namespace, self.sandbox_ready_timeout
        )
        if self.connection == "port_forward":
            # Reconnect on a fresh local port (the old one may be in TIME_WAIT) and
            # rebuild the HTTP client so it targets the new host URL.
            self._connect_port_forward_with_retry(preferred_port=None)
            self.reset_client()
        else:
            self._wait_for_health(timeout=self.health_check_timeout)
        logger.info("Sandbox %r resumed at %s", self._sandbox.sandbox_id, self.host)

    def _stop_port_forward(self) -> None:
        """Stop the kubectl port-forward subprocess if running."""
        if self._pf_process is None:
            return
        self._stop_logs.set()
        if self._logs_thread and self._logs_thread.is_alive():
            self._logs_thread.join(timeout=2)
        try:
            os.killpg(os.getpgid(self._pf_process.pid), signal.SIGTERM)
            self._pf_process.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(self._pf_process.pid), signal.SIGKILL)
                self._pf_process.wait(timeout=2)
            except Exception:
                pass
        self._pf_process = None

    def __enter__(self) -> "AgentSandboxWorkspace":
        """Context manager entry - returns the workspace itself."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        """Context manager exit - tears down the sandbox."""
        self.cleanup()

    def __del__(self) -> None:
        """Best-effort cleanup when the workspace is garbage collected."""
        try:
            if getattr(self, "__pydantic_private__", None) is not None:
                self.cleanup()
        except Exception:
            # Never raise from __del__ (e.g. during interpreter shutdown).
            pass

    def cleanup(self) -> None:
        """Stop the port-forward and delete the SandboxClaim (idempotent)."""
        self._stop_port_forward()
        sandbox = getattr(self, "_sandbox", None)
        if sandbox is not None:
            try:
                logger.info("Terminating sandbox %r...", sandbox.sandbox_id)
                sandbox.terminate()
            except Exception as e:
                logger.warning("Error terminating sandbox: %s", e)
            self._sandbox = None
