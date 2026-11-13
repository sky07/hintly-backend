"""Resume parser service for PDF and DOCX files.

Extracts plain text from uploaded resume files.
"""

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyPDF2.

    Args:
        file_bytes: Raw PDF file bytes

    Returns:
        Extracted text string

    Raises:
        ValueError: If PDF cannot be parsed
    """
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)

        result = "\n\n".join(pages).strip()
        if not result:
            raise ValueError("PDF contains no extractable text (may be scanned/image-based)")
        return result

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to parse PDF: {e}")


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text from DOCX bytes using python-docx.

    Args:
        file_bytes: Raw DOCX file bytes

    Returns:
        Extracted text string

    Raises:
        ValueError: If DOCX cannot be parsed
    """
    try:
        from docx import Document

        doc = Document(io.BytesIO(file_bytes))
        paragraphs = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                paragraphs.append(text)

        result = "\n\n".join(paragraphs).strip()
        if not result:
            raise ValueError("DOCX contains no text content")
        return result

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to parse DOCX: {e}")


def parse_resume(file_bytes: bytes, filename: str) -> str:
    """Parse a resume file and extract text.

    Routes to appropriate parser based on file extension.

    Args:
        file_bytes: Raw file bytes
        filename: Original filename (used to determine format)

    Returns:
        Extracted text string

    Raises:
        ValueError: If file format is unsupported or parsing fails
    """
    if not filename:
        raise ValueError("Filename is required to determine file format")

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        text = extract_text_from_pdf(file_bytes)
    elif ext in ("docx", "doc"):
        if ext == "doc":
            raise ValueError("Legacy .doc format is not supported. Please convert to .docx or .pdf")
        text = extract_text_from_docx(file_bytes)
    else:
        raise ValueError(f"Unsupported file format: .{ext}. Please upload a PDF or DOCX file.")

    logger.info(f"[RESUME] Parsed {filename}: {len(text)} chars extracted")
    return text
