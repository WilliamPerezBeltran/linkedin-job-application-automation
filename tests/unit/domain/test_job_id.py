from uuid import UUID

import pytest

from app.domain.exceptions.invalid_identifier_error import InvalidIdentifierError
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.job_id import JobId

pytestmark = pytest.mark.unit


class TestJobId:
    def test_new_generates_a_unique_id(self) -> None:
        assert JobId.new() != JobId.new()

    def test_of_parses_a_valid_uuid_string(self) -> None:
        raw = "12345678-1234-5678-1234-567812345678"

        job_id = JobId.of(raw)

        assert job_id.value == UUID(raw)

    def test_of_rejects_an_invalid_string(self) -> None:
        with pytest.raises(InvalidIdentifierError):
            JobId.of("not-a-uuid")

    def test_is_immutable(self) -> None:
        job_id = JobId.new()

        with pytest.raises(AttributeError):
            job_id.value = job_id.value  # type: ignore[misc]


class TestApplicationId:
    def test_new_generates_a_unique_id(self) -> None:
        assert ApplicationId.new() != ApplicationId.new()

    def test_of_rejects_an_invalid_string(self) -> None:
        with pytest.raises(InvalidIdentifierError):
            ApplicationId.of("nope")

    def test_job_id_and_application_id_are_never_equal(self) -> None:
        raw = "12345678-1234-5678-1234-567812345678"

        assert JobId.of(raw) != ApplicationId.of(raw)  # type: ignore[comparison-overlap]
