"""Domain value objects: immutable, self-validating pieces of data."""

from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus

__all__ = [
    "ApplicationId",
    "ApplicationStatus",
    "EmailAddress",
    "JobId",
    "JobStatus",
]
