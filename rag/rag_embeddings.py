"""
Responsibility:
Provides embedding utilities: L2 normalization and a small wrapper around Hugging Face Transformers models for turning text into vectors.

Used by:
* rag/email_02_chunks_to_faiss.py
* rag/email_03_web_gui.py

Pipelines:
- load_model -> encode_texts -> normalize_vectors -> return_embeddings

Invariants:
- `embed_documents` returns a list-of-lists of floats in the same order as input texts.
- `embed_query` returns a NumPy `float32` vector and applies L2 normalization when possible.
- When the underlying model exposes a callable `encode`, it is used preferentially.

Out of scope:
- Vector index construction, persistence, and retrieval.
- Model training or fine-tuning.
"""

import numpy as np
import os
import torch
from transformers import AutoConfig, AutoModel, AutoTokenizer


def l2_normalize(mat: list[list[float]]) -> list[list[float]]:
    """
    Purpose:
    L2-normalize a batch of vectors to unit length.

    Inputs:
    - mat: 2D list of numeric values shaped like `[num_vectors][dim]`.

    Outputs:
    - A 2D list of floats where each row has L2 norm 1.0 (unless the row is all zeros).

    Side effects:
    - None.

    Failure modes:
    - Raises if `mat` cannot be converted to a numeric NumPy array.
    """

    arr = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr.tolist()


class EmbeddingModel:
    """
    Responsibility:
    Loads a Transformers model and exposes `embed_documents` / `embed_query` for producing dense vectors.

    Invariants:
    - If CUDA is available and `device` starts with `"cuda"`, the model is moved to that device.
    - The underlying model is always put into eval mode.
    """

    def __init__(self, model_name: str, device: str, batch_size: int, task: str):
        """
        Purpose:
        Initialize tokenizer/model for embedding with a preference for local model directories.

        Inputs:
        - model_name: Hugging Face model ID or a local directory path.
        - device: Target device string (e.g. `"cpu"`, `"cuda"`, `"cuda:0"`).
        - batch_size: Batch size used by `encode` or the fallback tokenizer path.
        - task: Task string passed through to model-provided `encode` when available.

        Outputs:
        - None.

        Side effects:
        - Loads model configuration, tokenizer (best-effort), and model weights from disk/network.
        - May print notices when disabling flash attention or when tokenizer loading fails.

        Failure modes:
        - Raises if model configuration/model weights cannot be loaded.
        """

        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._task = task
        self._local_files_only = os.path.isdir(model_name)

        config = AutoConfig.from_pretrained(
            model_name,
            trust_remote_code=True,
            local_files_only=self._local_files_only,
        )
        if getattr(config, "use_flash_attn", False):
            print("ℹ️ Disabling flash attention for this model; using PyTorch attention instead")
            config.use_flash_attn = False

        self._tokenizer = None
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                trust_remote_code=True,
                local_files_only=self._local_files_only,
            )
        except Exception as e:
            print(f"⚠️ Tokenizer load failed for {model_name}: {e}")

        self._model = AutoModel.from_pretrained(
            model_name,
            trust_remote_code=True,
            config=config,
            local_files_only=self._local_files_only,
        )
        if torch.cuda.is_available() and device.startswith("cuda"):
            self._model.to(device)
        self._model.eval()

    def _encode(self, texts: list[str]):
        """
        Purpose:
        Produce embeddings for a list of texts using either `model.encode` or a tokenizer-based fallback.

        Inputs:
        - texts: List of input strings.

        Outputs:
        - Embeddings as a NumPy array, a Torch tensor, or a model-specific array-like return value.

        Side effects:
        - Runs the model in `torch.no_grad()` mode.

        Failure modes:
        - Raises `RuntimeError` if the model does not provide `encode` and no tokenizer is available.
        - Raises `RuntimeError` if the fallback model output does not expose `last_hidden_state`.
        """

        with torch.no_grad():
            encode_fn = getattr(self._model, "encode", None)
            if callable(encode_fn):
                vectors = encode_fn(
                    texts,
                    batch_size=self._batch_size,
                    task=self._task,
                    device=self._device,
                )
                if torch.is_tensor(vectors):
                    return vectors.detach().cpu().numpy()
                return vectors

            if self._tokenizer is None:
                raise RuntimeError(
                    f"Model {self._model_name} does not implement encode(), and tokenizer could not be loaded."
                )

            batch_size = max(int(self._batch_size or 1), 1)
            use_cuda = torch.cuda.is_available() and self._device.startswith("cuda")
            device = self._device if use_cuda else "cpu"

            batches: list[torch.Tensor] = []
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                tokens = self._tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=2048,
                    return_tensors="pt",
                )
                if use_cuda:
                    tokens = tokens.to(device)

                out = self._model(**tokens)
                last_hidden = getattr(out, "last_hidden_state", None)
                if last_hidden is None and isinstance(out, (tuple, list)) and out:
                    last_hidden = out[0]
                if last_hidden is None:
                    raise RuntimeError(f"Unexpected model output type for {self._model_name}: {type(out)}")

                emb = last_hidden[:, 0]  # CLS pooling
                emb = torch.nn.functional.normalize(emb, p=2, dim=1)
                batches.append(emb.detach().cpu())

            return torch.cat(batches, dim=0).numpy()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        Purpose:
        Embed a list of documents into a list-of-lists of floats.

        Inputs:
        - texts: Document texts.

        Outputs:
        - A list of embedding vectors aligned with the input order.

        Side effects:
        - Runs the underlying model.

        Failure modes:
        - Propagates exceptions from `_encode`.
        """

        if not texts:
            return []
        vectors = self._encode(texts)
        if isinstance(vectors, np.ndarray):
            return vectors.tolist()
        return [list(vec) for vec in vectors]

    def embed_query(self, text: str) -> np.ndarray:
        """
        Purpose:
        Embed a single query string into a NumPy `float32` vector and L2-normalize it when possible.

        Inputs:
        - text: Query string.

        Outputs:
        - A 1D NumPy array representing the query embedding.

        Side effects:
        - Runs the underlying model.

        Failure modes:
        - Propagates exceptions from `_encode`.
        """

        vectors = self._encode([text])
        if isinstance(vectors, np.ndarray):
            vec = vectors[0]
        elif torch.is_tensor(vectors):
            vec = vectors[0].detach().cpu().numpy()
        else:
            vec = np.asarray(vectors[0], dtype=np.float32)
        vec = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec


def build_embeddings(model_name: str, device: str, batch_size: int, task: str) -> EmbeddingModel:
    """
    Purpose:
    Convenience constructor for `EmbeddingModel`.

    Inputs:
    - model_name: Hugging Face model ID or local directory path.
    - device: Device string.
    - batch_size: Batch size used for encoding.
    - task: Task string forwarded to `encode` when available.

    Outputs:
    - An initialized `EmbeddingModel` instance.

    Side effects:
    - Loads model artifacts as part of `EmbeddingModel` initialization.

    Failure modes:
    - Propagates exceptions from `EmbeddingModel.__init__`.
    """

    return EmbeddingModel(
        model_name=model_name,
        device=device,
        batch_size=batch_size,
        task=task,
    )
