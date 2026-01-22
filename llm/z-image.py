"""
Docstring for llm.z-image
Loads Z-Image from local cache. Requires diffusers 0.36.x (model remote code imports diffusers.ZImagePipeline).
Using torch 2.2.2+cu118 on NVIDIA P100.
"""

import types

import torch


def _ensure_torch_xpu_stub():
    if hasattr(torch, "xpu"):
        return
    torch.xpu = types.SimpleNamespace(  # type: ignore[attr-defined]
        empty_cache=lambda: None,
        device_count=lambda: 0,
        manual_seed=torch.manual_seed,
        reset_peak_memory_stats=lambda *args, **kwargs: None,
        max_memory_allocated=lambda *args, **kwargs: 0,
        synchronize=lambda *args, **kwargs: None,
        is_available=lambda: False,
    )


_ensure_torch_xpu_stub()

def _ensure_torch_device_mesh_stub():
    dist = getattr(torch, "distributed", None)
    if dist is None:
        return
    if hasattr(dist, "device_mesh"):
        return

    class _DeviceMesh:  # minimal stub for type annotations
        pass

    dist.device_mesh = types.SimpleNamespace(DeviceMesh=_DeviceMesh)  # type: ignore[attr-defined]


_ensure_torch_device_mesh_stub()

import diffusers  # noqa: E402
from diffusers import DiffusionPipeline  # noqa: E402

MODEL_PATH = "/root/.cache/huggingface/hub/Tongyi-MAI-Z-Image-Turbo/"

prompt = (
    "Young Chinese woman in red Hanfu, intricate embroidery. Impeccable makeup, "
    "red floral forehead pattern. Elaborate high bun, golden phoenix headdress, "
    "red flowers, beads. Holds round folding fan with lady, trees, bird. Neon "
    "lightning-bolt lamp (⚡️), bright yellow glow, above extended left palm. "
    "Soft-lit outdoor night background, silhouetted tiered pagoda (西安大雁塔), "
    "blurred colorful distant lights."
)


def load_zimage(path=MODEL_PATH):
    """Load Z-Image from local cache; try bfloat16, fall back to float16."""
    if not hasattr(diffusers, "ZImagePipeline"):
        raise RuntimeError(
            "This model's remote code requires `diffusers.ZImagePipeline`, which is missing in "
            f"diffusers {getattr(diffusers, '__version__', 'unknown')}. "
            "Install/upgrade to diffusers==0.36.0 (or newer) and retry."
        )
    for dtype in (torch.bfloat16, torch.float16, torch.float32):
        try:
            return DiffusionPipeline.from_pretrained(
                path,
                torch_dtype=dtype,
                local_files_only=True,
            )
        except Exception as e:
            print(f"Failed with {dtype}: {e}")
    raise RuntimeError(f"Could not load Z-Image from {path}")


pipe = load_zimage()
device = "cuda" if torch.cuda.is_available() else "cpu"
if device == "cuda":
    try:
        pipe.to("cuda")
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        pipe.enable_model_cpu_offload()
else:
    pipe.to("cpu")

# Generate image
generator = torch.Generator(device).manual_seed(42)
image = pipe(
    prompt=prompt,
    height=1024,
    width=1024,
    num_inference_steps=9,
    guidance_scale=0.0,
    generator=generator,
).images[0]

image.save("example.png")
