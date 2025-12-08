import torch
import os # NEW: Added for checking if the saved directory exists
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig # NEW: Added for quantization

# --- Configuration ---
# Original local path (from your script)
ORIGINAL_MODEL_PATH = "/root/.cache/huggingface/hub/models--mistralai--Mistral-7B-Instruct-v0.3/snapshots/0d4b76e1efeb5eb6f6b5e757c79870472e04bd3a"
# A new, local path to save the fast-loading, quantized model
SAVED_MODEL_PATH = "/root/.cache/huggingface/hub/models--mistralai--Mistral-7B-Instruct-v0.3/mistral_7b_int4_offline" 
# Fallback model ID (used if original local path fails on first run)
MODEL_ID = "mistralai/Mistral-7B-Instruct-v0.3"
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
if os.path.isdir(SAVED_MODEL_PATH):
    # 1. Fully Offline Load (Subsequent Runs)
    print(f"Loading fully offline model from: {SAVED_MODEL_PATH}")
    tokenizer = AutoTokenizer.from_pretrained(SAVED_MODEL_PATH, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        SAVED_MODEL_PATH,
        device_map="auto",
        local_files_only=True
    )
else:
    # 2. First-time Load, Quantize, and Save (First Run)
    print(f"Offline model not found. Loading, quantizing, and saving model to {SAVED_MODEL_PATH}...")
    try:
        # Load the sharded model, applying quantization
        tokenizer = AutoTokenizer.from_pretrained(ORIGINAL_MODEL_PATH, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            ORIGINAL_MODEL_PATH,
            quantization_config=bnb_config,
            device_map="auto",
            local_files_only=True
        )
    except Exception:
        # Fallback to download if local path is incomplete/failed
        print("Local load failed. Falling back to download (requires network)...")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, local_files_only=False)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            quantization_config=bnb_config,
            device_map="auto",
            local_files_only=False
        )

    # Save the fully loaded model for subsequent fast loads
    print("Saving quantized model. This will create a faster-loading checkpoint...")
    tokenizer.save_pretrained(SAVED_MODEL_PATH)
    model.save_pretrained(SAVED_MODEL_PATH, safe_serialization=True)
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