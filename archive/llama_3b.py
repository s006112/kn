import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    pipeline,
)

MODEL_ID = "adalberto-temp/Llama-3.2-3B-Instruct-GOLD"
question = (
#    "If you had a time machine, but could only go to the past or the future once and never return, which would you choose and why?"
    "How are you?"
) 


def load_model():
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=False)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        device_map="auto",
        torch_dtype=torch.float16,
        trust_remote_code=True,
    )
    return model, tokenizer


def main():
    model, tokenizer = load_model()
    generator = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        device_map="auto",
    )

    output = generator(
        [{"role": "user", "content": question}],
        max_new_tokens=16384,
        return_full_text=False,
    )[0]

    print(output["generated_text"])


if __name__ == "__main__":
    main()
