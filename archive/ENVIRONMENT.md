# Environment Information

## Python
- Version: 3.10.12

## FAISS
- Package: faiss-gpu
- Version: 1.7.2
- Build: GPU-enabled (CUDA 12.x, as per nvidia-* packages)

## System
- Platform: Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.35
- OS: Ubuntu 20.04.6 LTS (under WSL2)
- CPU/GPU: NVIDIA CUDA 12.8 libraries installed

## Key Packages
(derived from `pip list`)

- **Vector / Embeddings**
  - faiss-gpu == 1.7.2
  - numpy == 1.26.2
  - scikit-learn == 1.7.1
  - sentence-transformers == 5.1.0
  - transformers == 4.55.4
  - huggingface-hub == 0.34.4

- **LangChain / RAG stack**
  - langchain == 0.3.27
  - langchain-core == 0.3.74
  - langchain-community == 0.3.27
  - langchain-openai == 0.3.32
  - langchain-huggingface == 0.3.1
  - langchain-text-splitters == 0.3.9
  - langsmith == 0.4.15

- **LLM Providers**
  - openai == 1.102.0

- **Web/Serving**
  - gradio == 5.44.1
  - fastapi == 0.116.1
  - uvicorn == 0.35.0

- **Other Useful**
  - pandas == 2.3.1
  - scipy == 1.15.3
  - tiktoken == 0.11.0
  - pydantic == 2.11.7

## Notes
- CUDA_VISIBLE_DEVICES: (currently set empty to force CPU in code, but CUDA libs are installed)
- TORCH_USE_CUDA_DSA=0
- Torch: 2.9.0.dev20250723+cu128
- Embedding model (at indexing): `jinaai/jina-embeddings-v3`
- Normalize embeddings: True
- Prompt settings: `retrieval.query` / `retrieval.passage`
