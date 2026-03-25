"""Pre-download the ONNX embedding model into the Docker layer cache."""
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

print("Checking embedding model cache\u2026", flush=True)
DefaultEmbeddingFunction()
print("Embedding model ready.", flush=True)
