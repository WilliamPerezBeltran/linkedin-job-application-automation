from datetime import UTC, datetime

import pytest

from app.domain.entities.job import Job
from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "source": "linkedin_feed",
        "author": "Jane Recruiter",
        "content": "We are hiring a Senior Python Engineer...",
        "content_hash": "a" * 64,
        "email": EmailAddress("jobs@example.com"),
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "published_at": _NOW,
        "scraped_at": _NOW,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Job.create(**defaults)  # type: ignore[arg-type]


class TestJobCreate:
    def test_new_job_starts_scraped(self) -> None:
        job = _make_job()

        assert job.status == JobStatus.SCRAPED

    def test_generates_a_unique_id(self) -> None:
        assert _make_job().id != _make_job().id

    def test_email_is_optional(self) -> None:
        job = _make_job(email=None)

        assert job.email is None

    @pytest.mark.parametrize(
        "field",
        ["source", "author", "content", "content_hash", "url"],
    )
    def test_rejects_empty_required_fields(self, field: str) -> None:
        with pytest.raises(InvalidDomainValueError):
            _make_job(**{field: "   "})


class TestJobStatusTransitionsAreEnforced:
    def test_full_happy_path_pipeline(self) -> None:
        job = _make_job()

        job.mark_analyzed()
        status_after_analyzed: JobStatus = job.status
        assert status_after_analyzed == JobStatus.ANALYZED

        job.mark_relevant()
        status_after_relevant: JobStatus = job.status
        assert status_after_relevant == JobStatus.RELEVANT

        job.mark_cv_selected()
        status_after_cv_selected: JobStatus = job.status
        assert status_after_cv_selected == JobStatus.CV_SELECTED

        job.mark_email_generated()
        status_after_email_generated: JobStatus = job.status
        assert status_after_email_generated == JobStatus.EMAIL_GENERATED

        job.mark_draft_created()
        status_after_draft_created: JobStatus = job.status
        assert status_after_draft_created == JobStatus.DRAFT_CREATED

        job.mark_sent()
        status_after_sent: JobStatus = job.status
        assert status_after_sent == JobStatus.SENT

    def test_not_relevant_branch_is_terminal(self) -> None:
        job = _make_job()
        job.mark_analyzed()

        job.mark_not_relevant()

        assert job.status == JobStatus.NOT_RELEVANT
        with pytest.raises(InvalidStateTransitionError):
            job.mark_relevant()

    def test_cannot_skip_states(self) -> None:
        job = _make_job()

        with pytest.raises(InvalidStateTransitionError) as exc_info:
            job.mark_relevant()

        error = exc_info.value
        assert error.entity_name == "Job"
        assert error.current_status == "SCRAPED"
        assert error.target_status == "RELEVANT"
        assert str(job.id) == error.entity_id

    def test_cannot_transition_out_of_a_terminal_state(self) -> None:
        job = _make_job()
        job.mark_analyzed()
        job.mark_relevant()
        job.mark_cv_selected()
        job.mark_email_generated()
        job.mark_draft_created()
        job.mark_sent()

        with pytest.raises(InvalidStateTransitionError):
            job.mark_sent()


class TestJobReconstruct:
    def test_reconstruct_accepts_a_persisted_status_directly(self) -> None:
        job_id = JobId.new()

        job = Job.reconstruct(
            id=job_id,
            source="linkedin_feed",
            author="Jane Recruiter",
            content="content",
            content_hash="b" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/urn:li:activity:456/",
            published_at=None,
            scraped_at=_NOW,
            status=JobStatus.EMAIL_GENERATED,
            created_at=_NOW,
        )

        assert job.id == job_id
        assert job.status == JobStatus.EMAIL_GENERATED

    def test_reconstructed_job_still_enforces_transitions_going_forward(self) -> None:
        job = Job.reconstruct(
            id=JobId.new(),
            source="linkedin_feed",
            author="Jane Recruiter",
            content="content",
            content_hash="c" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/urn:li:activity:789/",
            published_at=None,
            scraped_at=_NOW,
            status=JobStatus.NOT_RELEVANT,
            created_at=_NOW,
        )

        with pytest.raises(InvalidStateTransitionError):
            job.mark_analyzed()


