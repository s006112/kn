from transformers import pipeline
import torch

model_id = "/root/.cache/huggingface/hub/gpt-oss-20b"

if torch.cuda.is_available():
    device_map = "auto"
    torch_dtype = torch.float16
else:
    device_map = None
    torch_dtype = "auto"

# If torch.xpu exists (e.g. Intel extension builds), force-disable it so we don't
# accidentally pick XPU on NVIDIA systems.
if hasattr(torch, "xpu"):
    try:
        torch.xpu.is_available = lambda: False  # type: ignore[attr-defined]
    except Exception:
        pass

pipe = pipeline(
    "text-generation",
    model=model_id,
    torch_dtype=torch_dtype,
    device_map=device_map,
    local_files_only=True,
)

messages = [
    {"role": "user", "content": "Explain quantum mechanics clearly and concisely."},
]

outputs = pipe(
    messages,
    max_new_tokens=256,
)
print(outputs[0]["generated_text"][-1])
