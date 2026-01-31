import json
import os

import torch
from transformers import AutoTokenizer, Mistral3Config, Mistral3ForConditionalGeneration, PreTrainedTokenizerFast
from safetensors import safe_open

MODEL_PATH = "/root/.cache/huggingface/hub/Ministral-3-3B-Instruct-2512"
MISTRAL_CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "{% if loop.first %}<s>{% endif %}"
    "{% if message['role'] == 'user' %}"
    "[INST] {{ message['content'] }} [/INST]"
    "{% elif message['role'] == 'assistant' %}"
    "{{ message['content'] }}</s>"
    "{% elif message['role'] == 'system' %}"
    "[SYSTEM_PROMPT] {{ message['content'] }} [/SYSTEM_PROMPT]"
    "{% endif %}"
    "{% endfor %}"
)


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
        config: dict = {}
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as handle:
                cfg = json.load(handle)
            keys = ("bos_token", "eos_token", "pad_token", "unk_token", "model_max_length")
            config.update({key: cfg[key] for key in keys if key in cfg})
            extra_tokens = cfg.get("additional_special_tokens")
            if extra_tokens is None and isinstance(cfg.get("extra_special_tokens"), list):
                extra_tokens = cfg["extra_special_tokens"]
            if extra_tokens is not None:
                config["additional_special_tokens"] = extra_tokens
        return PreTrainedTokenizerFast(tokenizer_file=tokenizer_json, **config)


def load_config(model_path: str):
    config_path = os.path.join(model_path, "config.json")
    with open(config_path, "r", encoding="utf-8") as handle:
        config_dict = json.load(handle)
    config_dict.pop("quantization_config", None)
    text_config = config_dict.get("text_config")
    if isinstance(text_config, dict) and text_config.get("model_type") == "ministral3":
        text_config["model_type"] = "mistral"
    return Mistral3Config.from_dict(config_dict)

def dequantize_fp8_weights(model, model_path: str, target_dtype: torch.dtype | None = None):
    if not any(
        p.dtype in (torch.float8_e4m3fn, torch.float8_e5m2) for p in model.parameters()
    ):
        return

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
            dtype = target_dtype if target_dtype is not None else torch.bfloat16
            p.data = (p.to(torch.float32) * scale_inv).to(dtype)


def build_inputs(tokenizer, prompt: str):
    messages = [{"role": "user", "content": prompt}]
    if not getattr(tokenizer, "chat_template", None):
        tokenizer.chat_template = MISTRAL_CHAT_TEMPLATE
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    inputs.pop("token_type_ids", None)
    return inputs


# 載入模型和分詞器
tokenizer = load_tokenizer(MODEL_PATH)

model = Mistral3ForConditionalGeneration.from_pretrained(
    MODEL_PATH,
    config=load_config(MODEL_PATH),
    torch_dtype=torch.float32,  # float16, float32
    device_map="auto",
    local_files_only=True
)
dequantize_fp8_weights(model, MODEL_PATH, target_dtype=model.dtype)

# 定義提示
PROMPT = "Eleborate the concept of 'Laplace transform' followed by simple examples illustration. no need follow up question"

# 準備輸入
inputs = build_inputs(tokenizer, PROMPT).to(model.device)

GEN_KWARGS = {
    "do_sample": True,
    "temperature": 0.2,
    "top_p": 0.95,
    "max_new_tokens": 512,
    "pad_token_id": tokenizer.eos_token_id,
}

with torch.no_grad():
    outputs = model.generate(**inputs, **GEN_KWARGS)

generated = outputs[0][inputs["input_ids"].shape[1] :]
print(tokenizer.decode(generated, skip_special_tokens=True))
