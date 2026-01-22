import torch
import os # NEW: Added for checking if the saved directory exists
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig # NEW: Added for quantization

# --- Configuration ---
# Original local path (from your script)
model_id = "/root/.cache/huggingface/hub/Ministral-3-3B-Instruct-2512" 
# --- End Configuration ---
device = "cuda" if torch.cuda.is_available() else "cpu"

# Int4 Quantization Configuration (Recommended for 8GB VRAM)
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.bfloat16, 
    bnb_4bit_quant_type="nf4", 
    bnb_4bit_use_double_quant=True
)

# --- Core Loading and Saving Logic ---
if os.path.isdir(model_id):
    # 1. Fully Offline Load (Subsequent Runs)
    print(f"Loading fully offline model from: {model_id}")
    tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        device_map="auto",
        local_files_only=True
    )

    # Save the fully loaded model for subsequent fast loads
    print("Saving quantized model. This will create a faster-loading checkpoint...")
    tokenizer.save_pretrained(model_id)
    model.save_pretrained(model_id, safe_serialization=True)
    print("Save complete. Rerunning will now use the fast-loading version.")


# --- Inference ---
#prompt = "Eleborate the concept of 'Fourier transform' followed by simple examples illustration."
prompt = "What is meta-physics? Please explain concisely based on your understanding."
inputs = tokenizer(prompt, return_tensors="pt").to(device)

with torch.no_grad():
    outputs = model.generate(
        **inputs,
        do_sample=False,
        max_new_tokens=2048
    )

print(tokenizer.decode(outputs[0], skip_special_tokens=True))