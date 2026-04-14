"""
Utilities for loading resume sources from PDF, TXT, or JSON files.
"""
from __future__ import annotations

import json
from pathlib import Path

from pypdf import PdfReader

DEFAULT_RESUME_PATHS = [
    "resume.json",
    "Dhruv_Ladani_Resume_PM.pdf",
    "Dhruv_Ladani_Resume_Tech.pdf",
]


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []

    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)

    return unique


def discover_resume_paths(explicit_paths: list[str] | None = None) -> list[Path]:
    """Resolve resume source paths, defaulting to Dhruv's PDFs when present."""
    if explicit_paths:
        paths = [Path(path) for path in explicit_paths]
    else:
        paths = [Path(path) for path in DEFAULT_RESUME_PATHS if Path(path).exists()]
        if not paths:
            for fallback in ("resume.pdf", "resume.txt", "resume.json"):
                fallback_path = Path(fallback)
                if fallback_path.exists():
                    paths.append(fallback_path)

    paths = _unique_paths(paths)

    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"Resume source(s) not found: {', '.join(missing)}"
        )

    if not paths:
        defaults = ", ".join(DEFAULT_RESUME_PATHS)
        raise FileNotFoundError(
            "No resume sources found. Pass --resume or add one of: "
            f"{defaults}, resume.txt, resume.json"
        )

    return paths


def _read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []

    for page in reader.pages:
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append(page_text)

    if not pages:
        raise ValueError(f"No readable text found in PDF: {path}")

    return "\n\n".join(pages)


def _read_text_source(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return json.dumps(data, indent=2)

    return path.read_text(encoding="utf-8")


def load_resume_bundle(resume_paths: list[str] | None = None) -> dict:
    """Load resume sources and return combined prompt text."""
    paths = discover_resume_paths(resume_paths)
    sections = []

    for path in paths:
        text = _read_text_source(path).strip()
        if text:
            sections.append(f"Source: {path.name}\n{text}")

    if not sections:
        raise ValueError("Resume sources were found, but they were empty.")

    return {
        "source_paths": [str(path) for path in paths],
        "resume_text": "\n\n".join(sections),
    }
