import pytest

from app.domain.entities.job_analysis import JobAnalysis
from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.value_objects.job_id import JobId

pytestmark = pytest.mark.unit


def _make_analysis(**overrides: object) -> JobAnalysis:
    defaults: dict[str, object] = {
        "job_id": JobId.new(),
        "job_type": "python",
        "seniority": "senior",
        "skills": ["Python", "FastAPI"],
        "languages": ["Python"],
        "frameworks": ["FastAPI"],
        "cloud": ["AWS"],
        "ai_related": False,
    }
    defaults.update(overrides)
    return JobAnalysis(**defaults)  # type: ignore[arg-type]


class TestJobAnalysisConstruction:
    def test_stores_skill_collections_as_tuples(self) -> None:
        analysis = _make_analysis(skills=["Python", "FastAPI"])

        assert analysis.skills == ("Python", "FastAPI")
        assert isinstance(analysis.skills, tuple)

    def test_rejects_empty_job_type(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            _make_analysis(job_type="  ")

    @pytest.mark.parametrize("match_score", [-0.1, 1.1])
    def test_rejects_match_score_out_of_range(self, match_score: float) -> None:
        with pytest.raises(InvalidDomainValueError):
            _make_analysis(match_score=match_score)

    @pytest.mark.parametrize("match_score", [0.0, 0.5, 1.0])
    def test_accepts_match_score_within_range(self, match_score: float) -> None:
        analysis = _make_analysis(match_score=match_score)

        assert analysis.match_score == match_score

    def test_cannot_construct_with_generated_email_but_no_recommended_cv(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            _make_analysis(generated_email="body", recommended_cv=None)


class TestJobAnalysisPipelineInvariant:
    def test_cannot_record_generated_email_before_cv_recommendation(self) -> None:
        analysis = _make_analysis()

        with pytest.raises(InvalidDomainValueError):
            analysis.record_generated_email(subject="Application", body="body")

    def test_can_record_generated_email_after_cv_recommendation(self) -> None:
        analysis = _make_analysis()

        analysis.record_cv_recommendation(recommended_cv="cvs/python/cv.pdf", match_score=0.8)
        analysis.record_generated_email(subject="Application", body="body")

        assert analysis.recommended_cv == "cvs/python/cv.pdf"
        assert analysis.match_score == 0.8
        assert analysis.subject == "Application"
        assert analysis.generated_email == "body"

    def test_record_cv_recommendation_rejects_empty_value(self) -> None:
        analysis = _make_analysis()

        with pytest.raises(InvalidDomainValueError):
            analysis.record_cv_recommendation(recommended_cv="  ", match_score=None)


class TestJobAnalysisEquality:
    """`JobAnalysis` -- igual que `Job`/`Application` -- define identidad por
    `job_id` (una `JobAnalysis` por `Job`), no por igualdad estructural de
    todos sus campos."""

    def test_two_analyses_with_the_same_job_id_are_equal(self) -> None:
        job_id = JobId.new()
        first = _make_analysis(job_id=job_id, job_type="python")
        second = _make_analysis(job_id=job_id, job_type="java")

        assert first == second
        assert hash(first) == hash(second)

    def test_two_analyses_with_different_job_ids_are_not_equal(self) -> None:
        first = _make_analysis(job_id=JobId.new())
        second = _make_analysis(job_id=JobId.new())

        assert first != second

    def test_is_not_equal_to_an_object_of_a_different_type(self) -> None:
        analysis = _make_analysis()

        assert analysis != "not a JobAnalysis"
        assert analysis.__eq__("not a JobAnalysis") is NotImplemented