class TestJobRecordExtractedEmail:
    def test_sets_the_email(self) -> None:
        job = _make_job(email=None)
        extracted = EmailAddress("recruiter@example.com")

        job.record_extracted_email(extracted)

        assert job.email == extracted

    def test_overwrites_a_previously_set_email(self) -> None:
        job = _make_job(email=EmailAddress("old@example.com"))
        new_email = EmailAddress("new@example.com")

        job.record_extracted_email(new_email)

        assert job.email == new_email

    def test_does_not_change_status(self) -> None:
        job = _make_job(email=None)

        job.record_extracted_email(EmailAddress("recruiter@example.com"))

        assert job.status == JobStatus.SCRAPED

    @pytest.mark.parametrize(
        "status",
        [
            JobStatus.SCRAPED,
            JobStatus.ANALYZED,
            JobStatus.RELEVANT,
            JobStatus.NOT_RELEVANT,
            JobStatus.CV_SELECTED,
            JobStatus.EMAIL_GENERATED,
            JobStatus.DRAFT_CREATED,
            JobStatus.SENT,
        ],
    )
    def test_can_be_called_regardless_of_status(self, status: JobStatus) -> None:
        job = Job.reconstruct(
            id=JobId.new(),
            source="linkedin_feed",
            author="Jane Recruiter",
            content="content",
            content_hash="d" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/urn:li:activity:999/",
            published_at=None,
            scraped_at=_NOW,
            status=status,
            created_at=_NOW,
        )
        extracted = EmailAddress("recruiter@example.com")

        job.record_extracted_email(extracted)

        assert job.email == extracted
        assert job.status == status


class TestJobMarkIgnored:
    @pytest.mark.parametrize(
        "status",
        [
            JobStatus.SCRAPED,
            JobStatus.ANALYZED,
            JobStatus.RELEVANT,
            JobStatus.CV_SELECTED,
            JobStatus.EMAIL_GENERATED,
        ],
    )
    def test_marks_ignored_from_an_allowed_review_status(self, status: JobStatus) -> None:
        job = Job.reconstruct(
            id=JobId.new(),
            source="linkedin_feed",
            author="Jane Recruiter",
            content="content",
            content_hash="e" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/urn:li:activity:111/",
            published_at=None,
            scraped_at=_NOW,
            status=status,
            created_at=_NOW,
        )

        job.mark_ignored()

        assert job.status == JobStatus.IGNORED

    @pytest.mark.parametrize(
        "status",
        [
            JobStatus.DRAFT_CREATED,
            JobStatus.SENT,
            JobStatus.NOT_RELEVANT,
            JobStatus.IGNORED,
        ],
    )
    def test_rejects_ignoring_once_a_real_draft_or_send_exists(self, status: JobStatus) -> None:
        job = Job.reconstruct(
            id=JobId.new(),
            source="linkedin_feed",
            author="Jane Recruiter",
            content="content",
            content_hash="f" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/urn:li:activity:222/",
            published_at=None,
            scraped_at=_NOW,
            status=status,
            created_at=_NOW,
        )

        with pytest.raises(InvalidStateTransitionError):
            job.mark_ignored()


class TestJobEquality:
    def test_equal_when_same_id(self) -> None:
        job = _make_job()
        same_job_different_instance = Job.reconstruct(
            id=job.id,
            source=job.source,
            author=job.author,
            content=job.content,
            content_hash=job.content_hash,
            email=job.email,
            url=job.url,
            published_at=job.published_at,
            scraped_at=job.scraped_at,
            status=job.status,
            created_at=job.created_at,
        )

        assert job == same_job_different_instance
        assert hash(job) == hash(same_job_different_instance)

    def test_not_equal_to_an_object_of_a_different_type(self) -> None:
        job = _make_job()

        assert job != "not a Job"
        assert job.__eq__("not a Job") is NotImplemented

    def test_not_equal_when_different_id(self) -> None:
        assert _make_job() != _make_job()
