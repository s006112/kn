"""
helper_faiss_embedding.py

Responsibility:
Load a local/offline BGE M3 embedding model, expose a singleton-backed embedding entry point, and convert legacy PyTorch binary weights to safetensors when required for local model loading.

Used by:
* rag/faiss_index_builder.py
* rag/helper_rag_pipeline.py

Pipelines:
- texts -> tokenize -> forward_pass -> cls_pool -> normalize -> numpy
- load_failure -> detect_weights_format -> convert_weights -> reload

Invariants:
- Embedding inference runs with a locally cached model in offline mode.
- The public embed helper reuses a singleton model instance within the process.
- Returned embeddings are L2 normalized NumPy arrays on CPU memory.

Out of scope:
- Downloading model assets from remote registries.
- Building or saving FAISS indexes.
- Query ranking or retrieval orchestration.

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
        device = "cuda" if torch.cuda.is_available() else "cpu"

        # transformers>=4.57 refuses to load .bin weights unless torch>=2.6 (CVE-2025-32434 mitigation).
        # Prefer safetensors; if only .bin exists, convert once in-place to model.safetensors.
        try:
            self.model = AutoModel.from_pretrained(
                self.MODEL_PATH,
                local_files_only=True,
                use_safetensors=True,
            ).to(device)
        except Exception as e:
            if _looks_like_torch_bin_block(e):
                _maybe_convert_bin_to_safetensors(self.MODEL_PATH)
                self.model = AutoModel.from_pretrained(
                    self.MODEL_PATH,
                    local_files_only=True,
                    use_safetensors=True,
                ).to(device)
            else:
                raise

        self.model.eval()
    
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
            #hidden = out.last_hidden_state  # (batch_size, seq_len, hidden_size)
            emb = out.last_hidden_state[:, 0]  # CLS pooling
            #emb = out.last_hidden_state.mean(dim=1) # Mean pooling
            emb = torch.nn.functional.normalize(emb, p=2, dim=1)
            return emb.cpu().numpy()

# Singleton instance
_embedder = None

def embed(texts):
    global _embedder
    if _embedder is None:
        _embedder = BGEEmbedding()
    return _embedder.embed(texts)


def _looks_like_torch_bin_block(exc: Exception) -> bool:
    msg = str(exc)
    if ("CVE-2025-32434" in msg) or ("upgrade torch to at least v2.6" in msg):
        return True
    return ("no file named model.safetensors found" in msg) or ("no file named model.safetensors" in msg)


def _maybe_convert_bin_to_safetensors(model_dir: str) -> None:
    safetensors_path = os.path.join(model_dir, "model.safetensors")
    if os.path.exists(safetensors_path):
        return

    bin_path = os.path.join(model_dir, "pytorch_model.bin")
    if not os.path.exists(bin_path):
        raise FileNotFoundError(
            f"Missing weights: neither {safetensors_path} nor {bin_path} exists. "
            "To use torch<2.6 with transformers>=4.57, provide safetensors weights (model.safetensors)."
        )

    try:
        from safetensors.torch import save_file
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "safetensors is required to load embeddings with torch<2.6 on transformers>=4.57. "
            "Install it (offline wheel ok), or upgrade torch to >=2.6."
        ) from e

    print("Converting pytorch_model.bin -> model.safetensors (one-time)...")

    tmp_path = safetensors_path + ".tmp"
    try:
        try:
            obj = torch.load(bin_path, map_location="cpu", weights_only=True)
        except TypeError:
            obj = torch.load(bin_path, map_location="cpu")

        state_dict = obj.get("state_dict") if isinstance(obj, dict) else None
        if state_dict is None:
            state_dict = obj

        if not isinstance(state_dict, dict):
            raise TypeError(f"Unexpected weights type: {type(state_dict)}")

        try:
            save_file(state_dict, tmp_path)
            os.replace(tmp_path, safetensors_path)
        except PermissionError as e:
            raise PermissionError(
                f"Cannot write {safetensors_path}. Run with write permissions for the model directory, "
                "or place a safetensors-based model copy in a writable directory."
            ) from e
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
