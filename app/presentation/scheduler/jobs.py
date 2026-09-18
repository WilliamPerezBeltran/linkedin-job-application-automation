"""Daily scheduled pipeline (Fase 9, `ROADMAP.md`): another entry point,
driven by time instead of HTTP (`docs/agents/AGENTS.md` sección 8).

Invoca en secuencia los mismos use cases ya existentes que el dashboard
manual usa (Fases 2-5):

```
CollectFeedPosts -> AnalyzeJobPost -> SelectBestCV -> GenerateApplicationEmail
```

dejando cada `Job` que llegue hasta el final en `EMAIL_GENERATED`, listo
para revisión humana. **Nunca** crea drafts de Gmail ni marca nada `SENT`
-- eso sigue siendo 100% manual, disparado por el usuario desde el
frontend (`POST /api/jobs/{id}/create-draft`, Fase 7).

Dos capas, deliberadamente separadas (mismo criterio de testabilidad que el
resto del proyecto -- nunca acoplar lógica de negocio a un framework):

1. `run_daily_pipeline` -- función pura de orquestación. Recibe sus
   dependencias ya construidas (`feed_collector`, `llm_provider`,
   `cv_matcher`, `cv_catalog`, y una fábrica de unidades de trabajo,
   `new_job_pipeline_session`, ver más abajo) y no importa nada de
   APScheduler ni de `app.infrastructure.database.session` por nombre --
   la fábrica que le pasan puede ser un `session_scope()` real o un
   fake in-memory de test. Es lo que ejercitan los unit tests de este
   módulo.
2. `run_scheduler`/`_run_daily_pipeline_job` -- capa fina de APScheduler
   que arma las dependencias de producción reales (`LinkedInFeedCollector`,
   `AnthropicProvider`, `FilesystemCVRepository`+`CVMatcher`,
   `session_scope()`) y llama a `run_daily_pipeline` con ellas. No decide
   ninguna lógica de negocio -- solo construye e invoca.

Particionamiento de transacciones (decisión de diseño, `backend-engineer`)
-------------------------------------------------------------------------
Cada etapa (collect/persist, analyze, select-cv, generate-email) corre en
su **propia** `Session`/transacción, con su propio commit -- en vez de una
única transacción gigante para las cuatro etapas, y también en vez de una
sola `Session` compartida con commits intermedios explícitos (el patrón que
sí usa `POST /api/jobs/{id}/analyze`, `app/presentation/api/routes/jobs.py`,
para encadenar dos pasos dentro de un único request HTTP). Motivo:

- Aislamiento total entre etapas: una `Session` que ya hizo `commit()` y
  `close()` no puede verse afectada por nada que le pase a una `Session`
  completamente distinta abierta después para la siguiente etapa. Con un
  único `session.commit()` intermedio sobre la misma `Session` (como hace
  `analyze_job`) el aislamiento es el mismo en la práctica, pero acá hay
  cuatro etapas en vez de dos, corriendo desatendidas una vez al día sin
  nadie mirando -- preferimos el diseño que hace ese aislamiento estructural
  (imposible de romper por accidente si alguien agrega una quinta etapa)
  en vez de depender de acordarse de poner el `commit()` en el lugar
  correcto.
- Cada `JobRepository`/`JobAnalysisRepository` que devuelve
  `JobRepository.list_by_status(...)` ya son entidades de dominio
  desacopladas de la `Session` que las originó (no son objetos ORM vivos),
  así que no hay ningún costo de "reconectar" objetos entre `Session`s
  distintas de una etapa a la siguiente -- cada etapa relee del estado ya
  committeado por la etapa anterior.
- Consecuencia directa (el requisito no negociable de esta fase): un fallo
  en una etapa posterior nunca puede revertir el trabajo ya persistido de
  una etapa anterior, porque para cuando la etapa posterior siquiera
  empieza a ejecutar, la `Session` de la etapa anterior ya hizo su commit y
  se cerró.

`new_job_pipeline_session: Callable[[], AbstractContextManager[JobPipelineRepositories]]`
es la única abstracción que `run_daily_pipeline` necesita para lograr esto
sin importar `session_scope()` por nombre: al entrar, entrega un
`(JobRepository, JobAnalysisRepository)` ya construidos sobre la misma
unidad de trabajo; al salir sin excepción, confirma esa unidad de trabajo
(commit real en producción, no-op en los fakes in-memory de test). Se
invoca una vez por etapa (cuatro veces por corrida completa) -- ver
`_sql_job_pipeline_session` más abajo para la implementación real, y
`tests/unit/presentation/scheduler/test_jobs.py` para el fake usado en
tests.

Aislamiento de errores por etapa (gap real detectado en los use cases
existentes, reportado -- no reimplementado acá)
----------------------------------------------------------------------
`AnalyzeJobPost.execute()`, `SelectBestCV.execute()` y
`GenerateApplicationEmail.execute()` ya atrapan, cada uno, los fallos
transitorios de infraestructura que su propia etapa puede producir por
job (`LLMProviderError`/`LLMResponseValidationError`,
`CVInfrastructureError` en el caso de `GenerateApplicationEmail`) para no
abortar el resto del batch -- pero **ninguno de los tres** atrapa
`InvalidStateTransitionError` dentro de su propio loop de batch, y
`SelectBestCV.execute()` tampoco atrapa `CVInfrastructureError` que
`CVMatcher.match()` puede propagar si `config/cvs.yaml` falla al cargarse
(a diferencia de `GenerateApplicationEmail.execute()`, que sí lo hace para
su propio uso de `CVCatalog.get_summary()`). En un pipeline manejado solo
por el dashboard esto es improbable (`execute_one` es lo único que corre
dentro de un request HTTP, nunca `execute()` en batch); pero en un
scheduler no atendido, corriendo en paralelo a un usuario operando el
dashboard manualmente, una carrera real es posible: el scheduler lista un
`Job` en `SCRAPED` y, antes de llamar `execute_one` sobre él, el usuario ya
lo movió manualmente a otro estado (o `config/cvs.yaml` está temporalmente
inaccesible) -- eso propagaría la excepción a través de todo el `for job in
jobs:` del use case, abortando el resto del batch de esa etapa para toda la
corrida.

Esto **no se corrige acá** (violaría "no reimplementes/dupliques lógica de
los use cases" -- ver el prompt de esta tarea): se reporta como deuda
técnica (ver el reporte final de esta tarea) y, mientras tanto, cada etapa
de `run_daily_pipeline` atrapa `(InvalidStateTransitionError,
CVInfrastructureError)` alrededor de la llamada a `execute()` de esa etapa
-- no alrededor de cada `Job` individual, que sigue siendo responsabilidad
exclusiva del use case -- para que un fallo de este tipo interrumpa como
mucho el resto *de esa etapa* en esa corrida, sin abortar las etapas
restantes ni perder el trabajo ya comprometido de las **etapas anteriores**
(cada una ya en su propia `Session`, ya committeada y cerrada -- ver la
sección anterior).

**Límite real de este mitigante, precisado tras una segunda revisión
(hallazgo MEDIUM de `code-reviewer`):** la mitigación de arriba opera a
nivel de *etapa completa*, no a nivel de *job individual dentro de esa
etapa*. Si `execute(limit=...)` ya procesó y guardó (vía `save()`, sin
commit todavío -- una sola `Session` por etapa) 30 de 50 jobs con éxito y
el job #31 dispara una de las dos excepciones de `_STAGE_LEVEL_ERRORS` sin
que el use case la atrape internamente, el `with new_job_pipeline_session()`
de esa etapa sale por excepción y `session_scope()` hace `rollback()` sobre
**toda** su `Session` -- incluyendo esos 30 jobs ya procesados con éxito
*en esa misma llamada*, que vuelven a su estado anterior (`SCRAPED`/
`RELEVANT`/`CV_SELECTED`) para la corrida del día siguiente, con el costo
de LLM ya pagado y descartado. Esto es distinto -- y estrictamente menos
grave -- que perder trabajo de una etapa *anterior* ya committeada (lo que
el requisito no negociable de esta fase prohíbe, y que este diseño sí
evita), pero es una limitación real del "aislamiento por etapa" tal como
está hoy, no cubierta por los tests actuales (todos usan un único job por
escenario de fallo). Reportado como deuda técnica adicional junto con el
gap de los use cases de arriba -- corregirlo de raíz requeriría que
`AnalyzeJobPost`/`SelectBestCV`/`GenerateApplicationEmail` atrapen
`InvalidStateTransitionError` (y `SelectBestCV` también
`CVInfrastructureError`) por-job dentro de su propio loop, igual que ya
hacen con los fallos de LLM -- ese es el fix correcto, en esos tres
archivos, no acá.

Logging
-------
Nunca se loguea contenido de posts/emails ni secretos -- solo contadores,
`job_id` cuando aplica, y `type(exc).__name__` para fallos, mismo criterio
que el resto del pipeline (`docs/agents/AGENTS.md` sección 24). Un resumen
estructurado de la corrida completa se emite con `logger.info` al final de
`run_daily_pipeline` (ROADMAP Fase 9, punto 2/3) -- "log a archivo" se
resuelve corriendo el proceso del scheduler con redirección de
stdout/stderr a un archivo (p. ej. `python -m app.presentation.scheduler.jobs
>> scheduler.log 2>&1`, o el manejo de logs que ya use el entorno de
despliegue), sin forzar una integración de logging-a-archivo dedicada que
no existe hoy en el proyecto (YAGNI, `docs/agents/AGENTS.md` principio 9).
No se implementa ningún envío de email de notificación real -- el ROADMAP
lo marca explícito como opcional, y no hay infraestructura de envío de
email lista (Gmail solo crea drafts, nunca envía, por diseño).

Arranque de los dos procesos (ver README/`docs/architecture/` para más
detalle):

- API + dashboard: `uvicorn app.main:app` (sin cambios, Fase 6/7/8).
- Scheduler: `python -m app.presentation.scheduler.jobs`, proceso propio,
  nunca dentro del proceso de `uvicorn` -- corre `run_scheduler()`
  (`BlockingScheduler`), que dispara `_run_daily_pipeline_job` todos los
  días a la hora configurada por `SCHEDULER_DAILY_HOUR` (default `8`,
  ROADMAP "08:00").
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.exc import IntegrityError

from app.application.cv.cv_catalog import CVCatalog
from app.application.cv.cv_catalog_errors import CVInfrastructureError
from app.application.cv.matcher import CVMatcher
from app.application.interfaces.feed_collector import FeedCollector
from app.application.interfaces.llm_provider import LLMProvider
from app.application.use_cases.analyze_job_post import AnalyzeJobPost, AnalyzeJobPostResult
from app.application.use_cases.collect_feed_posts import CollectFeedPosts, CollectFeedPostsResult
from app.application.use_cases.generate_application_email import (
    GenerateApplicationEmail,
    GenerateApplicationEmailResult,
)
from app.application.use_cases.select_best_cv import SelectBestCV, SelectBestCVResult
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.infrastructure.cv.filesystem_cv_repository import FilesystemCVRepository
from app.infrastructure.database.repositories.sqlalchemy_job_analysis_repository import (
    SQLAlchemyJobAnalysisRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import session_scope
from app.infrastructure.linkedin.exceptions import LinkedInInfrastructureError
from app.infrastructure.linkedin.linkedin_feed_collector import LinkedInFeedCollector
from app.infrastructure.llm.anthropic_provider import AnthropicProvider

logger = logging.getLogger(__name__)

_DEFAULT_DAILY_HOUR = 8
_DEFAULT_BATCH_LIMIT = 50

JobPipelineRepositories = tuple[JobRepository, JobAnalysisRepository]
"""A `(JobRepository, JobAnalysisRepository)` pair bound to a single unit of work."""

JobPipelineSessionFactory = Callable[[], AbstractContextManager[JobPipelineRepositories]]
"""Opens one unit of work for a single pipeline stage.

