from transformers import pipeline

model_id = "openai/gpt-oss-20b"

pipe = pipeline(
    "text-generation",
    model=model_id,
    torch_dtype="auto",
    device_map={"": "cuda:0"},   # IMPORTANT: avoid CPU/disk offload for first run
)

messages = [{"role": "user", "content": "Explain quantum mechanics clearly and concisely."}]
outputs = pipe(messages, max_new_tokens=128)
print(outputs[0]["generated_text"][-1])
