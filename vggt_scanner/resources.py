"""Memory preflight for the large neural model."""
from pathlib import Path
import psutil


def available_memory():
    available=int(psutil.virtual_memory().available)
    # Respect a container memory limit when Linux exposes it.
    for limit_file,usage_file in (
        ("/sys/fs/cgroup/memory.max","/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes","/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            limit=int(Path(limit_file).read_text().strip())
            used=int(Path(usage_file).read_text().strip())
            available=min(available,max(0,limit-used))
        except (OSError,ValueError): pass
    return available


def check_memory(device,free_cuda=None):
    gib=1024**3
    if device=="cpu":
        free=available_memory()/gib
        if free<12:
            raise RuntimeError(f"CPU memory preflight: {free:.1f} GiB available. This app reserves at least 12 GiB "
                               "of available RAM before loading full VGGT on CPU. Free RAM or use a compatible NVIDIA GPU.")
    elif device=="cuda" and free_cuda is not None and free_cuda/gib<6:
        raise RuntimeError(f"GPU memory preflight: {free_cuda/gib:.1f} GiB free. Free GPU memory before loading "
                           "VGGT; this app requires at least 6 GiB free for model loading. Start with 224 pixels and 6 views.")
