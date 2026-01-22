"""
Docstring for llm.z-image
Loads Z-Image from local cache with diffusers 0.35.2 (no ZImagePipeline in this version)
Using torch 2.2.2+cu118 on NVIDIA P100.
"""

import torch
from diffusers import DiffusionPipeline

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
    for dtype in (torch.bfloat16, torch.float16, torch.float32):
        try:
            return DiffusionPipeline.from_pretrained(
                path,
                torch_dtype=dtype,
                local_files_only=True,
                trust_remote_code=True,
            )
        except Exception as e:
            print(f"Failed with {dtype}: {e}")
    raise RuntimeError(f"Could not load Z-Image from {path}")


pipe = load_zimage()
device = "cuda" if torch.cuda.is_available() else "cpu"
pipe.to(device)

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
