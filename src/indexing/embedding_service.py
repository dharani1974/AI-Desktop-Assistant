import json
import urllib.error
import urllib.request


class EmbeddingService:
    def __init__(self, model="nomic-embed-text"):
        self.model = model
        self.api_url = "http://localhost:11434/api/embed"

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Text cannot be empty.")

        payload = {
            "model": self.model,
            "input": text,
        }

        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))

        except urllib.error.URLError as error:
            raise RuntimeError(
                "Could not connect to Ollama. Make sure it is running."
            ) from error

        embeddings = result.get("embeddings")

        if not embeddings or not embeddings[0]:
            raise RuntimeError("Ollama returned no embedding vector.")

        return embeddings[0]