Committing (or not) on `__exit__` is entirely up to the factory -- see the
module docstring, section "Particionamiento de transacciones".
"""

# Errores de infraestructura/dominio que una etapa (`execute()` en batch)
# puede propagar sin haberlos atrapado internamente por job -- ver el
# docstring del módulo, sección "Aislamiento de errores por etapa", para el
# gap concreto detectado en los tres use cases y por qué se atrapa acá en
# vez de en ellos.
_STAGE_LEVEL_ERRORS: tuple[type[Exception], ...] = (
    InvalidStateTransitionError,
    CVInfrastructureError,
)


@dataclass(frozen=True, slots=True)
class DailyPipelineResult:
    """Summarizes a single `run_daily_pipeline` run, one field pair per stage.

    `*_result` es `None` si la etapa nunca llegó a correr (p. ej.
    `collect_result` es `None` si `feed_collector.collect()` falló antes de
    persistir nada). `*_error` lleva `type(exc).__name__` -- nunca el
    contenido de la excepción, que podría incluir fragmentos de la
    respuesta HTTP/LLM cruda -- si esa etapa fue interrumpida por un error
    no manejado internamente por el use case (ver `_STAGE_LEVEL_ERRORS` /
    `LinkedInInfrastructureError` para collect). `None` significa que la
    etapa corrió sin ningún fallo a nivel de etapa (los fallos por-job ya
    vienen contados dentro de cada `*Result`).
    """

    collect_result: CollectFeedPostsResult | None
    collect_error: str | None
    analyze_result: AnalyzeJobPostResult | None
    analyze_error: str | None
    select_cv_result: SelectBestCVResult | None
    select_cv_error: str | None
    generate_email_result: GenerateApplicationEmailResult | None
    generate_email_error: str | None
    duration_seconds: float


def run_daily_pipeline(
    *,
    feed_collector: FeedCollector,
    new_job_pipeline_session: JobPipelineSessionFactory,
    llm_provider: LLMProvider,
    cv_matcher: CVMatcher,
    cv_catalog: CVCatalog,
    analyze_limit: int = _DEFAULT_BATCH_LIMIT,
    select_cv_limit: int = _DEFAULT_BATCH_LIMIT,
    generate_email_limit: int = _DEFAULT_BATCH_LIMIT,
) -> DailyPipelineResult:
    """Runs `CollectFeedPosts -> AnalyzeJobPost -> SelectBestCV ->
    GenerateApplicationEmail` once, each stage in its own unit of work.

    Función pura de orquestación -- no importa APScheduler ni
    `session_scope()` por nombre, ver el docstring del módulo. Nunca crea
    drafts de Gmail ni marca nada `SENT`.
    """
    started_at = time.monotonic()

    collect_result, collect_error = _run_collect_stage(
        feed_collector=feed_collector, new_job_pipeline_session=new_job_pipeline_session
    )
    analyze_result, analyze_error = _run_analyze_stage(
        new_job_pipeline_session=new_job_pipeline_session,
        llm_provider=llm_provider,
        limit=analyze_limit,
    )
    select_cv_result, select_cv_error = _run_select_cv_stage(
        new_job_pipeline_session=new_job_pipeline_session,
        cv_matcher=cv_matcher,
        limit=select_cv_limit,
    )
    generate_email_result, generate_email_error = _run_generate_email_stage(
        new_job_pipeline_session=new_job_pipeline_session,
        cv_catalog=cv_catalog,
        llm_provider=llm_provider,
        limit=generate_email_limit,
    )

    duration_seconds = time.monotonic() - started_at

    result = DailyPipelineResult(
        collect_result=collect_result,
        collect_error=collect_error,
        analyze_result=analyze_result,
        analyze_error=analyze_error,
        select_cv_result=select_cv_result,
        select_cv_error=select_cv_error,
        generate_email_result=generate_email_result,
        generate_email_error=generate_email_error,
        duration_seconds=duration_seconds,
    )
    _log_summary(result)
    return result


def _run_collect_stage(
    *, feed_collector: FeedCollector, new_job_pipeline_session: JobPipelineSessionFactory
) -> tuple[CollectFeedPostsResult | None, str | None]:
    """Runs the collect+persist stage.

    `feed_collector.collect()` (Playwright) corre deliberadamente **fuera**
    de cualquier unidad de trabajo -- mismo límite transaccional que
    documenta `CollectFeedPosts` y que ya respeta `POST /scrape`
    (`app/presentation/api/routes/scrape.py`): nunca mantener una
    transacción de PostgreSQL abierta mientras Playwright navega el feed.

    El paso de persistencia (`CollectFeedPosts.persist`) también se atrapa
    explícitamente (hallazgo HIGH de `code-reviewer` sobre esta misma fase,
    corregido acá): `persist()` solo atrapa `InvalidDomainValueError`
    internamente, así que un `IntegrityError` de SQLAlchemy -- dos
    invocaciones concurrentes (este scheduler y, p. ej., un `POST /scrape`
    manual desde el dashboard) intentando persistir el mismo `content_hash`
    en la ventana entre el chequeo de dedup y el `save()`, mismo caso ya
    documentado en `app/presentation/api/routes/scrape.py` -- se propagaría
    sin atrapar y abortaría `run_daily_pipeline` completo antes de que
    analyze/select-cv/generate-email lleguen siquiera a correr sobre el
    backlog ya existente. Tratado igual que el resto de las etapas: se
    cuenta como fallo de esta etapa (`collect_error`), sin abortar las
    etapas siguientes.
    """
    try:
        raw_posts = feed_collector.collect()
    except LinkedInInfrastructureError as exc:
        logger.warning("scheduler.collect_stage_failed error_type=%s", type(exc).__name__)
        return None, type(exc).__name__

    try:
        with new_job_pipeline_session() as (job_repository, _job_analysis_repository):
            use_case = CollectFeedPosts(
                feed_collector=feed_collector, job_repository=job_repository
            )
            result = use_case.persist(raw_posts)
    except IntegrityError as exc:
        logger.error("scheduler.collect_stage_persist_failed error_type=%s", type(exc).__name__)
        return None, type(exc).__name__
    return result, None


def _run_analyze_stage(
    *,
    new_job_pipeline_session: JobPipelineSessionFactory,
    llm_provider: LLMProvider,
    limit: int,
) -> tuple[AnalyzeJobPostResult | None, str | None]:
    try:
        with new_job_pipeline_session() as (job_repository, job_analysis_repository):
            use_case = AnalyzeJobPost(
                job_repository=job_repository,
                job_analysis_repository=job_analysis_repository,
                llm_provider=llm_provider,
            )
            result = use_case.execute(limit=limit)
    except _STAGE_LEVEL_ERRORS as exc:
        logger.error("scheduler.analyze_stage_failed error_type=%s", type(exc).__name__)
        return None, type(exc).__name__
    return result, None


def _run_select_cv_stage(
    *,
    new_job_pipeline_session: JobPipelineSessionFactory,
    cv_matcher: CVMatcher,
    limit: int,
) -> tuple[SelectBestCVResult | None, str | None]:
    try:
        with new_job_pipeline_session() as (job_repository, job_analysis_repository):
            use_case = SelectBestCV(
                job_repository=job_repository,
                job_analysis_repository=job_analysis_repository,
                cv_matcher=cv_matcher,
            )
            result = use_case.execute(limit=limit)
    except _STAGE_LEVEL_ERRORS as exc:
        logger.error("scheduler.select_cv_stage_failed error_type=%s", type(exc).__name__)
        return None, type(exc).__name__
    return result, None


def _run_generate_email_stage(
    *,
    new_job_pipeline_session: JobPipelineSessionFactory,
    cv_catalog: CVCatalog,
    llm_provider: LLMProvider,
    limit: int,
) -> tuple[GenerateApplicationEmailResult | None, str | None]:
    try:
        with new_job_pipeline_session() as (job_repository, job_analysis_repository):
            use_case = GenerateApplicationEmail(
                job_repository=job_repository,
                job_analysis_repository=job_analysis_repository,
                cv_catalog=cv_catalog,
                llm_provider=llm_provider,
            )
            result = use_case.execute(limit=limit)
    except _STAGE_LEVEL_ERRORS as exc:
        logger.error("scheduler.generate_email_stage_failed error_type=%s", type(exc).__name__)
        return None, type(exc).__name__
    return result, None


def _log_summary(result: DailyPipelineResult) -> None:
    """Structured, secret-free summary of a full pipeline run.

    Nunca contenido de posts/emails -- solo contadores, `error_type` por
    etapa, y duración total (`docs/agents/AGENTS.md` sección 24).
    """
    logger.info(
        "scheduler.daily_pipeline_finished "
        "collect_saved=%s collect_skipped_duplicates=%s collect_skipped_invalid=%s "
        "collect_error=%s "
        "analyzed=%s relevant=%s not_relevant=%s skipped_by_filter=%s "
        "analyze_failed_llm_calls=%s analyze_error=%s "
        "cv_selected=%s cv_no_match=%s cv_skipped_missing_analysis=%s select_cv_error=%s "
        "emails_generated=%s email_failed_llm_calls=%s "
        "email_skipped_missing_analysis=%s email_skipped_missing_cv_summary=%s "
        "generate_email_error=%s duration_seconds=%.2f",
        result.collect_result.saved if result.collect_result else None,
        result.collect_result.skipped_duplicates if result.collect_result else None,
        result.collect_result.skipped_invalid if result.collect_result else None,
        result.collect_error,
        result.analyze_result.analyzed if result.analyze_result else None,
        result.analyze_result.relevant if result.analyze_result else None,
        result.analyze_result.not_relevant if result.analyze_result else None,
        result.analyze_result.skipped_by_filter if result.analyze_result else None,
        result.analyze_result.failed_llm_calls if result.analyze_result else None,
        result.analyze_error,
        result.select_cv_result.cv_selected if result.select_cv_result else None,
        result.select_cv_result.no_match if result.select_cv_result else None,
        result.select_cv_result.skipped_missing_analysis if result.select_cv_result else None,
        result.select_cv_error,
        result.generate_email_result.generated if result.generate_email_result else None,
        result.generate_email_result.failed_llm_calls if result.generate_email_result else None,
        result.generate_email_result.skipped_missing_analysis
        if result.generate_email_result
        else None,
        result.generate_email_result.skipped_missing_cv_summary
        if result.generate_email_result
        else None,
        result.generate_email_error,
        result.duration_seconds,
    )


# ---------------------------------------------------------------------------
# Capa fina de APScheduler -- composition root del proceso del scheduler.
# Nada de lo que sigue contiene lógica de negocio: solo construye las
# dependencias de producción reales e invoca `run_daily_pipeline`.
# ---------------------------------------------------------------------------


@contextmanager
def _sql_job_pipeline_session() -> Iterator[JobPipelineRepositories]:
    """Production `JobPipelineSessionFactory`: one real `Session` per stage.

    Envuelve `session_scope()` (`app/infrastructure/database/session.py`):
    commit al salir sin excepción, rollback si algo dentro del `with`
    propaga -- ver el docstring del módulo para por qué cada etapa usa una
    `Session` propia en vez de una compartida con commits intermedios.
    """
    with session_scope() as session:
        yield SQLAlchemyJobRepository(session), SQLAlchemyJobAnalysisRepository(session)


def _run_daily_pipeline_job() -> None:
    """APScheduler job callable: builds real dependencies and delegates.

    Construidas directamente (no vía `app.presentation.api.dependencies`):
    esas funciones `get_*` son dependencias de FastAPI (`Depends(...)` como
    default de argumento) pensadas para resolverse dentro del ciclo de vida
    de un request -- llamarlas fuera de ese contexto (p. ej.
    `get_cv_matcher()` sin argumentos) usaría el objeto `Depends(...)` sin
    resolver como si fuera el valor real. `AnthropicProvider()` y
    `FilesystemCVRepository()` no reciben argumentos en ninguno de los dos
    casos, así que construirlas acá directamente es equivalente y evita ese
    riesgo -- mismo patrón que `POST /scrape`
    (`app/presentation/api/routes/scrape.py`) ya usa para `LinkedInFeedCollector()`.
    """
    feed_collector = LinkedInFeedCollector()
    llm_provider: LLMProvider = AnthropicProvider()
    cv_catalog: CVCatalog = FilesystemCVRepository()
    cv_matcher = CVMatcher(cv_catalog)

    run_daily_pipeline(
        feed_collector=feed_collector,
        new_job_pipeline_session=_sql_job_pipeline_session,
        llm_provider=llm_provider,
        cv_matcher=cv_matcher,
        cv_catalog=cv_catalog,
    )


def _get_daily_hour() -> int:
    """Reads `SCHEDULER_DAILY_HOUR` from the environment (default `8`).

    Falla rápido con un `ValueError` explícito si la variable está seteada
    pero no es un entero válido en `[0, 23]` -- un error de configuración de
    arranque debe frenar `run_scheduler()` antes de `scheduler.start()`, no
    dejar pasar silenciosamente una hora sin sentido a `CronTrigger`.
    """
    raw_hour = os.environ.get("SCHEDULER_DAILY_HOUR")
    if raw_hour is None or not raw_hour.strip():
        return _DEFAULT_DAILY_HOUR

    try:
        hour = int(raw_hour)
    except ValueError as exc:
        raise ValueError(
            f"SCHEDULER_DAILY_HOUR must be an integer in [0, 23], got {raw_hour!r}."
        ) from exc
    if not 0 <= hour <= 23:
        raise ValueError(f"SCHEDULER_DAILY_HOUR must be an integer in [0, 23], got {hour}.")
    return hour


def run_scheduler() -> None:
    """Starts the scheduler as a `BlockingScheduler`, its own process.

    Entry point de producción: `python -m app.presentation.scheduler.jobs`
    (ver `if __name__ == "__main__":` al final del módulo). Nunca se corre
    dentro del proceso de `uvicorn app.main:app` -- son dos responsabilidades
    distintas (API+dashboard vs. pipeline por tiempo), cada una su propio
    proceso, arrancados por separado. `BlockingScheduler` (no
    `BackgroundScheduler`): este módulo no tiene ningún otro trabajo que
    hacer en el mismo proceso una vez que el scheduler arrancó.
    """
    daily_hour = _get_daily_hour()
    scheduler = BlockingScheduler()
    scheduler.add_job(
        _run_daily_pipeline_job,
        trigger=CronTrigger(hour=daily_hour, minute=0),
        id="daily_pipeline",
        name="Daily job application pipeline",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    logger.info("scheduler.starting daily_hour=%s", daily_hour)
    scheduler.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_scheduler()
