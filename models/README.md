# Local model checkpoints

Weights are never committed. Keep them in this ignored directory; scans never download anything at runtime.

## SAM 2.1 tiny (AI object selection)

| Item | Value |
| --- | --- |
| Code | Official [facebookresearch/sam2](https://github.com/facebookresearch/sam2), commit `2b90b9f5ceec907a1c18123530e92e794ad901a4` |
| Checkpoint | `sam2.1_hiera_tiny.pt` from `https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt` |
| SHA-256 | `7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69` (156,008,466 bytes) |
| Model config | `configs/sam2.1/sam2.1_hiera_t.yaml` (bundled with the code) |
| Licence | Apache 2.0 for the SAM 2 code and checkpoints, according to the upstream repository. Recheck before redistributing. |

Do **not** `pip install sam2` from PyPI. That package name is a third-party repackage of a fork, not Meta's release.

### Install (verified on the development laptop, CPU only)

SAM 2 needs PyTorch, which the web server does not. Install it in a separate environment and point `segmentation_python` at it, so the scanner's constrained `.venv` stays unchanged. The following was run with the repository's `uv` on Windows / Python 3.11.18:

```powershell
.tools/uv/uv.exe venv --python .venv/Scripts/python.exe .venv-sam2
.tools/uv/uv.exe pip install --python .venv-sam2/Scripts/python.exe --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match torch==2.5.1 torchvision==0.20.1
.tools/uv/uv.exe pip install --python .venv-sam2/Scripts/python.exe "setuptools>=61" wheel
$env:SAM2_BUILD_CUDA = '0'
.tools/uv/uv.exe pip install --python .venv-sam2/Scripts/python.exe --no-build-isolation "git+https://github.com/facebookresearch/sam2.git@2b90b9f5ceec907a1c18123530e92e794ad901a4"
Invoke-WebRequest https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt -OutFile models/sam2.1_hiera_tiny.pt
(Get-FileHash models/sam2.1_hiera_tiny.pt -Algorithm SHA256).Hash   # must match the table above
```

Then in `config.local.toml`:

```toml
segmentation_python = '.venv-sam2/Scripts/python.exe'
sam2_checkpoint = 'models/sam2.1_hiera_tiny.pt'
```

Doctor's `segmentation` section reports the interpreter, the checkpoint hash, and whether `torch` and `sam2` import (`cpu` or `cuda`).

`SAM2_BUILD_CUDA=0` skips SAM 2's optional CUDA extension. SAM 2 then logs that its hole-filling post-process was skipped; upstream documents this as safe to ignore in most cases. Masks on the reviewed set looked correct without it.

**GPU (partner laptop, not yet verified):** use the PyTorch CUDA wheel index that matches the installed driver (for example `https://download.pytorch.org/whl/cu124`) instead of `/cpu`, then check that doctor reports `cuda`. `segmentation_device = "auto"` picks CUDA when it is available.

### Measured on real input

On the 77-frame reviewed Camo set (1280×720), development laptop CPU:

- **One click on the top face of the power bank:** 180 s. The mask tracked the top face in all 77 frames but missed the dark sides.
- **Adding a click on the side band of a second frame:** fixed the frames near that one, but not distant frames.
- **Through the web UI on 14 frames** (top and side clicks on one frame, plus one "not object" click on the table): 31.5 s. All 14 masks covered the whole object, sides included.

These numbers show the model running locally; they say nothing about reconstruction quality.
