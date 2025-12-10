from pathlib import Path
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig

# ⚠️ 注意：將此路徑設置為直接包含 model.safetensors 和 tokenizer.json 的目錄。
#MODEL_PATH = "/root/.cache/huggingface/hub/google-gemma-3-270m-it/snapshots/ac82b4e820549b854eebf28ce6dedaf9fdfa17b3"
MODEL_PATH = "/root/.cache/huggingface/hub/google-gemma-3-1b-it/snapshots/dcc83ea841ab6100d6b47a070329e1ba4cf78752"
#MODEL_PATH = "/root/.cache/huggingface/hub/google-gemma-3-4b-it/"

# 設置計算設備
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# 載入模型和分詞器
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)

#    - 這是原始代碼中不使用量化時最穩定的載入方式。
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH,
    dtype=torch.float32,   # float32, bfloat16
    device_map="auto",
    # 移除 quantization_config
    local_files_only=True
)

# 定義提示
PROMPT = "Eleborate the concept of 'Laplace transform' followed by simple examples illustration."
messages = [{"role": "user", "content": PROMPT}]

# 應用聊天模板以符合 instruction-tuned 模型的要求
if hasattr(tokenizer, "chat_template") and tokenizer.chat_template is not None:
    PROMPT = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

# 準備輸入
inputs = tokenizer(PROMPT, return_tensors="pt").to(DEVICE)

# 生成參數設定：強制模型生成完整內容直到達到 max_new_tokens (2048)
GEN_KWARGS = {
    "do_sample": False,
    "max_new_tokens":4096,
    "pad_token_id": tokenizer.eos_token_id, 
}

with torch.no_grad():
    outputs = model.generate(**inputs, **GEN_KWARGS)

# 由於啟用了 eos_token_id=None，結果可能會比較長。
print(tokenizer.decode(outputs[0], skip_special_tokens=True))