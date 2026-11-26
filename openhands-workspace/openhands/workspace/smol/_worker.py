"""Own one native Smol runtime in a process with a bounded parent lifetime.

Configuration travels over stdin, never argv or a temporary file. stdout is a
private protocol channel; native and guest output is discarded by the parent.
"""

import importlib
import json
import os
import sys
from importlib.metadata import version
from typing import Any, TextIO


def _send(channel: TextIO, **message: Any) -> None:
    channel.write(json.dumps(message) + "\n")
    channel.flush()


def main() -> None:
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    request = json.loads(sys.stdin.readline())
    # The optional native library is loaded only in this owned helper process.
    smol = importlib.import_module("smol")

    connection = smol.ConnectOptions(target="local")
    name = request["name"]
    if request.get("cleanup"):
        smol.Machine.delete_by_name(name, connection, drain_secs=0)
        return

    available, code, _reason = smol.local_availability()
    if not available:
        _send(channel, error="availability", code=code)
        return

    try:
        smol.Machine.publish_atomic
    except AttributeError:
        _send(channel, error="atomic_ports")
        return

    machine = None
    allocated = False
    try:
        _send(channel, stage="create")
        allocated = True
        machine = smol.Machine.create(
            smol.MachineConfig(
                name=name,
                image=request["image"],
                resources=smol.ResourceSpec(**request["resources"]),
                command=request["command"],
                env=request["env"],
                workdir=request["workdir"],
                persistent=False,
                detach=False,
                wait_for_ports=False,
                ready_timeout_seconds=request["timeout"],
            ),
            connection,
        )
        _send(channel, stage="ports")
        machine.publish_atomic(8000)
        endpoint = machine.endpoint(8000)
        images = machine.list_images()
        digest = next(
            (
                image.digest
                for image in images
                if image.reference == request["image"]
                or request["image"].endswith("@" + image.digest)
            ),
            request["image"].split("@", 1)[1] if "@" in request["image"] else None,
        )
        _send(
            channel,
            endpoint=endpoint.http_url,
            image_digest=digest,
            smol_version=version("smolmachines"),
        )
        # EOF also tears down the VM when the parent exits normally.
        sys.stdin.readline()
    except BaseException as error:
        # Native exception messages can include workload env or registry auth.
        text = str(error).lower()
        kind = "image_architecture" if "architecture" in text else "create"
        _send(channel, error=kind)
    finally:
        if allocated:
            try:
                if machine is not None:
                    machine.stop(drain_secs=0, timeout_secs=0)
                # Also covers native creation failures before a handle exists.
                smol.Machine.delete_by_name(name, connection, drain_secs=0)
            except BaseException:
                _send(channel, error="cleanup")
                sys.exit(1)


if __name__ == "__main__":
    main()
