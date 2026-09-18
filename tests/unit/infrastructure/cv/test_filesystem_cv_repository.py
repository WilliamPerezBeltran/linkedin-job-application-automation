"""Unit tests for `app.infrastructure.cv.filesystem_cv_repository.FilesystemCVRepository`.

Usa un `config/cvs.yaml` de fixture escrito en `tmp_path` (nunca el real del
repo) con `base_dir` apuntando también a `tmp_path` -- así los tests no
dependen del cwd del proceso ni se rompen si alguien edita el catálogo real
más adelante (mismo criterio que
`docs/agents/AGENTS.md` sección 16 y el pedido explícito del encargo).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.cv.cv_profile import CVProfile
from app.infrastructure.cv.exceptions import (
    CVCatalogMalformedError,
    CVCatalogNotFoundError,
    CVFileNotFoundError,
    CVNotFoundError,
)
from app.infrastructure.cv.filesystem_cv_repository import FilesystemCVRepository

pytestmark = pytest.mark.unit

_VALID_CATALOG = """
cvs:
  java:
    file: cvs/java/william-java.pdf.placeholder
    skills: [Java, Spring Boot, Kafka]
    summary: Java backend engineer.
  python:
    file: cvs/python/william-python.pdf.placeholder
    skills: [Python, FastAPI]
    summary: Python backend engineer.
"""


def _write_catalog(tmp_path: Path, content: str, *, create_placeholders: bool = True) -> Path:
    catalog_path = tmp_path / "cvs.yaml"
    catalog_path.write_text(content, encoding="utf-8")

    if create_placeholders:
        relative_paths = (
            "cvs/java/william-java.pdf.placeholder",
            "cvs/python/william-python.pdf.placeholder",
        )
        for relative in relative_paths:
            placeholder = tmp_path / relative
            placeholder.parent.mkdir(parents=True, exist_ok=True)
            placeholder.write_text("placeholder", encoding="utf-8")

    return catalog_path


class TestFilesystemCVRepositoryListCvs:
    def test_lists_every_cv_with_parsed_fields(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, _VALID_CATALOG)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        profiles = repository.list_cvs()

        assert profiles == [
            CVProfile(
                id="java",
                file="cvs/java/william-java.pdf.placeholder",
                skills=("Java", "Spring Boot", "Kafka"),
                summary="Java backend engineer.",
            ),
            CVProfile(
                id="python",
                file="cvs/python/william-python.pdf.placeholder",
                skills=("Python", "FastAPI"),
                summary="Python backend engineer.",
            ),
        ]

    def test_preserves_yaml_declaration_order(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, _VALID_CATALOG)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        ids = [profile.id for profile in repository.list_cvs()]

        assert ids == ["java", "python"]


class TestFilesystemCVRepositoryGetSummary:
    def test_returns_summary_for_known_cv_id(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, _VALID_CATALOG)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        assert repository.get_summary("python") == "Python backend engineer."

    def test_raises_for_unknown_cv_id(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, _VALID_CATALOG)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVNotFoundError):
            repository.get_summary("go")


class TestFilesystemCVRepositoryErrorHandling:
    def test_raises_explicit_error_when_catalog_file_is_missing(self, tmp_path: Path) -> None:
        repository = FilesystemCVRepository(tmp_path / "does-not-exist.yaml", base_dir=tmp_path)

        with pytest.raises(CVCatalogNotFoundError):
            repository.list_cvs()

    def test_raises_explicit_error_when_referenced_file_is_missing(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, _VALID_CATALOG, create_placeholders=False)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVFileNotFoundError):
            repository.list_cvs()

    def test_raises_explicit_error_for_missing_top_level_cvs_key(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, "not_cvs: {}", create_placeholders=False)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_for_empty_skills_list(self, tmp_path: Path) -> None:
        content = """
cvs:
  java:
    file: cvs/java/william-java.pdf.placeholder
    skills: []
    summary: Java backend engineer.
"""
        catalog_path = _write_catalog(tmp_path, content)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_for_missing_summary(self, tmp_path: Path) -> None:
        content = """
cvs:
  java:
    file: cvs/java/william-java.pdf.placeholder
    skills: [Java]
"""
        catalog_path = _write_catalog(tmp_path, content)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_for_invalid_yaml_syntax(self, tmp_path: Path) -> None:
        catalog_path = tmp_path / "cvs.yaml"
        catalog_path.write_text("cvs: [unclosed", encoding="utf-8")
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_when_cvs_key_is_not_a_mapping(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, "cvs: not-a-mapping", create_placeholders=False)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_when_cvs_key_is_an_empty_mapping(self, tmp_path: Path) -> None:
        catalog_path = _write_catalog(tmp_path, "cvs: {}", create_placeholders=False)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_when_a_cv_entry_is_not_a_mapping(self, tmp_path: Path) -> None:
        content = """
cvs:
  java: "not a mapping"
"""
        catalog_path = _write_catalog(tmp_path, content, create_placeholders=False)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_for_missing_file_field(self, tmp_path: Path) -> None:
        content = """
cvs:
  java:
    skills: [Java]
    summary: Java backend engineer.
"""
        catalog_path = _write_catalog(tmp_path, content, create_placeholders=False)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_for_blank_file_field(self, tmp_path: Path) -> None:
        content = """
cvs:
  java:
    file: "   "
    skills: [Java]
    summary: Java backend engineer.
"""
        catalog_path = _write_catalog(tmp_path, content, create_placeholders=False)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_for_non_string_item_in_skills(self, tmp_path: Path) -> None:
        content = """
cvs:
  java:
    file: cvs/java/william-java.pdf.placeholder
    skills: [Java, 123]
    summary: Java backend engineer.
"""
        catalog_path = _write_catalog(tmp_path, content)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()

    def test_raises_explicit_error_for_blank_item_in_skills(self, tmp_path: Path) -> None:
        content = """
cvs:
  java:
    file: cvs/java/william-java.pdf.placeholder
    skills: [Java, "   "]
    summary: Java backend engineer.
"""
        catalog_path = _write_catalog(tmp_path, content)
        repository = FilesystemCVRepository(catalog_path, base_dir=tmp_path)

        with pytest.raises(CVCatalogMalformedError):
            repository.list_cvs()
