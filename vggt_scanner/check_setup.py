"""Report installation/device readiness without loading any model weights."""
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
from resources import available_memory,check_memory


def main():
    print("Python / package readiness")
    for package in ("torch","vggt","opencv-python-headless","fastapi","numpy","accelerate"):
        try: print(f"  {package}: {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError: print(f"  {package}: NOT INSTALLED")
    print(f"Available RAM: {available_memory()/1024**3:.1f} GiB")
    folder=Path(os.environ.get("VGGT_CACHE",str(Path(__file__).resolve().parent/"model_cache")))
    parent=folder
    while not parent.exists(): parent=parent.parent
    print(f"Weight cache: {folder}")
    print(f"Free cache disk: {shutil.disk_usage(parent).free/1024**3:.1f} GiB")
    if importlib.util.find_spec("torch") is None: return
    import torch
    cuda=torch.cuda.is_available()
    print(f"Usable PyTorch CUDA: {cuda}")
    if cuda:
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"Free GPU memory: {torch.cuda.mem_get_info()[0]/1024**3:.1f} GiB")
    device="cuda" if cuda else "cpu"
    try:
        check_memory(device,torch.cuda.mem_get_info()[0] if cuda else None)
        print(f"{device} memory preflight: PASS (larger batches can still need more memory)")
    except RuntimeError as error: print(error)


if __name__=="__main__": main()
