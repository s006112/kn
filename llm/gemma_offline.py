from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

# model path inside container
model_path = "/root/.cache/huggingface/hub/models--google--gemma-3-1b-it/snapshots/dcc83ea841ab6100d6b47a070329e1ba4cf78752"
# model_path = "/root/.cache/huggingface/hub/models--google--gemma-3-270m-it/snapshots/ac82b4e820549b854eebf28ce6dedaf9fdfa17b3"
# model_path = "/root/.cache/huggingface/hub/models--google--gemma-3-4b-it"


device = "cuda" if torch.cuda.is_available() else "cpu"


def resolve_snapshot_path(cache_root: str) -> str:
    """Return the snapshot directory that actually contains the weights."""
    base = Path(cache_root)
    snapshots_dir = base / "snapshots"
    refs_main = base / "refs" / "main"

    if snapshots_dir.is_dir():
        if refs_main.is_file():
            commit_hash = refs_main.read_text().strip()
            candidate = snapshots_dir / commit_hash
            if candidate.is_dir():
                return str(candidate)

        snapshot_dirs = [
            child for child in snapshots_dir.iterdir() if child.is_dir()
        ]
        if snapshot_dirs:
            latest_snapshot = max(snapshot_dirs, key=lambda p: p.stat().st_mtime)
            return str(latest_snapshot)

    return str(base)


snapshot_path = resolve_snapshot_path(model_path)

tokenizer = AutoTokenizer.from_pretrained(snapshot_path, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(
    snapshot_path,
    dtype=torch.bfloat16,
    device_map="auto",
    local_files_only=True
)

prompt = "Please use to explain the term 'Fourier transform'."

messages = [{"role": "user", "content": prompt}]
if getattr(tokenizer, "chat_template", None):
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

inputs = tokenizer(prompt, return_tensors="pt").to(device)

gen_kwargs = {
    "do_sample": False,
    "max_new_tokens": 16384,
}

eos_token_id = model.generation_config.eos_token_id
if eos_token_id is not None:
    gen_kwargs["eos_token_id"] = eos_token_id

pad_token_id = tokenizer.pad_token_id or tokenizer.eos_token_id
if pad_token_id is not None:
    gen_kwargs["pad_token_id"] = pad_token_id

with torch.no_grad():
    outputs = model.generate(**inputs, **gen_kwargs)

print(tokenizer.decode(outputs[0], skip_special_tokens=True))
