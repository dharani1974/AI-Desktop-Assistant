from src.indexing.document_store import DocumentStore


class DocumentSearch:
    def __init__(self, store=None):
        self.store = store or DocumentStore()
        self._owns_store = store is None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if not query or not query.strip():
            return []

        if limit < 1:
            raise ValueError("Limit must be positive.")

        # Convert the query into lowercase search terms.
        terms = set(query.lower().split())

        if not terms:
            return []

        results = []

        for chunk in self.store.get_all_chunks():
            content = chunk["content"].lower()

            # Count how many query terms appear in the chunk.
            score = sum(
                1 for term in terms
                if term in content
            )

            if score > 0:
                results.append({
                    **chunk,
                    "score": score,
                })

        # Prefer chunks matching the most query terms.
        results.sort(
            key=lambda item: (
                -item["score"],
                item["file_name"].lower(),
                item["chunk_number"],
            )
        )

        return results[:limit]

    def close(self):
        if self._owns_store:
            self.store.close()