"""Unit tests for `/api/jobs/**` (`app/presentation/api/routes/jobs.py`).

Mismo patrón que `tests/unit/presentation/api/routes/test_scrape.py`
(`TestClient` de FastAPI), pero usando `app.dependency_overrides` en vez de
`monkeypatch` sobre el módulo -- este router construye sus dependencias vía
`Depends` (`app/presentation/api/dependencies.py`), así que
`dependency_overrides` es el mecanismo idiomático de FastAPI para
inyectar dobles de prueba, nunca Postgres/Anthropic real.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.application.cv.cv_profile import CVProfile
from app.application.cv.matcher import CVMatcher
from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.cv.exceptions import CVCatalogNotFoundError, CVNotFoundError
from app.infrastructure.gmail.exceptions import GmailDraftCreationError
from app.infrastructure.llm.exceptions import LLMProviderError
from app.main import app
from app.presentation.api.dependencies import (
    get_application_repository,
    get_cv_catalog,
    get_cv_matcher,
    get_db_session,
    get_email_draft_repository,
    get_job_analysis_repository,
    get_job_repository,
    get_llm_provider,
)

pytestmark = pytest.mark.unit

client = TestClient(app)

_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes -- mismo patrón que tests/unit/application/use_cases/*.py.
# ---------------------------------------------------------------------------


class FakeJobRepository:
    def __init__(self) -> None:
        self._jobs_by_id: dict[JobId, Job] = {}

    def save(self, job: Job) -> None:
        self._jobs_by_id[job.id] = job

    def get_by_id(self, job_id: JobId) -> Job | None:
        return self._jobs_by_id.get(job_id)

    def get_by_content_hash(self, content_hash: str) -> Job | None:
        for job in self._jobs_by_id.values():
            if job.content_hash == content_hash:
                return job
        return None

    def list_by_status(self, status: JobStatus, *, limit: int = 50, offset: int = 0) -> list[Job]:
        matching = sorted(
            (job for job in self._jobs_by_id.values() if job.status == status),
            key=lambda job: job.created_at,
        )
        return matching[offset : offset + limit]

    def seed(self, job: Job) -> None:
        self._jobs_by_id[job.id] = job


class FakeJobAnalysisRepository:
    def __init__(self) -> None:
        self._by_job_id: dict[JobId, JobAnalysis] = {}

    def save(self, job_analysis: JobAnalysis) -> None:
        self._by_job_id[job_analysis.job_id] = job_analysis

    def get_by_job_id(self, job_id: JobId) -> JobAnalysis | None:
        return self._by_job_id.get(job_id)

    def seed(self, job_analysis: JobAnalysis) -> None:
        self._by_job_id[job_analysis.job_id] = job_analysis


class FakeLLMProvider:
    def __init__(
        self,
        analyze_result: JobAnalysisResult | None = None,
        analyze_error: Exception | None = None,
        email_result: GeneratedEmail | None = None,
        email_error: Exception | None = None,
    ) -> None:
        self._analyze_result = analyze_result
        self._analyze_error = analyze_error
        self._email_result = email_result
        self._email_error = email_error

    def analyze_job(self, content: str) -> JobAnalysisResult:
        if self._analyze_error is not None:
            raise self._analyze_error
        assert self._analyze_result is not None
        return self._analyze_result

    def generate_email(self, context: EmailContext) -> GeneratedEmail:
        if self._email_error is not None:
            raise self._email_error
        assert self._email_result is not None
        return self._email_result


class FakeSession:
    """Stands in for `sqlalchemy.orm.Session` -- only `commit()` is used
    directly by a handler (`analyze_job`'s intermediate checkpoint commit,
    see `app/presentation/api/routes/jobs.py`); everything else goes through
    the already-faked `job_repository`/`job_analysis_repository`, which
    never touch a real `Session`. Overriding `get_db_session` with this fake
    (see `_override_repositories`) keeps these unit tests off real
    PostgreSQL entirely."""

    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


class FakeCVCatalog:
    def __init__(
        self, profiles: list[CVProfile] | None = None, *, broken: bool = False
    ) -> None:
        self._broken = broken
        self._profiles = profiles or [
            CVProfile(
                id="java",
                file="cvs/java/william-java.pdf",
                skills=("Java", "Spring Boot", "Kafka"),
                summary="Java backend engineer.",
            ),
            CVProfile(
                id="python",
                file="cvs/python/william-python.pdf",
                skills=("Python", "FastAPI", "Django"),
                summary="Python backend engineer.",
            ),
        ]

    def list_cvs(self) -> list[CVProfile]:
        if self._broken:
            raise CVCatalogNotFoundError("config/cvs.yaml")
        return list(self._profiles)

    def get_summary(self, cv_id: str) -> str:
        if self._broken:
            raise CVCatalogNotFoundError("config/cvs.yaml")
        for profile in self._profiles:
            if profile.id == cv_id:
                return profile.summary
        raise CVNotFoundError(cv_id)


class FakeApplicationRepository:
    def __init__(self) -> None:
        self._by_id: dict[ApplicationId, Application] = {}

    def save(self, application: Application) -> None:
        self._by_id[application.id] = application

    def get_by_id(self, application_id: ApplicationId) -> Application | None:
        return self._by_id.get(application_id)

    def get_by_job_id(self, job_id: JobId) -> Application | None:
        for application in self._by_id.values():
            if application.job_id == job_id:
                return application
        return None

    def seed(self, application: Application) -> None:
        self._by_id[application.id] = application


class RacingApplicationRepository:
    """Simulates the concurrency race a MEDIUM `code-reviewer` finding
    flagged for `POST /api/jobs/{id}/create-draft` (two genuinely
    simultaneous requests for the same `Job`): both pass
    `CreateGmailDraft.execute_one`'s idempotency check
    (`get_by_job_id(job.id) is None`) before either commits, both call Gmail,
    and the second `save()` loses the race against the real
    `uq_applications_job_id` constraint.

    `get_by_job_id` returns `None` on its first call (mirrors the use case's
    idempotency check finding nothing yet) and `winning_application` on every
    call after that (mirrors a fresh query, after the router's
    `session.rollback()`, now seeing the row the concurrent request already
    committed). `save()` always raises `IntegrityError`, mirroring
    `SQLAlchemyApplicationRepository.save`'s `flush()` hitting the unique
    constraint -- `winning_application=None` models the (extremely unlikely)
    case where, even after rollback, the concurrent transaction still hasn't
    committed.
    """

    def __init__(self, winning_application: Application | None) -> None:
        self._winning_application = winning_application
        self.get_by_job_id_calls = 0
        self.save_calls = 0

    def save(self, application: Application) -> None:
        self.save_calls += 1
        raise IntegrityError(
            "INSERT INTO applications (...) VALUES (...)",
            {},
            Exception(
                'duplicate key value violates unique constraint "uq_applications_job_id"'
            ),
        )

    def get_by_id(self, application_id: ApplicationId) -> Application | None:
        raise NotImplementedError("not exercised by the race test")

    def get_by_job_id(self, job_id: JobId) -> Application | None:
        self.get_by_job_id_calls += 1
        if self.get_by_job_id_calls == 1:
            return None
        return self._winning_application


class FakeEmailDraftRepository:
    """Fake `EmailDraftRepository` -- never touches OAuth/the real Gmail API.

    Mismo patrón que `FakeLLMProvider`: `draft_error`, cuando se setea, se
    lanza en `create_draft` en vez de devolver un `gmail_draft_id`.
    """

    def __init__(
        self, *, gmail_draft_id: str = "draft-123", draft_error: Exception | None = None
    ) -> None:
        self._gmail_draft_id = gmail_draft_id
        self._draft_error = draft_error
        self.calls: list[dict[str, object]] = []

    def create_draft(self, *, to: EmailAddress, subject: str, body: str, cv_path: str) -> str:
        self.calls.append({"to": to, "subject": subject, "body": body, "cv_path": cv_path})
        if self._draft_error is not None:
            raise self._draft_error
        return self._gmail_draft_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    *,
    content: str = "We are hiring a Senior Java Engineer, remote, apply now!",
    content_hash: str = "a" * 64,
    status: JobStatus = JobStatus.SCRAPED,
) -> Job:
    job = Job.create(
        source="linkedin",
        author="Jane Recruiter",
        content=content,
        content_hash=content_hash,
        email=None,
        url=f"https://www.linkedin.com/feed/update/{content_hash}/",
        published_at=_NOW,
        scraped_at=_NOW,
        created_at=_NOW,
    )
    if status is JobStatus.SCRAPED:
        return job
    job.mark_analyzed()
    if status is JobStatus.ANALYZED:
        return job
    if status is JobStatus.NOT_RELEVANT:
        job.mark_not_relevant()
        return job
    job.mark_relevant()
    if status is JobStatus.RELEVANT:
        return job
    job.mark_cv_selected()
    if status is JobStatus.CV_SELECTED:
        return job
    job.mark_email_generated()
    if status is JobStatus.EMAIL_GENERATED:
        return job
    job.mark_draft_created()
    if status is JobStatus.DRAFT_CREATED:
        return job
    job.mark_sent()
    return job


def _make_job_analysis(
    *,
    job_id: JobId,
    skills: tuple[str, ...] = ("Java", "Spring Boot"),
    recommended_cv: str | None = None,
) -> JobAnalysis:
    job_analysis = JobAnalysis(
        job_id=job_id,
        job_type="Java",
        seniority="Senior",
        skills=skills,
        languages=("Java",),
        frameworks=("Spring Boot",),
        cloud=(),
        ai_related=False,
    )
    if recommended_cv is not None:
        job_analysis.record_cv_recommendation(recommended_cv=recommended_cv, match_score=1.0)
    return job_analysis


def _make_analysis_result(**overrides: object) -> JobAnalysisResult:
    defaults: dict[str, object] = {
        "is_job": True,
        "job_type": "Java",
        "seniority": "Senior",
        "skills": ("Java", "Spring Boot", "Kafka"),
        "languages": ("Java",),
        "frameworks": ("Spring Boot",),
        "cloud": (),
        "ai_related": False,
        "email_addresses": (),
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return JobAnalysisResult(**defaults)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _override_repositories(
    job_repository: FakeJobRepository, job_analysis_repository: FakeJobAnalysisRepository
) -> None:
    app.dependency_overrides[get_job_repository] = lambda: job_repository
    app.dependency_overrides[get_job_analysis_repository] = lambda: job_analysis_repository
    # `analyze_job` also depends on `get_db_session` directly (intermediate
    # checkpoint commit, see its docstring) -- overridden here too so these
    # unit tests never open a real PostgreSQL connection.
    app.dependency_overrides[get_db_session] = lambda: FakeSession()


def _override_session(session: FakeSession) -> None:
    app.dependency_overrides[get_db_session] = lambda: session


def _override_llm(llm_provider: FakeLLMProvider) -> None:
    app.dependency_overrides[get_llm_provider] = lambda: llm_provider


def _override_cv(cv_catalog: FakeCVCatalog) -> None:
    app.dependency_overrides[get_cv_catalog] = lambda: cv_catalog
    app.dependency_overrides[get_cv_matcher] = lambda: CVMatcher(cv_catalog)


def _override_gmail(
    application_repository: FakeApplicationRepository,
    email_draft_repository: FakeEmailDraftRepository,
) -> None:
    app.dependency_overrides[get_application_repository] = lambda: application_repository
    app.dependency_overrides[get_email_draft_repository] = lambda: email_draft_repository


# ---------------------------------------------------------------------------
# GET /api/jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_returns_summaries_for_the_requested_status(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        relevant_job = _make_job(content_hash="b" * 64, status=JobStatus.RELEVANT)
        scraped_job = _make_job(content_hash="c" * 64, status=JobStatus.SCRAPED)
        job_repository.seed(relevant_job)
        job_repository.seed(scraped_job)
        analysis_repository.seed(
            _make_job_analysis(job_id=relevant_job.id, skills=("Java",))
        )
        _override_repositories(job_repository, analysis_repository)

        response = client.get("/api/jobs", params={"status": "RELEVANT"})

        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["id"] == str(relevant_job.id)
        assert payload[0]["status"] == "RELEVANT"
        assert payload[0]["job_type"] == "Java"
        assert payload[0]["skills"] == ["Java"]

    def test_missing_status_query_param_is_422(self) -> None:
        response = client.get("/api/jobs")
        assert response.status_code == 422

    def test_invalid_status_value_is_422(self) -> None:
        response = client.get("/api/jobs", params={"status": "NOT_A_STATUS"})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}
# ---------------------------------------------------------------------------


class TestGetJob:
    def test_returns_200_with_matching_and_missing_skills(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="d" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(
            _make_job_analysis(
                job_id=job.id,
                skills=("Java", "Spring Boot", "Docker"),
                recommended_cv="java",
            )
        )
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.get(f"/api/jobs/{job.id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["recommended_cv"] == "java"
        assert sorted(payload["matching_skills"]) == ["Java", "Spring Boot"]
        assert payload["missing_skills"] == ["Docker"]

    def test_returns_404_when_job_does_not_exist(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.get(f"/api/jobs/{JobId.new()}")

        assert response.status_code == 404

    def test_returns_404_for_a_malformed_id(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.get("/api/jobs/not-a-uuid")

        assert response.status_code == 404

    def test_no_analysis_yet_returns_empty_skill_fields(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="e" * 64, status=JobStatus.SCRAPED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.get(f"/api/jobs/{job.id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["job_type"] is None
        assert payload["recommended_cv"] is None
        assert payload["matching_skills"] == []
        assert payload["missing_skills"] == []

    def test_blank_and_whitespace_only_job_skills_are_ignored_in_skill_match(self) -> None:
        """Mismo criterio de `CVMatcher.match` (`app/application/cv/matcher.py`)
        reimplementado acá a propósito -- ver el docstring de
        `_compute_skill_match`: un skill vacío/solo-espacios detectado por el
        LLM nunca debe contarse ni como matching ni como missing."""
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="f" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(
            _make_job_analysis(
                job_id=job.id,
                skills=("Java", "", "   ", "Docker"),
                recommended_cv="java",
            )
        )
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.get(f"/api/jobs/{job.id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["matching_skills"] == ["Java"]
        assert payload["missing_skills"] == ["Docker"]

    def test_recommended_cv_not_in_catalog_returns_empty_skill_fields(self) -> None:
        """`recommended_cv` guardado en `JobAnalysis` ya no existe en
        `config/cvs.yaml` (p. ej. editado/eliminado después de que
        `SelectBestCV` corrió) -- degrada a listas vacías en vez de romper
        el endpoint, ver docstring de `_resolve_skill_match`."""
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="g" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(
            _make_job_analysis(job_id=job.id, recommended_cv="no-longer-in-catalog")
        )
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.get(f"/api/jobs/{job.id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["recommended_cv"] == "no-longer-in-catalog"
        assert payload["matching_skills"] == []
        assert payload["missing_skills"] == []

    def test_broken_cv_catalog_degrades_to_empty_skill_fields_without_failing(self) -> None:
        """`CVInfrastructureError` al cargar el catálogo (p. ej.
        `config/cvs.yaml` temporalmente inaccesible) no debe tirar abajo
        `GET /api/jobs/{id}` -- ver docstring de `_resolve_skill_match`."""
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="h" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis(job_id=job.id, recommended_cv="java"))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog(broken=True))

        response = client.get(f"/api/jobs/{job.id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["recommended_cv"] == "java"
        assert payload["matching_skills"] == []
        assert payload["missing_skills"] == []


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/ignore
# ---------------------------------------------------------------------------


class TestIgnoreJob:
    def test_returns_200_and_marks_ignored(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="f" * 64, status=JobStatus.RELEVANT)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)

        response = client.post(f"/api/jobs/{job.id}/ignore")

        assert response.status_code == 200
        assert response.json()["status"] == "IGNORED"
        stored = job_repository.get_by_id(job.id)
        assert stored is not None
        assert stored.status == JobStatus.IGNORED

    def test_returns_409_when_transition_not_allowed(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="g" * 64, status=JobStatus.DRAFT_CREATED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)

        response = client.post(f"/api/jobs/{job.id}/ignore")

        assert response.status_code == 409

    def test_returns_404_when_job_does_not_exist(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        _override_repositories(job_repository, analysis_repository)

        response = client.post(f"/api/jobs/{JobId.new()}/ignore")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/analyze
# ---------------------------------------------------------------------------


class TestAnalyzeJob:
    def test_relevant_result_chains_cv_selection(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="h" * 64, status=JobStatus.SCRAPED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_llm(
            FakeLLMProvider(
                analyze_result=_make_analysis_result(
                    is_job=True, skills=("Java", "Spring Boot", "Kafka")
                )
            )
        )
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/analyze")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "CV_SELECTED"
        assert payload["recommended_cv"] == "java"
        stored = job_repository.get_by_id(job.id)
        assert stored is not None
        assert stored.status == JobStatus.CV_SELECTED

    def test_not_relevant_result_does_not_chain_cv_selection(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="i" * 64, status=JobStatus.SCRAPED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_llm(FakeLLMProvider(analyze_result=_make_analysis_result(is_job=False)))
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/analyze")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "NOT_RELEVANT"
        assert payload["recommended_cv"] is None
        stored = job_repository.get_by_id(job.id)
        assert stored is not None
        assert stored.status == JobStatus.NOT_RELEVANT

    def test_relevant_but_no_cv_match_leaves_job_relevant(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="j" * 64, status=JobStatus.SCRAPED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_llm(
            FakeLLMProvider(
                analyze_result=_make_analysis_result(is_job=True, skills=("Elixir", "Phoenix"))
            )
        )
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/analyze")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "RELEVANT"
        assert payload["recommended_cv"] is None

    def test_returns_404_when_job_does_not_exist(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        _override_repositories(job_repository, analysis_repository)
        _override_llm(FakeLLMProvider())
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{JobId.new()}/analyze")

        assert response.status_code == 404

    def test_returns_409_when_job_not_scraped(self) -> None:
        # `execute_one` no chequea `status` de antemano (por diseño, ver su
        # docstring): el candidate filter pasa y el LLM se llama igual antes
        # de que `Job.mark_analyzed()` detecte la transición inválida --
        # por eso el `FakeLLMProvider` acá sí necesita un resultado
        # configurado, aunque el resultado final sea 409.
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="k" * 64, status=JobStatus.ANALYZED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_llm(FakeLLMProvider(analyze_result=_make_analysis_result()))
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/analyze")

        assert response.status_code == 409

    def test_returns_502_on_llm_provider_failure(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="l" * 64, status=JobStatus.SCRAPED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_llm(FakeLLMProvider(analyze_error=LLMProviderError("boom")))
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/analyze")

        assert response.status_code == 502
        stored = job_repository.get_by_id(job.id)
        assert stored is not None
        assert stored.status == JobStatus.SCRAPED

    def test_commits_the_checkpoint_before_chaining_cv_selection(self) -> None:
        """Regression test for a HIGH finding from `code-reviewer`: without
        an intermediate `session.commit()` between `AnalyzeJobPost` and the
        chained `SelectBestCV`, a failure in the CV Matcher step would roll
        back the whole request `Session`, discarding the already-successful
        (already paid for) LLM analysis. This unit test only asserts that
        the checkpoint commit is actually invoked exactly once (observable
        with a fake `Session`) -- the actual rollback-safety behavior with a
        real `Session`/PostgreSQL is covered by an integration test (see
        `tests/integration/test_jobs_analyze_checkpoint.py`), since a fake
        repository has no notion of a real transaction to roll back.
        """
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="q" * 64, status=JobStatus.SCRAPED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        session = FakeSession()
        _override_session(session)
        _override_llm(
            FakeLLMProvider(
                analyze_result=_make_analysis_result(
                    is_job=True, skills=("Java", "Spring Boot", "Kafka")
                )
            )
        )
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/analyze")

        assert response.status_code == 200
        assert session.commit_calls == 1


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/generate-email
# ---------------------------------------------------------------------------


class TestGenerateEmail:
    def test_returns_200_and_records_email(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="m" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis(job_id=job.id, recommended_cv="java"))
        _override_repositories(job_repository, analysis_repository)
        _override_llm(
            FakeLLMProvider(
                email_result=GeneratedEmail(
                    subject="Application for Java role", body="Dear team..."
                )
            )
        )
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/generate-email")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "EMAIL_GENERATED"
        assert payload["subject"] == "Application for Java role"
        assert payload["generated_email"] == "Dear team..."

    def test_returns_404_when_job_does_not_exist(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        _override_repositories(job_repository, analysis_repository)
        _override_llm(FakeLLMProvider())
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{JobId.new()}/generate-email")

        assert response.status_code == 404

    def test_returns_409_when_job_not_cv_selected(self) -> None:
        # `execute_one` no chequea `status` de antemano (por diseño, ver su
        # docstring): con `JobAnalysis`/`recommended_cv` ya presentes, el
        # LLM se llama igual antes de que `Job.mark_email_generated()`
        # detecte la transición inválida -- por eso acá se seedea una
        # `JobAnalysis` completa y un `FakeLLMProvider` con resultado.
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="n" * 64, status=JobStatus.RELEVANT)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis(job_id=job.id, recommended_cv="java"))
        _override_repositories(job_repository, analysis_repository)
        _override_llm(
            FakeLLMProvider(email_result=GeneratedEmail(subject="subject", body="body"))
        )
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/generate-email")

        assert response.status_code == 409

    def test_returns_502_on_llm_provider_failure(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="o" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis(job_id=job.id, recommended_cv="java"))
        _override_repositories(job_repository, analysis_repository)
        _override_llm(FakeLLMProvider(email_error=LLMProviderError("boom")))
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/generate-email")

        assert response.status_code == 502
        stored = job_repository.get_by_id(job.id)
        assert stored is not None
        assert stored.status == JobStatus.CV_SELECTED

    def test_returns_500_when_cv_summary_cannot_be_resolved(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="p" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis(job_id=job.id, recommended_cv="ghost"))
        _override_repositories(job_repository, analysis_repository)
        _override_llm(FakeLLMProvider())
        _override_cv(FakeCVCatalog())

        response = client.post(f"/api/jobs/{job.id}/generate-email")

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# PATCH /api/jobs/{id}/email
# ---------------------------------------------------------------------------


def _make_job_analysis_with_email(
    *, job_id: JobId, recommended_cv: str = "java", subject: str = "Original subject"
) -> JobAnalysis:
    job_analysis = _make_job_analysis(job_id=job_id, recommended_cv=recommended_cv)
    job_analysis.record_generated_email(subject=subject, body="Original body.")
    return job_analysis


class TestEditGeneratedEmail:
    def test_returns_200_with_the_edited_subject_and_body(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="r" * 64, status=JobStatus.EMAIL_GENERATED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.patch(
            f"/api/jobs/{job.id}/email",
            json={"subject": "Edited subject", "body": "Edited body."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["subject"] == "Edited subject"
        assert payload["generated_email"] == "Edited body."
        stored = analysis_repository.get_by_job_id(job.id)
        assert stored is not None
        assert stored.subject == "Edited subject"
        assert stored.generated_email == "Edited body."

    def test_returns_404_when_job_does_not_exist(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.patch(
            f"/api/jobs/{JobId.new()}/email",
            json={"subject": "subject", "body": "body"},
        )

        assert response.status_code == 404

    def test_returns_404_when_no_job_analysis_exists(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        # `mark_email_generated` requiere una `JobAnalysis` real en la
        # práctica, pero `FakeJobRepository`/`FakeJobAnalysisRepository` no
        # imponen esa relación entre sí -- se fuerza acá el caso borde de
        # "job sin JobAnalysis asociada" para cubrir la rama 404 dedicada.
        job = _make_job(content_hash="s" * 64, status=JobStatus.EMAIL_GENERATED)
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.patch(
            f"/api/jobs/{job.id}/email",
            json={"subject": "subject", "body": "body"},
        )

        assert response.status_code == 404

    def test_returns_409_when_email_not_generated_yet(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="t" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis(job_id=job.id, recommended_cv="java"))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.patch(
            f"/api/jobs/{job.id}/email",
            json={"subject": "subject", "body": "body"},
        )

        assert response.status_code == 409

    def test_returns_409_when_draft_already_created(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="u" * 64, status=JobStatus.DRAFT_CREATED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.patch(
            f"/api/jobs/{job.id}/email",
            json={"subject": "subject", "body": "body"},
        )

        assert response.status_code == 409

    def test_returns_422_when_subject_is_empty(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="v" * 64, status=JobStatus.EMAIL_GENERATED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.patch(
            f"/api/jobs/{job.id}/email",
            json={"subject": "", "body": "body"},
        )

        assert response.status_code == 422

    def test_returns_422_when_body_is_blank(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="w" * 64, status=JobStatus.EMAIL_GENERATED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        response = client.patch(
            f"/api/jobs/{job.id}/email",
            json={"subject": "subject", "body": "   "},
        )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/create-draft
# ---------------------------------------------------------------------------


class TestCreateDraft:
    def test_returns_200_with_gmail_draft_id_and_url(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        application_repository = FakeApplicationRepository()
        job = _make_job(content_hash="x" * 64, status=JobStatus.EMAIL_GENERATED)
        job.record_extracted_email(EmailAddress("recruiter@example.com"))
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())
        _override_gmail(
            application_repository, FakeEmailDraftRepository(gmail_draft_id="draft-abc")
        )

        response = client.post(f"/api/jobs/{job.id}/create-draft")

        assert response.status_code == 200
        payload = response.json()
        assert payload["gmail_draft_id"] == "draft-abc"
        assert payload["gmail_url"] == "https://mail.google.com/mail/u/0/#drafts/draft-abc"
        assert payload["job_id"] == str(job.id)
        assert payload["email"] == "recruiter@example.com"
        assert payload["status"] == "DRAFT_CREATED"
        assert payload["cv_path"] == "cvs/java/william-java.pdf"

        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.DRAFT_CREATED
        stored_application = application_repository.get_by_job_id(job.id)
        assert stored_application is not None
        assert stored_application.gmail_draft_id == "draft-abc"

    def test_returns_404_when_job_does_not_exist(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())
        _override_gmail(FakeApplicationRepository(), FakeEmailDraftRepository())

        response = client.post(f"/api/jobs/{JobId.new()}/create-draft")

        assert response.status_code == 404

    def test_returns_409_when_job_not_email_generated(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="y" * 64, status=JobStatus.CV_SELECTED)
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis(job_id=job.id, recommended_cv="java"))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())
        _override_gmail(FakeApplicationRepository(), FakeEmailDraftRepository())

        response = client.post(f"/api/jobs/{job.id}/create-draft")

        assert response.status_code == 409

    def test_returns_502_when_gmail_raises(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="z" * 64, status=JobStatus.EMAIL_GENERATED)
        job.record_extracted_email(EmailAddress("recruiter@example.com"))
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())
        _override_gmail(
            FakeApplicationRepository(),
            FakeEmailDraftRepository(
                draft_error=GmailDraftCreationError(
                    "The Gmail API rejected the draft creation request."
                )
            ),
        )

        response = client.post(f"/api/jobs/{job.id}/create-draft")

        assert response.status_code == 502
        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.EMAIL_GENERATED

    def test_returns_500_when_pipeline_state_is_inconsistent(self) -> None:
        # `EMAIL_GENERATED` sin ninguna `JobAnalysis` asociada nunca debería
        # ocurrir en un pipeline sano (ver docstring de `CreateGmailDraft`),
        # pero `FakeJobRepository`/`FakeJobAnalysisRepository` no imponen esa
        # relación entre sí -- mismo criterio ya usado por
        # `TestEditGeneratedEmail.test_returns_404_when_no_job_analysis_exists`
        # para forzar ese caso borde.
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="1" * 64, status=JobStatus.EMAIL_GENERATED)
        job.record_extracted_email(EmailAddress("recruiter@example.com"))
        job_repository.seed(job)
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())
        _override_gmail(FakeApplicationRepository(), FakeEmailDraftRepository())

        response = client.post(f"/api/jobs/{job.id}/create-draft")

        assert response.status_code == 500

    def test_returns_500_when_cv_catalog_is_broken(self) -> None:
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="2" * 64, status=JobStatus.EMAIL_GENERATED)
        job.record_extracted_email(EmailAddress("recruiter@example.com"))
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog(broken=True))
        _override_gmail(FakeApplicationRepository(), FakeEmailDraftRepository())

        response = client.post(f"/api/jobs/{job.id}/create-draft")

        assert response.status_code == 500
        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.EMAIL_GENERATED

    def test_returns_the_winning_application_when_a_concurrent_request_wins_the_race(
        self,
    ) -> None:
        """Two concurrent `create-draft` requests for the same `Job`: this
        one loses the `uq_applications_job_id` race (`RacingApplicationRepository`
        raises `IntegrityError` on `save()`), but the winning `Application` is
        already visible after the router's `session.rollback()` + re-query --
        the loser must see a 200 with that `Application`, never an unhandled
        500 (the MEDIUM finding this test guards against)."""
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="3" * 64, status=JobStatus.EMAIL_GENERATED)
        job.record_extracted_email(EmailAddress("recruiter@example.com"))
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        winning_application = Application.create(
            job_id=job.id,
            email=EmailAddress("recruiter@example.com"),
            subject="Original subject",
            body="Original body.",
            cv_path="cvs/java/william-java.pdf",
            gmail_draft_id="draft-from-the-winning-request",
        )
        application_repository = RacingApplicationRepository(winning_application)
        fake_session = FakeSession()
        _override_gmail(application_repository, FakeEmailDraftRepository())  # type: ignore[arg-type]
        _override_session(fake_session)

        response = client.post(f"/api/jobs/{job.id}/create-draft")

        assert response.status_code == 200
        payload = response.json()
        assert payload["gmail_draft_id"] == "draft-from-the-winning-request"
        assert application_repository.save_calls == 1
        assert application_repository.get_by_job_id_calls == 2
        assert fake_session.rollback_calls == 1

        # `job.mark_draft_created()`/`job_repository.save(job)` in the use
        # case only run *after* `application_repository.save()` succeeds --
        # the loser's own `Job` was never transitioned, only its Application
        # write collided.
        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.EMAIL_GENERATED

    def test_returns_409_when_the_race_is_still_unresolved_after_rollback(self) -> None:
        """Extremely unlikely edge case (documented in the router's
        docstring): even after `session.rollback()`, the concurrent
        transaction still hasn't committed its `Application` row. No retry
        loop (YAGNI) -- the client gets an explicit `409 Conflict` asking it
        to retry, never an unhandled 500."""
        job_repository = FakeJobRepository()
        analysis_repository = FakeJobAnalysisRepository()
        job = _make_job(content_hash="4" * 64, status=JobStatus.EMAIL_GENERATED)
        job.record_extracted_email(EmailAddress("recruiter@example.com"))
        job_repository.seed(job)
        analysis_repository.seed(_make_job_analysis_with_email(job_id=job.id))
        _override_repositories(job_repository, analysis_repository)
        _override_cv(FakeCVCatalog())

        application_repository = RacingApplicationRepository(None)
        fake_session = FakeSession()
        _override_gmail(application_repository, FakeEmailDraftRepository())  # type: ignore[arg-type]
        _override_session(fake_session)

        response = client.post(f"/api/jobs/{job.id}/create-draft")

        assert response.status_code == 409
        assert fake_session.rollback_calls == 1
