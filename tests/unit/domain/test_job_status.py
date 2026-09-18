import pytest

from app.domain.value_objects.job_status import JobStatus

pytestmark = pytest.mark.unit


class TestJobStatusTransitions:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (JobStatus.SCRAPED, JobStatus.ANALYZED),
            (JobStatus.ANALYZED, JobStatus.RELEVANT),
            (JobStatus.ANALYZED, JobStatus.NOT_RELEVANT),
            (JobStatus.RELEVANT, JobStatus.CV_SELECTED),
            (JobStatus.CV_SELECTED, JobStatus.EMAIL_GENERATED),
            (JobStatus.EMAIL_GENERATED, JobStatus.DRAFT_CREATED),
            (JobStatus.DRAFT_CREATED, JobStatus.SENT),
            (JobStatus.SCRAPED, JobStatus.IGNORED),
            (JobStatus.ANALYZED, JobStatus.IGNORED),
            (JobStatus.RELEVANT, JobStatus.IGNORED),
            (JobStatus.CV_SELECTED, JobStatus.IGNORED),
            (JobStatus.EMAIL_GENERATED, JobStatus.IGNORED),
        ],
    )
    def test_allows_valid_transitions(self, current: JobStatus, target: JobStatus) -> None:
        assert current.can_transition_to(target) is True

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (JobStatus.SCRAPED, JobStatus.RELEVANT),
            (JobStatus.SCRAPED, JobStatus.SENT),
            (JobStatus.ANALYZED, JobStatus.CV_SELECTED),
            (JobStatus.RELEVANT, JobStatus.NOT_RELEVANT),
            (JobStatus.NOT_RELEVANT, JobStatus.ANALYZED),
            (JobStatus.SENT, JobStatus.SCRAPED),
            (JobStatus.DRAFT_CREATED, JobStatus.DRAFT_CREATED),
            (JobStatus.DRAFT_CREATED, JobStatus.IGNORED),
            (JobStatus.SENT, JobStatus.IGNORED),
            (JobStatus.NOT_RELEVANT, JobStatus.IGNORED),
            (JobStatus.IGNORED, JobStatus.SCRAPED),
            (JobStatus.IGNORED, JobStatus.IGNORED),
        ],
    )
    def test_rejects_invalid_transitions(self, current: JobStatus, target: JobStatus) -> None:
        assert current.can_transition_to(target) is False

    def test_not_relevant_and_sent_are_terminal(self) -> None:
        for status in JobStatus:
            assert JobStatus.NOT_RELEVANT.can_transition_to(status) is False
            assert JobStatus.SENT.can_transition_to(status) is False

    def test_ignored_is_terminal(self) -> None:
        for status in JobStatus:
            assert JobStatus.IGNORED.can_transition_to(status) is False
