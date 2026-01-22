import json
import os

import torch
from transformers import AutoTokenizer, Mistral3Config, Mistral3ForConditionalGeneration, PreTrainedTokenizerFast
from safetensors import safe_open

MODEL_PATH = "/root/.cache/huggingface/hub/Ministral-3-3B-Instruct-2512"


# 設置計算設備
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

def load_tokenizer(model_path: str):
    try:
        return AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    except ValueError as exc:
        if "Tokenizer class" not in str(exc):
            raise
        tokenizer_json = os.path.join(model_path, "tokenizer.json")
        config_path = os.path.join(model_path, "tokenizer_config.json")
        if not os.path.exists(tokenizer_json):
            raise
        config = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as handle:
                cfg = json.load(handle)
            for key in ("bos_token", "eos_token", "pad_token", "unk_token", "model_max_length"):
                if key in cfg:
                    config[key] = cfg[key]
            if cfg.get("additional_special_tokens") is not None:
                config["additional_special_tokens"] = cfg["additional_special_tokens"]
            elif isinstance(cfg.get("extra_special_tokens"), list):
                config["additional_special_tokens"] = cfg["extra_special_tokens"]
        return PreTrainedTokenizerFast(tokenizer_file=tokenizer_json, **config)


def load_config(model_path: str):
    config_path = os.path.join(model_path, "config.json")
    with open(config_path, "r", encoding="utf-8") as handle:
        config_dict = json.load(handle)
    # This repo ships a quantization_config (fp8/static) that may not be supported
    # by the installed transformers build. We load in full precision here.
    config_dict.pop("quantization_config", None)
    text_config = config_dict.get("text_config")
    if isinstance(text_config, dict) and text_config.get("model_type") == "ministral3":
        text_config["model_type"] = "mistral"
    return Mistral3Config.from_dict(config_dict)

def dequantize_fp8_weights(model, model_path: str):
    weights_path = os.path.join(model_path, "model.safetensors")
    if not os.path.exists(weights_path):
        return

    def to_checkpoint_key(param_name: str) -> str:
        if not param_name.startswith("model."):
            return param_name
        key = param_name[len("model.") :]
        if key.startswith("language_model."):
            key = "language_model.model." + key[len("language_model.") :]
        return key

    with safe_open(weights_path, framework="pt", device="cpu") as f:
        keys = set(f.keys())
        for name, p in model.named_parameters():
            if p.dtype not in (torch.float8_e4m3fn, torch.float8_e5m2):
                continue
            ckpt_key = to_checkpoint_key(name)
            if not ckpt_key.endswith(".weight"):
                continue
            scale_key = ckpt_key[: -len(".weight")] + ".weight_scale_inv"
            if scale_key not in keys:
                raise KeyError(f"Missing FP8 scale for {name}: {scale_key}")
            scale_inv = f.get_tensor(scale_key).to(device=p.device, dtype=torch.float32)
            p.data = (p.to(torch.float32) * scale_inv).to(torch.bfloat16)


# 載入模型和分詞器
tokenizer = load_tokenizer(MODEL_PATH)

#    - 這是原始代碼中不使用量化時最穩定的載入方式。
model = Mistral3ForConditionalGeneration.from_pretrained(
    MODEL_PATH,
    config=load_config(MODEL_PATH),
    dtype=torch.bfloat16,   # float32, bfloat16
    device_map="auto",
    # 移除 quantization_config
    local_files_only=True
)
dequantize_fp8_weights(model, MODEL_PATH)

# 定義提示
PROMPT = "Eleborate the concept of 'Laplace transform' followed by simple examples illustration. no need follow up question"

# 準備輸入
inputs = tokenizer(PROMPT, return_tensors="pt")
inputs.pop("token_type_ids", None)  # Some tokenizers return this but Mistral3 doesn't use it.
inputs = inputs.to(DEVICE)

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
