from src.indexing.document_loader import load_document
from src.indexing.text_chunker import chunk_text
from src.indexing.document_store import DocumentStore
from src.indexing.embedding_service import EmbeddingService


class DocumentIndexer:
    def __init__(self, store=None, embedding_service=None):
        self.store = store or DocumentStore()
        self._owns_store = store is None
        self.embedding_service = embedding_service or EmbeddingService()

    def index_document(self, file_path):
        document = load_document(file_path)

        chunks = chunk_text(
            document["text"],
            chunk_size=800,
            overlap=120,
        )

        self.store.save_document(
            document["file_path"],
            document["file_name"],
            chunks,
        )

        saved_chunks = [
            item
            for item in self.store.get_all_chunks()
            if item["file_path"] == document["file_path"]
        ]

        for chunk in saved_chunks:
            if chunk["embedding"] is None:
                vector = self.embedding_service.embed(chunk["content"])
                self.store.save_embedding(chunk["id"], vector)

        return {
            "file_name": document["file_name"],
            "chunk_count": len(saved_chunks),
        }

    def index_missing_embeddings(self):
        """Generate vectors for existing chunks without embeddings."""
        chunks = self.store.get_all_chunks()
        indexed_count = 0

        for chunk in chunks:
            if chunk["embedding"] is None:
                vector = self.embedding_service.embed(chunk["content"])
                self.store.save_embedding(chunk["id"], vector)
                indexed_count += 1

        return indexed_count

    def close(self):
        if self._owns_store:
            self.store.close()