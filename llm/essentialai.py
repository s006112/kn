import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
import os

model_id = "/root/.cache/huggingface/hub/EssentialAI-rnj-1"

print(f"Loading model: {model_id}...")
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    dtype=torch.bfloat16,
    device_map="auto",
)
tokenizer = AutoTokenizer.from_pretrained(model_id)

print("Model and tokenizer loaded successfully.")

messages = [
    #{"role": "system", "content": "You are a helpful AI technical assistant."}, # Optional system message
    {"role": "user", "content": "Eleborate the concept of 'Laplace transform' followed by simple examples illustration."}
]

input_ids = tokenizer.apply_chat_template(
    messages, 
    add_generation_prompt=True, 
    return_tensors="pt"
).to(model.device)

# --- Generate Prediction --- #
print("Generating prediction...")
output_ids = model.generate(
    input_ids,
    max_new_tokens=512,
    pad_token_id=tokenizer.eos_token_id, 
    do_sample=True, 
    temperature=0.2,
    top_p=0.95 
)

response = tokenizer.decode(output_ids[0][input_ids.shape[-1]:], skip_special_tokens=True)
print(response)
