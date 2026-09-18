"""Domain entities: objects with identity and lifecycle across the pipeline."""

from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis

__all__ = [
    "Application",
    "Job",
    "JobAnalysis",
]
