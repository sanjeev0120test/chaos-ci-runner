"""Score regression decisions, independent of the CLI printer."""

from __future__ import annotations

from chaos_ci_runner.regression import compare_reports


def test_within_tolerance() -> None:
    diff = compare_reports(
        {"score": {"score": 96}},
        {"score": {"score": 100}},
        max_drop=5,
    )
    assert diff.status == "ok"
    assert diff.drop == 4
    assert diff.exit_code == 0


def test_drop_beyond_tolerance_fails() -> None:
    diff = compare_reports(
        {"score": {"score": 90}},
        {"score": {"score": 100}},
        max_drop=5,
    )
    assert diff.status == "regression"
    assert diff.exit_code == 1


def test_missing_current_score_is_an_error() -> None:
    diff = compare_reports({}, {"score": {"score": 80}}, max_drop=5)
    assert diff.status == "missing_current"
    assert diff.exit_code == 2


def test_unscored_baseline_is_accepted() -> None:
    diff = compare_reports({"score": {"score": 88}}, {}, max_drop=5)
    assert diff.status == "baseline_unscored"
    assert diff.exit_code == 0
