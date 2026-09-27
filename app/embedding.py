from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading

import numpy as np

DIMENSION = 384
DEFAULT_MODEL = 'intfloat/multilingual-e5-small'


class Embedder:
    """Vietnamese-capable semantic embeddings; hash is an explicit test/ablation option."""
    def __init__(self, model=None, cache_path=None):
        self.name = model or os.getenv('EMBEDDING_MODEL', DEFAULT_MODEL)
        self.model = None
        self.cache = OrderedDict()
        self.cache_limit = 2048
        self.lock = threading.RLock()
        self.cache_path = Path(cache_path or os.getenv('EMBEDDING_CACHE', 'runtime/embeddings.sqlite'))
        self.cache_folder = str(Path(os.getenv('EMBEDDING_MODEL_CACHE', 'runtime/models')).resolve())
        if self.name != 'hash':
            import torch
            torch.set_num_threads(int(os.getenv('EMBEDDING_THREADS', '4')))
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(self.name, cache_folder=self.cache_folder,
                                             device=os.getenv('EMBEDDING_DEVICE', 'cpu'), trust_remote_code=False)
            self.model.max_seq_length = 256
            if self.model.get_sentence_embedding_dimension() != DIMENSION:
                raise ValueError('The PostgreSQL schema requires 384-dimensional embeddings')
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.cache_path, timeout=60)) as cache, cache:
                cache.execute('CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL)')

    def _key(self, value):
        return hashlib.sha256((self.name + ':vi-ordered-v5:' + value).encode()).hexdigest()

    def _remember(self, key, vector):
        self.cache[key] = vector
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_limit:
            self.cache.popitem(last=False)

    def prepare(self, texts):
        """Bounded batched inference, cached on disk. No labels enter the encoder."""
        if not self.model:
            return
        with self.lock:
            unique = {self._key(t): t for t in texts}
            pending = {}
            with closing(sqlite3.connect(self.cache_path, timeout=60)) as cache, cache:
                for key, value in unique.items():
                    if key in self.cache:
                        continue
                    row = cache.execute('SELECT vector FROM embeddings WHERE key=?', (key,)).fetchone()
                    if row:
                        self._remember(key, np.frombuffer(row[0], dtype=np.float32).tolist())
                    else:
                        pending[key] = value
                if not pending:
                    return
                # Symmetric transaction/template matching uses E5's query prefix on both sides.
                inputs = [('query: ' + t) if 'multilingual-e5' in self.name else t for t in pending.values()]
                vectors = self.model.encode(inputs, normalize_embeddings=True,
                    batch_size=int(os.getenv('EMBEDDING_BATCH_SIZE', '32')), show_progress_bar=False)
                for key, vector in zip(pending, vectors):
                    values = np.asarray(vector, dtype=np.float32)
                    cache.execute('INSERT OR IGNORE INTO embeddings VALUES (?,?)', (key, values.tobytes()))
                    self._remember(key, values.tolist())

    def encode(self, value):
        value = re.sub(r'<NUM_\d+>', '<num>', value)
        key = self._key(value)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            if self.model:
                self.prepare([value])
                return self.cache[key]
            vector = np.zeros(DIMENSION, dtype=np.float32)
            words = value.split()
            features = words + [' '.join(words[i:i+2]) for i in range(len(words)-1)]
            features += [value[i:i+3] for i in range(max(0, len(value)-2))]
            for feature in features:
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                vector[int.from_bytes(digest[:4], 'little') % DIMENSION] += 1 if digest[4] & 1 else -1
            length = float(np.linalg.norm(vector))
            if length:
                vector /= length
            result = vector.tolist()
            self._remember(key, result)
            return result


def cosine(a, b):
    return max(0.0, min(1.0, float(np.dot(a, b))))


def vector_buckets(vector):
    indexes = sorted(range(len(vector)), key=lambda i: abs(vector[i]), reverse=True)[:8]
    return [f'v:{i}:{int(vector[i] >= 0)}' for i in indexes if vector[i] != 0]
