"""`CVMatcher`: deterministic Job-to-CV matching (Fase 4, ROADMAP.md).

Implementa el primer paso -- y, por ahora, único -- de la estrategia
híbrida descrita en `docs/agents/AGENTS.md` sección 11:

```
Job -> extraer requirements -> normalizar skills -> matching determinístico
   -> [análisis semántico/LLM opcional, no implementado -- YAGNI] -> recomendación
```

El ROADMAP (Fase 4, punto 4) marca el fallback semántico vía LLM como
**opcional** y a implementar solo "si el determinístico es insuficiente".
No hay evidencia de que lo sea para el caso de prueba pedido (`["Java",
"Spring Boot"]` -> CV java, no python) ni para el resto de categorías del
catálogo -- agregarlo ahora violaría el principio 9 de
`docs/agents/AGENTS.md` ("Avoid premature abstractions") y el criterio de
`TOKEN_OPTIMIZATION.md` ("preferir procesamiento determinístico... por
sobre una llamada a LLM siempre que resuelva la tarea de forma confiable").
Si en el futuro se detectan casos reales donde el matching determinístico
falla (p. ej. una skill compuesta o un sinónimo no cubierto por
`app.application.cv.skill_normalizer`), el fallback semántico se agrega ahí
explícitamente, no acá por adelantado.

Conexión con el resto del pipeline (no implementada en este módulo -- ver
`docs/agents/AGENTS.md` sección 11, "no es tu ownership escribir el use
case completo"): un futuro use case (p. ej.
`app/application/use_cases/select_best_cv.py`, ownership compartido con
`backend-engineer`) haría:

```python
result = matcher.match(job_analysis.skills)
if result.recommended_cv is not None:
    job_analysis.record_cv_recommendation(
        recommended_cv=result.recommended_cv, match_score=result.confidence
    )
    job_analysis_repository.save(job_analysis)
    job.mark_cv_selected()
    job_repository.save(job)
# Si `result.recommended_cv` es None, el use case decide qué hacer (dejar el
# job en RELEVANT para revisión manual, por ejemplo) -- este matcher nunca
# fuerza una recomendación sin fundamento, ver docstring de `match()`.
```
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.application.cv.cv_catalog import CVCatalog
from app.application.cv.cv_profile import CVProfile
from app.application.cv.skill_normalizer import normalize_skill


@dataclass(frozen=True, slots=True)
class CVMatchResult:
    """Result of matching a job's skills against the CV catalog.

    Misma forma que el ejemplo de salida de
    `docs/agents/AGENTS.md` sección 11 (`recommended_cv`, `matching_skills`,
    `missing_skills`, `confidence`).

    - `recommended_cv`: el `id` del CV ganador (p. ej. `"java"`), o `None`
      si ninguna skill del job matchea ninguna skill de ningún CV -- ver
      "Sin ningún match" en el docstring de `CVMatcher.match`. Nunca se
      fuerza una recomendación sin al menos una skill en común.
    - `matching_skills`: texto tal como aparece en `CVProfile.skills` del CV
      recomendado (no el texto del job ni la forma normalizada) -- son las
      skills que el CV puede *demostrar* que tiene, en el orden en que
      aparecen en el catálogo.
    - `missing_skills`: texto tal como aparece en las skills del job (no
      normalizado) que no matchearon ninguna skill del CV recomendado (o,
      si `recommended_cv` es `None`, todas las skills del job).
    - `confidence`: ver fórmula exacta en el docstring de `match()`.
    """

    recommended_cv: str | None
    matching_skills: list[str]
    missing_skills: list[str]
    confidence: float


class CVMatcher:
    """Matches a job's detected skills against the CV catalog."""

    def __init__(self, catalog: CVCatalog) -> None:
        self._catalog = catalog

    def match(self, job_skills: Sequence[str]) -> CVMatchResult:
        """Returns the best-matching CV for `job_skills`, or no match.

        Algoritmo (determinístico, sin LLM):

        1. Normaliza y deduplica `job_skills` con
           `app.application.cv.skill_normalizer.normalize_skill`,
           descartando strings vacíos/solo-espacios. Se conserva el primer
           texto original visto por cada forma normalizada (para reportar
           `missing_skills` con el texto tal como llegó del Job Analyzer).
        2. Si no queda ninguna skill utilizable -- `job_skills` vacío o con
           solo strings vacíos --, devuelve `recommended_cv=None`,
           `confidence=0.0`, sin CVs a comparar: no hay nada que matchear.
        3. Para cada `CVProfile` del catálogo (en el orden devuelto por
           `CVCatalog.list_cvs()`), normaliza también sus `skills` y calcula
           `score = len(job_skills_normalizados & cv_skills_normalizados)`
           -- exactamente `len(set(job_skills) & set(cv_skills))` que pide
           `ROADMAP.md` Fase 4 punto 4, aplicado sobre las formas
           normalizadas en vez de los strings crudos.
        4. Gana el CV con mayor `score`. **Desempate**: si dos o más CVs
           llegan al mismo `score` máximo, gana el que aparece primero en
           `CVCatalog.list_cvs()` (es decir, el primero en `config/cvs.yaml`
           de arriba hacia abajo) -- criterio elegido por ser el más
           predecible para quien edita el YAML a mano, sin depender de un
           orden alfabético de `id` que no tiene ningún significado de
           negocio. Documentado acá porque no hay otro lugar canónico donde
           un caller pueda inferirlo.
        5. **Sin ningún match** (el mejor `score` es 0, incluso si hay CVs
           en el catálogo): devuelve `recommended_cv=None`,
           `matching_skills=[]`, `missing_skills` con todas las skills del
           job, y `confidence=0.0`. Nunca se "fuerza" un CV sin ninguna
           skill en común -- ver `docs/agents/AGENTS.md` sección 11 ("Si no
           hay un match razonable, el resultado debe indicarlo
           explícitamente... en vez de forzar una recomendación"). Un
           `score` de al menos 1 sí produce una recomendación (con
           `confidence` baja si el resto de skills no matchea) porque en
           ese caso hay al menos una skill real en común, no una palabra
           suelta inventada.
        6. `confidence = len(matching) / len(distinct_job_skills)`, sobre el
           conjunto ya deduplicado de skills del job (paso 1) -- así una
           misma skill repetida dos veces por el Analyzer no infla ni
           distorsiona el denominador. Si `distinct_job_skills` está vacío,
           `confidence = 0.0` (paso 2).
        """
        distinct_job: dict[str, str] = {}
        for raw_skill in job_skills:
            if not raw_skill or not raw_skill.strip():
                continue
            key = normalize_skill(raw_skill)
            if not key:
                continue
            distinct_job.setdefault(key, raw_skill)

        if not distinct_job:
            return CVMatchResult(
                recommended_cv=None, matching_skills=[], missing_skills=[], confidence=0.0
            )

        best_profile: CVProfile | None = None
        best_matched_keys: set[str] = set()
        best_score = -1

        for profile in self._catalog.list_cvs():
            cv_normalized_keys = {
                key for raw_cv_skill in profile.skills if (key := normalize_skill(raw_cv_skill))
            }
            matched_keys = distinct_job.keys() & cv_normalized_keys
            score = len(matched_keys)
            if score > best_score:
                best_score = score
                best_profile = profile
                best_matched_keys = matched_keys

        if best_profile is None or best_score == 0:
            return CVMatchResult(
                recommended_cv=None,
                matching_skills=[],
                missing_skills=list(distinct_job.values()),
                confidence=0.0,
            )

        seen_keys: set[str] = set()
        matching_skills: list[str] = []
        for raw_cv_skill in best_profile.skills:
            key = normalize_skill(raw_cv_skill)
            if key in best_matched_keys and key not in seen_keys:
                matching_skills.append(raw_cv_skill)
                seen_keys.add(key)

        missing_skills = [
            original for key, original in distinct_job.items() if key not in best_matched_keys
        ]
        confidence = len(best_matched_keys) / len(distinct_job)

        return CVMatchResult(
            recommended_cv=best_profile.id,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            confidence=confidence,
        )
