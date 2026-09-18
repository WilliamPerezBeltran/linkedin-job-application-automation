import pytest

from app.domain.value_objects.application_status import ApplicationStatus

pytestmark = pytest.mark.unit


class TestApplicationStatusTransitions:
    def test_draft_created_can_transition_to_sent(self) -> None:
        assert ApplicationStatus.DRAFT_CREATED.can_transition_to(ApplicationStatus.SENT) is True

    def test_sent_is_terminal(self) -> None:
        assert ApplicationStatus.SENT.can_transition_to(ApplicationStatus.DRAFT_CREATED) is False
        assert ApplicationStatus.SENT.can_transition_to(ApplicationStatus.SENT) is False

    def test_draft_created_cannot_transition_to_itself(self) -> None:
        assert (
            ApplicationStatus.DRAFT_CREATED.can_transition_to(ApplicationStatus.DRAFT_CREATED)
            is False
        )
