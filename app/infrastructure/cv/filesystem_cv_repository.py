"""`FilesystemCVRepository`: loads and parses the CV catalog
(`config/cvs.yaml` by default) from the local filesystem.

Satisface `app.application.cv.cv_catalog.CVCatalog` por structural typing
(no hereda de nada definido en `application/` -- ese `Protocol` no importa
`pyyaml` ni `pathlib` concretamente). Es la única pieza de este módulo que
sabe que el catálogo vive en un YAML en disco; `CVMatcher` solo conoce el
`Protocol`.

Responsabilidades explícitas de este repositorio (ver
`docs/agents/AGENTS.md` sección 11 y `ROADMAP.md` Fase 4, punto 3):

- Parsear `config/cvs.yaml` con `pyyaml` (ya es dependencia del proyecto,
  ver `pyproject.toml`).
- Validar la forma del catálogo (mapeo `cvs`, cada entrada con `file`,
  `skills` no vacía, `summary`) -- nunca `except Exception: pass`
  (`docs/agents/AGENTS.md` principio 15 y sección 23): cada forma de fallo
  tiene una excepción concreta en `app.infrastructure.cv.exceptions`.
- Validar que el `file` de cada entrada exista en disco -- un CV
  recomendado que apunte a un archivo inexistente debe fallar acá, temprano
  y explícito, no silenciosamente más adelante en la Fase 7 (adjunto de
  Gmail).

No cachea el catálogo parseado entre llamadas: `config/cvs.yaml` es un
archivo chico (unas pocas categorías) y releerlo/parsearlo en cada llamada
es una operación de microsegundos -- agregar una capa de cache sería
optimización prematura (`docs/agents/AGENTS.md` principio 10) sin evidencia
de que haga falta. Si en el futuro el catálogo crece mucho o `list_cvs()`
se llama en un hot path medido, se puede agregar cache ahí, documentado.

Deuda técnica conocida (`DEBT-CV-01`, hallada en code review de Fase 4,
severidad LOW, no bloqueante):

- `resolved_path = base_dir / file_field` (ver `_parse_entry`) no verifica
  que el resultado quede contenido dentro de `base_dir` -- un `file:
  ../../../etc/passwd` en `config/cvs.yaml` resolvería sin error de path
  traversal. Aceptado por ahora porque `config/cvs.yaml` es configuración
  local editada por el propio usuario, no input de red ni de un tercero.
  Riesgo a reconsiderar en Fase 7 (`gmail-agent`): si ese mismo
  `resolved_path` termina siendo el archivo que se adjunta a un draft de
  Gmail, conviene agregar ahí (o acá) un chequeo tipo
  `resolved_path.resolve().is_relative_to(base_dir.resolve())` antes de
  usarlo -- coordinar con `security-agent` antes de cerrar esa fase.
- Si `config/cvs.yaml` tiene dos claves idénticas bajo `cvs:` (p. ej.
  `java:` repetido), `yaml.safe_load` se queda con la última definición sin
  que este repositorio lo detecte -- comportamiento estándar de YAML/dict,
  no testeado explícitamente. Impacto bajo (error de edición manual del
  propio usuario); no se resuelve ahora por YAGNI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.application.cv.cv_profile import CVProfile
from app.infrastructure.cv.exceptions import (
    CVCatalogMalformedError,
    CVCatalogNotFoundError,
    CVFileNotFoundError,
    CVNotFoundError,
)

DEFAULT_CATALOG_PATH = Path("config/cvs.yaml")


class FilesystemCVRepository:
    """Loads `CVProfile`s from a YAML catalog on the local filesystem."""

    def __init__(
        self,
        catalog_path: Path | str = DEFAULT_CATALOG_PATH,
        *,
        base_dir: Path | str | None = None,
    ) -> None:
        """
        - `catalog_path`: ruta al YAML del catálogo. Por defecto
          `config/cvs.yaml`, resuelta relativa al directorio de trabajo del
          proceso -- misma asunción que ya hace el resto del proyecto
          (`pyproject.toml` fija `pythonpath = ["."]` para pytest; Alembic y
          `uvicorn` también se invocan desde la raíz del repo).
        - `base_dir`: directorio contra el que se resuelven los `file`
          relativos declarados en el catálogo (p. ej. `cvs/java/...`). Por
          defecto, el directorio de trabajo del proceso (mismo criterio que
          `catalog_path`) -- **no** el directorio padre de `catalog_path`,
          porque `CLAUDE.md` documenta los `file` del catálogo como rutas
          relativas a la raíz del repo (`cvs/java/william-java.pdf`), no
          relativas a `config/`. Los tests pasan un `base_dir` explícito
          (un directorio temporal) para no depender del cwd del proceso que
          corre la suite.
        """
        self._catalog_path = Path(catalog_path)
        self._base_dir = Path(base_dir) if base_dir is not None else Path.cwd()

    def list_cvs(self) -> list[CVProfile]:
        """Returns every CV in the catalog, in YAML declaration order.

        Python's `dict`/`yaml.safe_load` preservan el orden de inserción
        (YAML mapping order), así que el orden de esta lista es
        determinístico y coincide con el orden en que las categorías están
        escritas en `config/cvs.yaml` -- contrato del que depende el
        desempate de `CVMatcher.match` (ver su docstring).
        """
        raw_entries = self._load_raw_entries()
        return [self._parse_entry(cv_id, entry) for cv_id, entry in raw_entries.items()]

    def get_summary(self, cv_id: str) -> str:
        """Returns the pre-written summary for `cv_id`.

        Recorre `list_cvs()` en vez de guardar un índice propio -- consistente
        con la decisión de no cachear (ver docstring del módulo). Lanza
        `CVNotFoundError` si `cv_id` no existe en el catálogo.
        """
        for profile in self.list_cvs():
            if profile.id == cv_id:
                return profile.summary
        raise CVNotFoundError(cv_id)

    def _load_raw_entries(self) -> dict[str, Any]:
        if not self._catalog_path.exists():
            raise CVCatalogNotFoundError(str(self._catalog_path))

        try:
            with self._catalog_path.open("r", encoding="utf-8") as catalog_file:
                data = yaml.safe_load(catalog_file)
        except yaml.YAMLError as exc:
            raise CVCatalogMalformedError(str(self._catalog_path), f"invalid YAML: {exc}") from exc

        if not isinstance(data, dict) or "cvs" not in data:
            raise CVCatalogMalformedError(
                str(self._catalog_path), "missing top-level 'cvs' mapping"
            )

        entries = data["cvs"]
        if not isinstance(entries, dict) or not entries:
            raise CVCatalogMalformedError(
                str(self._catalog_path), "'cvs' must be a non-empty mapping"
            )

        return entries

    def _parse_entry(self, cv_id: str, entry: Any) -> CVProfile:
        if not isinstance(entry, dict):
            raise CVCatalogMalformedError(
                str(self._catalog_path), f"entry '{cv_id}' must be a mapping"
            )

        file_field = entry.get("file")
        skills_field = entry.get("skills")
        summary_field = entry.get("summary")

        if not isinstance(file_field, str) or not file_field.strip():
            raise CVCatalogMalformedError(
                str(self._catalog_path), f"entry '{cv_id}' missing a non-empty 'file'"
            )

        if not isinstance(skills_field, list) or not skills_field:
            raise CVCatalogMalformedError(
                str(self._catalog_path),
                f"entry '{cv_id}' must have a non-empty 'skills' list",
            )
        if not all(isinstance(skill, str) and skill.strip() for skill in skills_field):
            raise CVCatalogMalformedError(
                str(self._catalog_path),
                f"entry '{cv_id}' has a non-string or empty item in 'skills'",
            )

        if not isinstance(summary_field, str) or not summary_field.strip():
            raise CVCatalogMalformedError(
                str(self._catalog_path), f"entry '{cv_id}' missing a non-empty 'summary'"
            )

        resolved_path = self._base_dir / file_field
        if not resolved_path.exists():
            raise CVFileNotFoundError(cv_id, str(resolved_path))

        return CVProfile(
            id=cv_id,
            file=file_field,
            skills=tuple(skills_field),
            summary=summary_field.strip(),
        )
