import numpy as np
import os
import torch
from transformers import AutoConfig, AutoModel, AutoTokenizer


def l2_normalize(mat: list[list[float]]) -> list[list[float]]:
    """L2-normalize a batch of vectors to unit length."""
    arr = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr.tolist()


class EmbeddingModel:
    """Lightweight wrapper around a Transformers model with encode()."""

    def __init__(self, model_name: str, device: str, batch_size: int, task: str):
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
        if not texts:
            return []
        vectors = self._encode(texts)
        if isinstance(vectors, np.ndarray):
            return vectors.tolist()
        return [list(vec) for vec in vectors]

    def embed_query(self, text: str) -> np.ndarray:
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
    return EmbeddingModel(
        model_name=model_name,
        device=device,
        batch_size=batch_size,
        task=task,
    )
