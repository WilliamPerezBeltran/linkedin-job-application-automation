from datetime import UTC, datetime

import pytest

from app.domain.entities.application import Application
from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _make_application(**overrides: object) -> Application:
    defaults: dict[str, object] = {
        "job_id": JobId.new(),
        "email": EmailAddress("jobs@example.com"),
        "subject": "Application for Senior Python Engineer",
        "body": "Dear hiring manager...",
        "cv_path": "cvs/python/william-python.pdf",
        "gmail_draft_id": "draft-123",
    }
    defaults.update(overrides)
    return Application.create(**defaults)  # type: ignore[arg-type]


class TestApplicationCreate:
    def test_new_application_starts_draft_created(self) -> None:
        application = _make_application()

        assert application.status == ApplicationStatus.DRAFT_CREATED
        assert application.sent_at is None

    @pytest.mark.parametrize("field", ["subject", "body"])
    def test_rejects_empty_required_fields(self, field: str) -> None:
        with pytest.raises(InvalidDomainValueError):
            _make_application(**{field: "   "})

    @pytest.mark.parametrize(
        "cv_path",
        ["", "  ", "cvs/../../etc/passwd", "../secrets.pdf"],
    )
    def test_rejects_invalid_cv_paths(self, cv_path: str) -> None:
        with pytest.raises(InvalidDomainValueError):
            _make_application(cv_path=cv_path)

    def test_gmail_draft_id_is_optional(self) -> None:
        application = _make_application(gmail_draft_id=None)

        assert application.gmail_draft_id is None


class TestApplicationMarkSent:
    def test_transitions_to_sent_and_records_timestamp(self) -> None:
        application = _make_application()

        application.mark_sent(sent_at=_NOW)

        assert application.status == ApplicationStatus.SENT
        assert application.sent_at == _NOW

    def test_cannot_mark_sent_twice(self) -> None:
        application = _make_application()
        application.mark_sent(sent_at=_NOW)

        with pytest.raises(InvalidStateTransitionError) as exc_info:
            application.mark_sent(sent_at=_NOW)

        error = exc_info.value
        assert error.entity_name == "Application"
        assert error.current_status == "SENT"
        assert error.target_status == "SENT"


class TestApplicationReconstructInvariants:
    def test_reconstruct_accepts_a_consistent_sent_state(self) -> None:
        application = Application.reconstruct(
            id=ApplicationId.new(),
            job_id=JobId.new(),
            email=EmailAddress("jobs@example.com"),
            subject="Subject",
            body="Body",
            cv_path="cvs/python/cv.pdf",
            gmail_draft_id="draft-1",
            status=ApplicationStatus.SENT,
            sent_at=_NOW,
        )

        assert application.status == ApplicationStatus.SENT

    def test_reconstruct_rejects_sent_status_without_sent_at(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            Application.reconstruct(
                id=ApplicationId.new(),
                job_id=JobId.new(),
                email=EmailAddress("jobs@example.com"),
                subject="Subject",
                body="Body",
                cv_path="cvs/python/cv.pdf",
                gmail_draft_id="draft-1",
                status=ApplicationStatus.SENT,
                sent_at=None,
            )

    def test_reconstruct_rejects_draft_created_status_with_sent_at(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            Application.reconstruct(
                id=ApplicationId.new(),
                job_id=JobId.new(),
                email=EmailAddress("jobs@example.com"),
                subject="Subject",
                body="Body",
                cv_path="cvs/python/cv.pdf",
                gmail_draft_id="draft-1",
                status=ApplicationStatus.DRAFT_CREATED,
                sent_at=_NOW,
            )


class TestApplicationEquality:
    """Identidad por `id`, mismo criterio que `Job`/`JobAnalysis`."""

    def test_equal_when_same_id(self) -> None:
        application = _make_application()
        same_application_different_instance = Application.reconstruct(
            id=application.id,
            job_id=application.job_id,
            email=application.email,
            subject=application.subject,
            body=application.body,
            cv_path=application.cv_path,
            gmail_draft_id=application.gmail_draft_id,
            status=application.status,
            sent_at=application.sent_at,
        )

        assert application == same_application_different_instance
        assert hash(application) == hash(same_application_different_instance)

    def test_not_equal_when_different_id(self) -> None:
        first = _make_application()
        second = _make_application()

        assert first != second

    def test_not_equal_to_an_object_of_a_different_type(self) -> None:
        application = _make_application()

        assert application != "not an Application"
        assert application.__eq__("not an Application") is NotImplemented
