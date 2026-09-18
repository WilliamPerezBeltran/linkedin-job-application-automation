"""Skill normalization for CV<->job matching.

`CLAUDE.md` (sección "Responsabilidades" del CV Matching Agent, ver
`docs/agents/AGENTS.md` sección 11) pide poder comparar "JS" con
"JavaScript", "Node" con "Node.js", etc. Este módulo implementa la forma
más simple que cubre eso sin sobre-ingeniería
(`docs/agents/AGENTS.md` principio 9, "Avoid premature abstractions"):

1. Normalización estructural, genérica para cualquier skill: minúsculas,
   `strip()`, y remoción de espacios/guiones/puntos internos (para que
   "Spring Boot" == "spring-boot" == "SpringBoot" == "spring.boot").
2. Un diccionario de alias deliberadamente pequeño y explícito, solo para
   los tres casos que `CLAUDE.md` menciona por nombre como necesarios
   ("JS"/"JavaScript", "Node"/"Node.js") más "Golang"/"Go" (ya usado como
   ejemplo de sinónimo en el propio `config/cvs.yaml` de `CLAUDE.md`). No es
   un motor de sinónimos general ni usa NLP/embeddings -- si aparecen más
   casos reales en producción, se agregan acá mismo, no se reemplaza este
   enfoque por algo más complejo sin evidencia de que hace falta.

No mueve mayúsculas/minúsculas ni separadores fuera de este módulo: tanto
`CVMatcher` como (potencialmente) otros callers dentro de
`app/application/cv/` deben normalizar siempre a través de esta única
función, para no duplicar la lógica de comparación en más de un lugar.
"""

from __future__ import annotations

import re

# Alias explícitos, ya normalizados estructuralmente (ver `_strip_separators`)
# en el lado izquierdo -> forma canónica normalizada en el lado derecho.
_ALIASES: dict[str, str] = {
    "js": "javascript",
    "node": "nodejs",
    "nodejs": "nodejs",
    "golang": "go",
}

_SEPARATORS_RE = re.compile(r"[\s._-]+")


def normalize_skill(skill: str) -> str:
    """Returns a normalized, comparable form of a single skill string.

    No lanza excepciones por strings vacíos/solo-espacios: devuelve `""`,
    dejando que el caller decida qué hacer (p. ej. `CVMatcher` ignora
    skills vacías en vez de fallar, ver su docstring) -- este módulo no
    conoce reglas de negocio sobre qué es una skill "válida".
    """
    cleaned = _SEPARATORS_RE.sub("", skill.strip().lower())
    return _ALIASES.get(cleaned, cleaned)
