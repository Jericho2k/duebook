"""The results document's tables and figure are computed from the saved record."""

from duebook.bench.report import differences, prompt_growth


def test_a_difference_whose_interval_excludes_zero_is_marked():
    summary = {
        "reference": "a",
        "methods": {
            "a": {"x": {"diff_vs_reference": 0.0, "diff_ci95": [0.0, 0.0]}},
            "b": {"x": {"diff_vs_reference": -0.2, "diff_ci95": [-0.3, -0.1]}},
            "c": {"x": {"diff_vs_reference": 0.1, "diff_ci95": [-0.1, 0.3]}},
        },
    }
    row = differences(summary, ["x"]).splitlines()[-1]
    assert "-0.200 [-0.300, -0.100] *" in row and "+0.100 [-0.100, +0.300] |" in row


def test_the_prompt_growth_curve_comes_from_measured_tokens_and_both_renderings():
    run = {
        "method": "event_memory",
        "ticket": "T",
        "detail": {
            "prompt_tokens": [[1, 1000], [2, 1100], [3, 1300]],
            "prompt_chars": [[1, 4000, 4000], [2, 4400, 4300], [3, 5200, 4800]],
        },
    }
    svg, rows = prompt_growth({"runs": [run, {**run, "method": "mem0"}]})
    assert svg.count("<polyline") == 2
    assert "| T | 3 | 1000 | 1300 | +30% | 1200 | 7.7% |" in rows
