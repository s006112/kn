import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

model_path = "/root/.cache/huggingface/hub/models--mistralai--Mistral-7B-Instruct-v0.3/snapshots/0d4b76e1efeb5eb6f6b5e757c79870472e04bd3a"
model_id = "mistralai/Mistral-7B-Instruct-v0.3"


device = "cuda" if torch.cuda.is_available() else "cpu"


def load_model(path, local_only):
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=local_only)
    model = AutoModelForCausalLM.from_pretrained(
        path,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=local_only
    )
    return tokenizer, model


try:
    tokenizer, model = load_model(model_path, True)
except FileNotFoundError:
    tokenizer, model = load_model(model_id, False)

prompt = "What is philosophy? Please explain concisely based on your understanding."
inputs = tokenizer(prompt, return_tensors="pt").to(device)

with torch.no_grad():
    outputs = model.generate(
        **inputs,
        do_sample=False,
        max_new_tokens=512
    )

print(tokenizer.decode(outputs[0], skip_special_tokens=True))
