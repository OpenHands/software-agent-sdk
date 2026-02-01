# Apptainer Workspace

The `ApptainerWorkspace` provides a container-based workspace using [Apptainer](https://apptainer.org/) (formerly Singularity), which doesn't require root access. This makes it ideal for HPC and shared computing environments where Docker may not be available or permitted.

Note: This class only works with **pre-built images**. It does not support building images on-the-fly from a base image. For on-the-fly building with Docker, use `DockerDevWorkspace` instead.

## Why Apptainer?

- **No root required**: Unlike Docker, Apptainer doesn't need root/sudo privileges
- **HPC-friendly**: Designed for high-performance computing environments
- **Secure**: Better security model for multi-user systems
- **Compatible**: Can use pre-built Docker images

## Prerequisites

Install Apptainer by following the [official quick start guide](https://apptainer.org/docs/user/main/quick_start.html).

On Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y apptainer
```

On CentOS/RHEL:
```bash
sudo yum install -y apptainer
```

## Usage

### Option 1: Use Pre-built Agent Server Image (Recommended)

```python
from openhands.workspace import ApptainerWorkspace

# Use a pre-built agent server image
with ApptainerWorkspace(
    server_image="ghcr.io/openhands/agent-server:latest-python",
    host_port=8010,
) as workspace:
    result = workspace.execute_command("echo 'Hello from Apptainer!'")
    print(result.stdout)
```

### Option 2: Use Existing SIF File

```python
from openhands.workspace import ApptainerWorkspace

# Use an existing Apptainer SIF file
with ApptainerWorkspace(
    sif_file="/path/to/your/agent-server.sif",
    host_port=8010,
) as workspace:
    result = workspace.execute_command("ls -la")
    print(result.stdout)
```

### Mount Host Directory

```python
from openhands.workspace import ApptainerWorkspace

# Mount a host directory into the container
with ApptainerWorkspace(
    server_image="ghcr.io/openhands/agent-server:latest-python",
    host_port=8010,
    mount_dir="/path/to/host/directory",
) as workspace:
    result = workspace.execute_command("ls /workspace")
    print(result.stdout)
```

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `server_image` | `str \| None` | `None` | Pre-built agent server image (mutually exclusive with `sif_file`) |
| `sif_file` | `str \| None` | `None` | Path to existing SIF file (mutually exclusive with `server_image`) |
| `host_port` | `int \| None` | `None` | Port to bind to (auto-assigned if None) |
| `mount_dir` | `str \| None` | `None` | Host directory to mount into container |
| `cache_dir` | `str \| None` | `~/.apptainer_cache` | Directory for caching SIF files |
| `forward_env` | `list[str]` | `["DEBUG"]` | Environment variables to forward |
| `detach_logs` | `bool` | `True` | Stream logs in background |
| `platform` | `PlatformType` | `"linux/amd64"` | Platform architecture |
| `extra_ports` | `bool` | `False` | Expose additional ports (VSCode, VNC) |
| `use_fakeroot` | `bool` | `True` | Use --fakeroot for consistent file ownership |

## How It Works

1. **Image Preparation**: Pulls Docker images and converts to Apptainer SIF format, or uses existing SIF files
2. **Caching**: SIF files are cached in `~/.apptainer_cache` by default for faster startup
3. **Container Execution**: Runs the agent server using `apptainer run`
4. **Health Checking**: Waits for the server to become healthy before accepting requests
5. **Cleanup**: Automatically stops the container when done

## Differences from DockerWorkspace

| Feature | DockerWorkspace | ApptainerWorkspace |
|---------|----------------|-------------------|
| Root required | Yes (typically) | No |
| Docker daemon | Required | Not required |
| Port mapping | Native | Host networking |
| Image format | Docker | SIF (from Docker) |
| HPC support | Limited | Excellent |
| Setup complexity | Lower | Slightly higher |

## Troubleshooting

### Apptainer not found
```
RuntimeError: Apptainer is not available
```
**Solution**: Install Apptainer following the [installation guide](https://apptainer.org/docs/user/main/quick_start.html).

### Port already in use
```
RuntimeError: Port 8010 is not available
```
**Solution**: Either specify a different `host_port` or let the system auto-assign one by not specifying it.

### Image pull fails
```
Failed to pull and convert Docker image
```
**Solution**: Ensure you have network access to pull images from the Docker registry. Apptainer pulls directly from Docker registries without needing Docker daemon.

## Complete Example

See `examples/02_remote_agent_server/07_convo_with_apptainer_sandboxed_server.py` for a complete working example that demonstrates:
- Setting up an Apptainer workspace
- Running agent conversations
- File operations in the sandboxed environment
- Proper cleanup

**To test the example:**
```bash
# Make sure Apptainer is installed
apptainer --version

# Run the example
cd examples/02_remote_agent_server
python 07_convo_with_apptainer_sandboxed_server.py
```

## Performance Notes

- **First run**: Slower due to image download and SIF conversion
- **Subsequent runs**: Much faster if the SIF file is cached
- **Best for**: Long-running workloads, HPC environments, multi-user systems
- **Cache location**: Check and clean `~/.apptainer_cache` periodically

## Security

Apptainer provides better security isolation for shared systems:
- Runs as the invoking user (no privilege escalation)
- No daemon running as root
- Designed for multi-tenant HPC environments
- Support for encrypted containers (optional)
