import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

#model_name = "google/gemma-3-1b-it"
model_name = "google/gemma-3-4b-it"
#model_name = "mistralai/Mistral-7B-Instruct-v0.3"

device = "cuda" if torch.cuda.is_available() else "cpu"

tokenizer = AutoTokenizer.from_pretrained(model_name)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    dtype=torch.bfloat16,
    device_map="auto"
)
generation_config = model.generation_config
generation_config.top_p = 1.0
generation_config.top_k = 50

# prompt = "What is philosophy. Please brainstorm based on your understanding and express your own thought. Answer in Chinese only."
prompt = "What is philosophy. Please express your own thought in a complete passage."

inputs = tokenizer(prompt, return_tensors="pt").to(device)

with torch.no_grad():
    outputs = model.generate(
        **inputs,
        do_sample=False,
        max_new_tokens=1024
    )

print(tokenizer.decode(outputs[0], skip_special_tokens=True))
