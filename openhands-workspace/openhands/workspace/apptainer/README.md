# Apptainer Workspace

The `ApptainerWorkspace` provides a container-based workspace using [Apptainer](https://apptainer.org/) (formerly Singularity), which doesn't require root access. This makes it ideal for HPC and shared computing environments where Docker may not be available or permitted.

## Why Apptainer?

- **No root required**: Unlike Docker, Apptainer doesn't need root/sudo privileges
- **HPC-friendly**: Designed for high-performance computing environments
- **Secure**: Better security model for multi-user systems
- **Compatible**: Can build from Docker images

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

### Basic Example

```python
from openhands.workspace import ApptainerWorkspace

# Option 1: Use a pre-built agent server image (fastest)
with ApptainerWorkspace(
    server_image="ghcr.io/openhands/agent-server:main-python",
    host_port=8010,
) as workspace:
    result = workspace.execute_command("echo 'Hello from Apptainer!'")
    print(result.stdout)
```

### Build from Base Image

```python
from openhands.workspace import ApptainerWorkspace

# Option 2: Build from a base image (more flexible)
with ApptainerWorkspace(
    base_image="nikolaik/python-nodejs:python3.12-nodejs22",
    host_port=8010,
) as workspace:
    result = workspace.execute_command("python --version")
    print(result.stdout)
```

### Use Existing SIF File

```python
from openhands.workspace import ApptainerWorkspace

# Option 3: Use an existing Apptainer SIF file
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
    server_image="ghcr.io/openhands/agent-server:main-python",
    host_port=8010,
    mount_dir="/path/to/host/directory",
) as workspace:
    result = workspace.execute_command("ls /workspace")
    print(result.stdout)
```

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_image` | `str \| None` | `None` | Base Docker image to build from (mutually exclusive with `server_image` and `sif_file`) |
| `server_image` | `str \| None` | `None` | Pre-built agent server image (mutually exclusive with `base_image` and `sif_file`) |
| `sif_file` | `str \| None` | `None` | Path to existing SIF file (mutually exclusive with `base_image` and `server_image`) |
| `host_port` | `int \| None` | `None` | Port to bind to (auto-assigned if None) |
| `mount_dir` | `str \| None` | `None` | Host directory to mount into container |
| `cache_dir` | `str \| None` | `~/.apptainer_cache` | Directory for caching SIF files |
| `forward_env` | `list[str]` | `["DEBUG"]` | Environment variables to forward |
| `detach_logs` | `bool` | `True` | Stream logs in background |
| `platform` | `PlatformType` | `"linux/amd64"` | Platform architecture |
| `target` | `TargetType` | `"source"` | Build target |
| `extra_ports` | `bool` | `False` | Expose additional ports (VSCode, VNC) |

## How It Works

1. **Image Preparation**: Converts Docker images to Apptainer SIF format or uses existing SIF files
2. **Caching**: SIF files are cached in `~/.apptainer_cache` by default for faster startup
3. **Instance Management**: Creates an Apptainer instance and runs the agent server inside it
4. **Health Checking**: Waits for the server to become healthy before accepting requests
5. **Cleanup**: Automatically stops and removes the instance when done

## Differences from DockerWorkspace

| Feature | DockerWorkspace | ApptainerWorkspace |
|---------|----------------|-------------------|
| Root required | Yes (typically) | No |
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

### SIF build fails
```
Failed to build SIF file from Docker image
```
**Solution**: Ensure Docker is running and you have network access to pull images. The ApptainerWorkspace first pulls/builds the Docker image, then converts it to SIF format.

## Complete Example

See `examples/02_remote_agent_server/05_convo_with_apptainer_sandboxed_server.py` for a complete working example that demonstrates:
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
python 05_convo_with_apptainer_sandboxed_server.py
```

**Note**: The implementation has been validated for code structure, type correctness, and API compatibility. Full runtime testing requires Apptainer to be installed on your system.

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
