import json
from pathlib import Path

from sqlalchemy import (
    create_engine,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_DIR = PROJECT_ROOT / "data" / "database"
DATABASE_PATH = DATABASE_DIR / "documents.db"


class Base(DeclarativeBase):
    pass


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    chunk_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "file_path",
            "chunk_number",
            name="unique_document_chunk",
        ),
    )


class DocumentStore:
    def __init__(self, database_path=None):
        self.database_path = Path(database_path or DATABASE_PATH).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        self.engine = create_engine(
            f"sqlite:///{self.database_path.as_posix()}"
        )
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        Base.metadata.create_all(self.engine)
        self._migrate_database()

    def _migrate_database(self):
        """Add the embedding column to existing databases if needed."""
        with self.engine.begin() as connection:
            columns = connection.execute(
                text("PRAGMA table_info(document_chunks)")
            ).fetchall()

            column_names = {column[1] for column in columns}

            if "embedding_json" not in column_names:
                connection.execute(
                    text(
                        "ALTER TABLE document_chunks "
                        "ADD COLUMN embedding_json TEXT"
                    )
                )

    def save_document(self, file_path, file_name, chunks):
        file_path = str(Path(file_path).resolve())

        with self.SessionLocal.begin() as session:
            session.query(DocumentChunk).filter_by(
                file_path=file_path
            ).delete()

            for number, content in enumerate(chunks, start=1):
                session.add(
                    DocumentChunk(
                        file_path=file_path,
                        file_name=file_name,
                        chunk_number=number,
                        content=content,
                    )
                )

    def save_embedding(self, chunk_id, embedding):
        """Save one embedding vector for an existing chunk."""
        if not embedding:
            raise ValueError("Embedding cannot be empty.")

        embedding_json = json.dumps(embedding)

        with self.SessionLocal.begin() as session:
            chunk = session.get(DocumentChunk, chunk_id)

            if chunk is None:
                raise ValueError(f"Chunk {chunk_id} was not found.")

            chunk.embedding_json = embedding_json

    def get_all_chunks(self):
        with self.SessionLocal() as session:
            chunks = session.scalars(
                select(DocumentChunk).order_by(
                    DocumentChunk.file_name,
                    DocumentChunk.chunk_number,
                )
            ).all()

            return [
                {
                    "id": item.id,
                    "file_path": item.file_path,
                    "file_name": item.file_name,
                    "chunk_number": item.chunk_number,
                    "content": item.content,
                    "embedding": (
                        json.loads(item.embedding_json)
                        if item.embedding_json
                        else None
                    ),
                }
                for item in chunks
            ]

    def delete_document(self, file_path):
        file_path = str(Path(file_path).resolve())

        with self.SessionLocal.begin() as session:
            return session.query(DocumentChunk).filter_by(
                file_path=file_path
            ).delete()

    def close(self):
        self.engine.dispose()