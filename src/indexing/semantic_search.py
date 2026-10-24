from pathlib import Path
from collections import defaultdict

from src.indexing.document_store import DocumentStore
from src.indexing.embedding_service import EmbeddingService


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("Vectors must have the same dimensions.")

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


class SemanticSearch:
    def __init__(self, store=None, embedding_service=None):
        self.store = store or DocumentStore()
        self._owns_store = store is None
        self.embedding_service = embedding_service or EmbeddingService()

    def search(
        self,
        query: str,
        limit: int = 5,
        file_path: str | None = None,
        file_paths: list[str] | None = None,
        summary: bool = False,
        chunks_per_file: int = 3,
        include_all_chunks: bool = False,
    ) -> list[dict]:

        if not query or not query.strip():
            return []

        if limit < 1:
            raise ValueError("Limit must be positive.")

        if chunks_per_file < 1:
            raise ValueError("chunks_per_file must be positive.")

        # Support both the old single-file argument and multiple files.
        allowed_paths = None

        if file_paths is not None:
            allowed_paths = {
                str(Path(path).resolve()) for path in file_paths
            }
        elif file_path is not None:
            allowed_paths = {
                str(Path(file_path).resolve())
            }

        chunks = self.store.get_all_chunks()

        # Search only within documents attached to this conversation.
        if allowed_paths is not None:
            chunks = [
                chunk for chunk in chunks
                if str(Path(chunk["file_path"]).resolve())
                in allowed_paths
            ]

        if not chunks:
            return []

        query_vector = self.embedding_service.embed(query)
        results = []

        for chunk in chunks:
            chunk_vector = chunk.get("embedding")

            # Repair missing or incompatible stored embeddings.
            if (
                chunk_vector is None
                or len(chunk_vector) != len(query_vector)
            ):
                chunk_vector = self.embedding_service.embed(
                    chunk["content"]
                )
                self.store.save_embedding(
                    chunk["id"],
                    chunk_vector,
                )

            score = cosine_similarity(
                query_vector,
                chunk_vector,
            )

            results.append({
                **chunk,
                "score": score,
            })

        # Rank the most relevant chunks first.
        results.sort(
            key=lambda item: item["score"],
            reverse=True,
        )
        # Comprehensive questions need complete document coverage.
        # Return every chunk in document order instead of truncating
        # the results to the normal semantic-search limit.
        if include_all_chunks and not summary:
            results.sort(
                key=lambda item: (
                    item["file_name"].casefold(),
                    item["chunk_number"],
                )
            )
            return results

        # Summary mode: include representative chunks from each file.
        if summary:
            by_file = defaultdict(list)

            for item in results:
                by_file[item["file_path"]].append(item)

            selected = []

            for file_items in by_file.values():
                # Include the first chunk for introductory context.
                first_chunk = min(
                    file_items,
                    key=lambda item: item["chunk_number"],
                )

                chosen = [first_chunk]
                chosen_ids = {first_chunk["id"]}

                # Add the most relevant remaining chunks.
                ranked_items = sorted(
                    file_items,
                    key=lambda item: item["score"],
                    reverse=True,
                )

                for item in ranked_items:
                    if item["id"] not in chosen_ids:
                        chosen.append(item)
                        chosen_ids.add(item["id"])

                    if len(chosen) >= chunks_per_file:
                        break

                selected.extend(chosen)

            selected.sort(
                key=lambda item: (
                    item["file_name"].casefold(),
                    item["chunk_number"],
                )
            )

            return selected

        # Normal question mode: select chunks round-robin across files.
        by_file = defaultdict(list)

        for item in results:
            by_file[item["file_path"]].append(item)

        for file_items in by_file.values():
            file_items.sort(
                key=lambda item: item["score"],
                reverse=True,
            )

        selected = []
        file_groups = list(by_file.values())
        max_rounds = max(
            (len(items) for items in file_groups),
            default=0,
        )

        for round_index in range(max_rounds):
            for file_items in file_groups:
                if round_index < len(file_items):
                    selected.append(file_items[round_index])

                    if len(selected) >= limit:
                        return selected

        return selected[:limit]

    def close(self):
        if self._owns_store:
            self.store.close()