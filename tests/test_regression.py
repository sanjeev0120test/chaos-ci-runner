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


def test_non_numeric_score_is_an_error() -> None:
    diff = compare_reports({"score": {"score": "n/a"}}, {"score": {"score": 80}}, max_drop=5)
    assert diff.status == "missing_current"
    assert diff.exit_code == 2


def test_explain_names_the_probe_and_experiment_that_moved() -> None:
    diff = compare_reports(
        {
            "score": {"score": 90},
            "experiments": [{"name": "pod-kill", "status": "failed"}],
            "probes": [
                {
                    "probe": "api",
                    "window": "during",
                    "success_rate_pct": 80,
                    "latency_p99_ms": 400,
                }
            ],
        },
        {
            "score": {"score": 100},
            "experiments": [{"name": "pod-kill", "status": "succeeded"}],
            "probes": [
                {
                    "probe": "api",
                    "window": "during",
                    "success_rate_pct": 99,
                    "latency_p99_ms": 40,
                }
            ],
        },
        max_drop=5,
    )
    assert diff.status == "regression"
    assert "experiment `pod-kill` succeeded -> failed" in diff.changes
    assert any(line.startswith("probe `api:during` success") for line in diff.changes)
    assert any("p99" in line for line in diff.changes)
