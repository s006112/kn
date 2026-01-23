from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM


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


cache_root = Path("/root/.cache/huggingface/hub/models--Qwen--Qwen3-4B-Instruct-2507")
model_path = resolve_snapshot_path(cache_root)

device = "cuda" if torch.cuda.is_available() else "cpu"

tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    dtype=torch.bfloat16,
    device_map="auto",
    local_files_only=True
)

prompt = "What is philosophy? Please explain concisely based on your understanding."
inputs = tokenizer(prompt, return_tensors="pt").to(device)

with torch.no_grad():
    outputs = model.generate(
        **inputs,
        do_sample=False,
        max_new_tokens=512
    )

print(tokenizer.decode(outputs[0], skip_special_tokens=True))
