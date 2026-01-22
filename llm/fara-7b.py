from pathlib import Path

import torch
from transformers import AutoTokenizer, Qwen2_5_VLForConditionalGeneration


def resolve_snapshot_path(base_cache: Path) -> Path:
    """Return the newest local snapshot directory for the requested model."""
    snapshot_root = base_cache / "snapshots"
    if not snapshot_root.exists():
        raise FileNotFoundError(f"Missing cache directory at {snapshot_root}")

    snapshots = sorted(
        (path for path in snapshot_root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not snapshots:
        raise FileNotFoundError(f"No snapshots found under {snapshot_root}")

    return snapshots[0]

def resolve_model_path() -> Path:
    """Handle both new and legacy Hugging Face cache layouts."""
    cache_root = Path("/root/.cache/huggingface/hub")
    new_cache = cache_root / "models--microsoft--Fara-7B"
    legacy_cache = cache_root / "microsoft-Fara-7B"

    if new_cache.exists():
        return resolve_snapshot_path(new_cache)
    if legacy_cache.exists():
        return legacy_cache

    raise FileNotFoundError(
        "Missing cache directory for Fara-7B under "
        "/root/.cache/huggingface/hub"
    )


model_path = resolve_model_path()

device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32

tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    model_path,
    torch_dtype=dtype,
    device_map="auto",
    local_files_only=True,
)

PROMPT = (
    "Eleborate the concept of 'Laplace transform' followed by simple examples "
    "illustration. no need follow up question"
)

inputs = tokenizer(PROMPT, return_tensors="pt").to(device)

GEN_KWARGS = {
    "do_sample": False,
    "max_new_tokens": 1024,
    "pad_token_id": tokenizer.eos_token_id,
}

with torch.no_grad():
    outputs = model.generate(**inputs, **GEN_KWARGS)

print(tokenizer.decode(outputs[0], skip_special_tokens=True))
