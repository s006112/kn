import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

model_path = "/root/.cache/huggingface/hub/models--mistralai--Mistral-7B-Instruct-v0.3/snapshots/0d4b76e1efeb5eb6f6b5e757c79870472e04bd3a"
model_id = "mistralai/Mistral-7B-Instruct-v0.3"

device = "cuda" if torch.cuda.is_available() else "cpu"

# 1. Define the 8-bit quantization configuration
bnb_config = BitsAndBytesConfig(
    # Set to True for 8-bit quantization
    load_in_4bit=True 
    # Note: bnb_4bit_... arguments are not needed for 8-bit loading
)

def load_model(path, local_only):
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=local_only)
    model = AutoModelForCausalLM.from_pretrained(
        path,
        # 2. Pass the quantization config
        quantization_config=bnb_config, 
        device_map="auto",
        local_files_only=local_only
    )
    return tokenizer, model

try:
    tokenizer, model = load_model(model_path, True)
except FileNotFoundError:
    tokenizer, model = load_model(model_id, False)
prompt = "Eleborate the concept of 'Laplace transform' followed by simple examples illustration."
#prompt = "What is philosophy? Please explain concisely based on your understanding."
inputs = tokenizer(prompt, return_tensors="pt").to(device)

with torch.no_grad():
    outputs = model.generate(
        **inputs,
        do_sample=False,
        max_new_tokens=2048
    )

print(tokenizer.decode(outputs[0], skip_special_tokens=True))
