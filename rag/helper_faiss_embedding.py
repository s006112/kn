"""
helper_faiss_embedding.py

Responsibility:
Provide a local/offline embedding helper (BGE M3) for FAISS indexing and RAG retrieval workflows.

Used by:
* rag/faiss_index_builder.py
* rag/helper_rag_pipeline.py
* rag/jsonl_to_faiss.py

"""

import os
import torch
from transformers import AutoTokenizer, AutoModel

class BGEEmbedding:
    MODEL_PATH = "/root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181"
    
    def __init__(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        print(f"Loading embedding model: {self.MODEL_PATH}")
        self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_PATH, local_files_only=True)
        self.model = AutoModel.from_pretrained(self.MODEL_PATH, local_files_only=True).cuda()
    
    def embed(self, texts):
        with torch.no_grad():
            tokens = self.tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=2048,
                return_tensors="pt",
            ).to(self.model.device)
            out = self.model(**tokens)
            emb = out.last_hidden_state[:, 0]  # CLS pooling
            emb = torch.nn.functional.normalize(emb, p=2, dim=1)
            return emb.cpu().numpy()

# Singleton instance
_embedder = None

def embed(texts):
    global _embedder
    if _embedder is None:
        _embedder = BGEEmbedding()
    return _embedder.embed(texts)
