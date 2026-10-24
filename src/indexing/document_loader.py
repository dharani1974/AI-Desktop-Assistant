
from pathlib import Path

from pypdf import PdfReader
from docx import Document


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def load_document(file_path: str) -> dict:
    """Extract text from a supported local document."""

    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    extension = path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            "Unsupported file type. Choose PDF, DOCX, or TXT."
        )

    if extension == ".pdf":
        reader = PdfReader(str(path))

        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported yet.")

        text = "\n".join(
            page.extract_text() or "" for page in reader.pages
        )

    elif extension == ".docx":
        document = Document(str(path))
        paragraphs = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]
        text = "\n".join(paragraphs)

    else:
        text = path.read_text(encoding="utf-8-sig")

    text = text.strip()

    if not text:
        raise ValueError(
            "No readable text found. The document may be empty "
            "or the PDF may contain scanned images."
        )

    return {
        "file_name": path.name,
        "file_path": str(path.resolve()),
        "file_type": extension[1:],
        "text": text,
        "character_count": len(text),
    }
