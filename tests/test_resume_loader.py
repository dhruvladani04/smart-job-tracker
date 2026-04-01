"""
Tests for resume loading functionality.
"""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from job_scraper.resume_loader import (
    discover_resume_paths,
    load_resume_bundle,
    _unique_paths,
    _read_pdf,
    _read_text_source,
    DEFAULT_RESUME_PATHS,
)


class TestUniquePaths:
    """Tests for path deduplication."""

    def test_removes_duplicates(self, tmp_path: Path):
        """Test that duplicate paths are removed."""
        file1 = tmp_path / "resume.pdf"
        file1.touch()

        paths = [file1, file1, file1]
        unique = _unique_paths(paths)

        assert len(unique) == 1

    def test_preserves_order(self, tmp_path: Path):
        """Test that first occurrence is preserved."""
        file1 = tmp_path / "a.pdf"
        file2 = tmp_path / "b.pdf"
        file1.touch()
        file2.touch()

        paths = [file1, file2, file1]
        unique = _unique_paths(paths)

        assert unique[0] == file1
        assert unique[1] == file2


class TestDiscoverResumePaths:
    """Tests for resume path discovery."""

    def test_explicit_paths_used(self, tmp_path: Path):
        """Test that explicit paths are used when provided."""
        resume = tmp_path / "my_resume.pdf"
        resume.touch()

        paths = discover_resume_paths([str(resume)])

        assert len(paths) == 1
        assert paths[0] == resume

    def test_raises_on_missing_explicit_paths(self, tmp_path: Path):
        """Test error when explicit paths don't exist."""
        nonexistent = tmp_path / "does_not_exist.pdf"

        with pytest.raises(FileNotFoundError) as exc_info:
            discover_resume_paths([str(nonexistent)])

        assert "does_not_exist.pdf" in str(exc_info.value)

    def test_no_default_paths_exist(self, tmp_path: Path, monkeypatch):
        """Test behavior when default paths don't exist."""
        # Change to temp directory where default files don't exist
        monkeypatch.chdir(tmp_path)

        # No explicit paths, no defaults exist
        with pytest.raises(FileNotFoundError) as exc_info:
            discover_resume_paths(None)

        assert "No resume sources found" in str(exc_info.value)

    def test_fallback_to_generic_names(self, tmp_path: Path, monkeypatch):
        """Test fallback to resume.pdf/txt/json."""
        monkeypatch.chdir(tmp_path)

        # Create fallback file
        resume = tmp_path / "resume.pdf"
        resume.touch()

        paths = discover_resume_paths(None)

        assert len(paths) == 1
        assert paths[0].name == "resume.pdf"


class TestReadTextSource:
    """Tests for reading text sources."""

    def test_read_txt_file(self, tmp_path: Path):
        """Test reading a plain text file."""
        txt_file = tmp_path / "resume.txt"
        content = "John Doe\nSoftware Engineer\nExperience: ..."
        txt_file.write_text(content)

        result = _read_text_source(txt_file)

        assert "John Doe" in result
        assert "Software Engineer" in result

    def test_read_json_file(self, tmp_path: Path):
        """Test reading a JSON file."""
        json_file = tmp_path / "resume.json"
        data = {"name": "John Doe", "experience": ["Role 1", "Role 2"]}
        json_file.write_text(json.dumps(data))

        result = _read_text_source(json_file)

        assert "John Doe" in result
        assert "Role 1" in result

    def test_read_pdf_file(self, tmp_path: Path):
        """Test reading a PDF file."""
        pdf_file = tmp_path / "resume.pdf"

        # Create a mock PDF
        with patch('job_scraper.resume_loader.PdfReader') as mock_reader:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "PDF Content Here"
            mock_reader.return_value.pages = [mock_page]

            result = _read_pdf(pdf_file)

            assert "PDF Content Here" in result

    def test_read_empty_pdf_raises(self, tmp_path: Path):
        """Test error when PDF has no text."""
        pdf_file = tmp_path / "empty.pdf"

        with patch('job_scraper.resume_loader.PdfReader') as mock_reader:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = ""
            mock_reader.return_value.pages = [mock_page]

            with pytest.raises(ValueError) as exc_info:
                _read_pdf(pdf_file)

            assert "No readable text" in str(exc_info.value)


class TestLoadResumeBundle:
    """Tests for resume bundle loading."""

    def test_load_single_resume(self, tmp_path: Path):
        """Test loading a single resume file."""
        resume = tmp_path / "resume.txt"
        resume.write_text("John Doe\nSoftware Engineer")

        bundle = load_resume_bundle([str(resume)])

        assert "source_paths" in bundle
        assert "resume_text" in bundle
        assert len(bundle["source_paths"]) == 1
        assert "John Doe" in bundle["resume_text"]

    def test_load_multiple_resumes(self, tmp_path: Path):
        """Test loading multiple resume files."""
        resume1 = tmp_path / "pm_resume.txt"
        resume2 = tmp_path / "tech_resume.txt"
        resume1.write_text("PM Resume Content")
        resume2.write_text("Tech Resume Content")

        bundle = load_resume_bundle([str(resume1), str(resume2)])

        assert "PM Resume Content" in bundle["resume_text"]
        assert "Tech Resume Content" in bundle["resume_text"]
        assert len(bundle["source_paths"]) == 2

    def test_bundle_includes_source_names(self, tmp_path: Path):
        """Test that bundle includes source file names."""
        resume = tmp_path / "my_resume.pdf"
        resume.touch()

        with patch('job_scraper.resume_loader._read_text_source') as mock_read:
            mock_read.return_value = "Resume content"

            bundle = load_resume_bundle([str(resume)])

            assert "my_resume.pdf" in bundle["resume_text"]

    def test_empty_resume_raises(self, tmp_path: Path):
        """Test error when all resumes are empty."""
        resume = tmp_path / "empty.txt"
        resume.write_text("")  # Empty file

        with patch('job_scraper.resume_loader._read_text_source') as mock_read:
            mock_read.return_value = ""

            with pytest.raises(ValueError) as exc_info:
                load_resume_bundle([str(resume)])

            assert "empty" in str(exc_info.value).lower()

    def test_bundle_format(self, tmp_path: Path):
        """Test the format of the resume bundle."""
        resume = tmp_path / "resume.txt"
        resume.write_text("Content here")

        bundle = load_resume_bundle([str(resume)])

        # Verify structure
        assert isinstance(bundle, dict)
        assert "source_paths" in bundle
        assert "resume_text" in bundle
        assert isinstance(bundle["source_paths"], list)
        assert isinstance(bundle["resume_text"], str)

        # Verify source format in resume_text
        assert "Source: resume.txt" in bundle["resume_text"]
