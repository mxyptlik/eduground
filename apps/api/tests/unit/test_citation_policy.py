import pytest

from app.policies.citation_policy import CitationPolicy

pytestmark = pytest.mark.unit


def test_citation_policy_fails_when_under_threshold() -> None:
    policy = CitationPolicy(minimum_coverage=1.0)
    result = policy.validate(factual_blocks=2, citation_count=1)
    assert result.is_valid is False
    assert result.coverage_ratio == 0.5
