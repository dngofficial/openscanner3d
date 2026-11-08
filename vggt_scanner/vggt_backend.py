"""Pinned VGGT adapter: joint camera and depth prediction, no COLMAP executable."""
from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
from threading import RLock
from resources import check_memory

COMPUTE_LOCK=RLock()


class VGGTBackend:
    def __init__(self):
        self.status_lock=RLock()
        self.state="unloaded";self.error="";self.model=None;self.device="cpu"
        self.repo=os.environ.get("VGGT_MODEL","facebook/VGGT-1B")

    def status(self):
        with self.status_lock:
            return dict(state=self.state,error=self.error,device=self.device,model=self.repo,
                        installed=all(importlib.util.find_spec(m) is not None for m in ("torch","vggt","einops","huggingface_hub","accelerate")))

    def load(self):
        with COMPUTE_LOCK:
            if self.model is not None: return
            with self.status_lock: self.state="loading";self.error=""
            try:
                import torch
                from vggt.models.vggt import VGGT
                from huggingface_hub import hf_hub_download
                configured=os.environ.get("VGGT_DEVICE","auto")
                self.device=("cuda" if torch.cuda.is_available() else "cpu") if configured=="auto" else configured
                if self.device not in {"cpu","cuda"}: raise ValueError("VGGT_DEVICE must be auto, cpu or cuda.")
                if self.device=="cuda" and not torch.cuda.is_available():
                    raise RuntimeError("CUDA was requested, but this PyTorch installation has no usable CUDA device.")
                check_memory(self.device,torch.cuda.mem_get_info()[0] if self.device=="cuda" else None)
                if self.device=="cpu": torch.set_num_threads(min(4,os.cpu_count() or 1))
                local=os.environ.get("VGGT_CHECKPOINT")
                checkpoint=str(Path(local).expanduser()) if local else hf_hub_download(
                    repo_id=self.repo,filename="model.pt",revision=os.environ.get("VGGT_REVISION","main"),
                    cache_dir=os.environ.get("VGGT_CACHE",str(Path(__file__).resolve().parent/"model_cache")))
                # Memory-map weights and construct on meta to avoid two full CPU copies.
                payload=torch.load(checkpoint,map_location="cpu",mmap=True,weights_only=True)
                state=payload.get("model",payload.get("state_dict",payload)) if isinstance(payload,dict) else payload
                from accelerate import init_empty_weights
                with init_empty_weights(include_buffers=False):
                    model=VGGT(enable_point=False,enable_track=False)
                result=model.load_state_dict(state,strict=False,assign=True)
                if result.missing_keys: raise RuntimeError("Checkpoint lacks required camera/depth parameters.")
                unknown=[key for key in result.unexpected_keys if not key.startswith(("point_head.","track_head."))]
                if unknown: raise RuntimeError("Checkpoint does not match the pinned VGGT model code.")
                # Non-persistent normalization constants are not supplied by a state dict.
                model.aggregator._resnet_mean=torch.tensor([.485,.456,.406]).view(1,1,3,1,1)
                model.aggregator._resnet_std=torch.tensor([.229,.224,.225]).view(1,1,3,1,1)
                if any(p.is_meta for p in model.parameters()): raise RuntimeError("Checkpoint left uninitialized parameters.")
                dtype=torch.float32
                if self.device=="cuda":
                    dtype=torch.bfloat16 if torch.cuda.get_device_capability()[0]>=8 else torch.float16
                model=model.to(device=self.device).eval()
                self.inference_dtype=dtype
                self.model=model
                with self.status_lock: self.state="ready"
            except Exception as error:
                with self.status_lock:
                    self.state="error"
                    self.error=("VGGT could not load. Install requirements.txt, run install_vggt.py, and check model "
                                "download access or VGGT_CHECKPOINT. "+str(error))
                raise RuntimeError(self.error) from error

    def predict(self,images,cancel,update):
        with COMPUTE_LOCK:
            self.load()
            if cancel.is_set(): raise InterruptedError("Reconstruction cancelled.")
            import torch
            from vggt.utils.pose_enc import pose_encoding_to_extri_intri
            tensor=torch.from_numpy(images.transpose(0,3,1,2).copy()).float().div_(255).to(self.device)
            dtype=getattr(self,"inference_dtype",torch.float32)
            update(stage="inference",progress=40,message=f"Joint VGGT inference on {len(images)} views ({self.device})…")
            context=torch.autocast("cuda",dtype=dtype) if self.device=="cuda" else nullcontext()
            try:
                with torch.inference_mode(),context:
                    result=self.model(tensor)
                    poses,K=pose_encoding_to_extri_intri(result["pose_enc"],tensor.shape[-2:])
                if cancel.is_set(): raise InterruptedError("Reconstruction cancelled.")
                return dict(depth=result["depth"][0,...,0].float().cpu().numpy(),
                            confidence=result["depth_conf"][0].float().cpu().numpy(),
                            extrinsics=poses[0].float().cpu().numpy(),
                            intrinsics=K[0].float().cpu().numpy())
            except torch.OutOfMemoryError as error:
                raise RuntimeError("Model memory exhausted. Reduce views or input size and retry; the last successful cloud is kept.") from error
            finally:
                if self.device=="cuda": torch.cuda.empty_cache()


BACKEND=VGGTBackend()
