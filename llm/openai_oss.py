import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, AutoConfig
import json
import os

# ⚠️ 注意：將此路徑設置為直接包含 model.safetensors 和 tokenizer.json 的目錄。
MODEL_PATH = "/root/.cache/huggingface/hub/models--openai--gpt-oss-20b/snapshots/6cee5e81ee83917806bbde320786a8fb61efebee"

# 設置計算設備
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# 載入模型和分詞器
try:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True, use_fast=False, trust_remote_code=True)
except Exception:
    # If local loading fails, download from HuggingFace Hub
    tokenizer = AutoTokenizer.from_pretrained("openai/gpt-oss-20b", use_fast=False, trust_remote_code=True)

# Load config and remove problematic quantization_config
config_path = os.path.join(MODEL_PATH, "config.json")
if os.path.exists(config_path):
    with open(config_path, 'r') as f:
        config_dict = json.load(f)
    config_dict.pop("quantization_config", None)
    from transformers.models.gpt_oss import GptOssConfig
    config = GptOssConfig(**config_dict)
else:
    config = AutoConfig.from_pretrained("openai/gpt-oss-20b", trust_remote_code=True)

#    - 這是原始代碼中不使用量化時最穩定的載入方式。
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH if torch.cuda.is_available() else "openai/gpt-oss-20b",
    config=config,
    dtype=torch.bfloat16,   # float32, bfloat16
    device_map="auto",
    local_files_only=torch.cuda.is_available(),
    trust_remote_code=True
)

# 定義提示
PROMPT = "Eleborate the concept of 'Laplace transform' followed by simple examples illustration. no need follow up question"
messages = [{"role": "user", "content": PROMPT}]

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