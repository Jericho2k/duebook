"""The results document's tables and figure are computed from the saved record."""

from duebook.bench.report import close_counts, differences, guard_effects, prompt_growth


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


def test_closes_are_reported_as_counts_with_the_tickets_each_method_ran_on():
    counts = {"closes_right": 9, "closes_false": 1, "closes_wrong_target": 2, "closes_missed": 3,
              "suggestions_right": 1, "suggestions_wrong": 0}  # fmt: skip
    summary = {"methods": {"mem0": {"tickets": 4, "counts_end": counts}}}
    assert close_counts(summary).splitlines()[-1] == "| mem0 | 4 | 9 | 1 | 2 | 3 of 14 | 1 / 0 |"


def test_what_the_guards_refused_is_tabled_by_reason_with_a_total():
    effects = {"verdicts": {"false_close_prevented": 2, "true_close_blocked": 1},
               "by_reason": {"ambiguous_target": {"false_close_prevented": 2},
                             "target_mismatch": {"true_close_blocked": 1}}}  # fmt: skip
    rows = guard_effects({"guard_effects": effects}).splitlines()
    assert rows[2] == "| ambiguous_target | 2 | 0 | 0 | 0 |"
    assert rows[-1] == "| **all** | 2 | 0 | 1 | 0 |"


def test_graphiti_closing_the_customers_value_of_an_open_dispute_is_counted_as_closed_current():
    from duebook.bench.generate import SPLITS
    from duebook.bench.report import graphiti_disputes

    ticket = SPLITS["dev3"].tickets()[0]
    disputes = ticket.gold.contradictions
    lines = []
    for d in disputes:
        said, recorded = d.values
        lines += [f"- (from #{d.seqs[0]}) Клиент говорит {said}. [no longer true from Mon]",
                  f"- (from #{d.seqs[1]}) Компания говорит {recorded}."]  # fmt: skip
    listing = "\n".join(["FACTS", *lines, "ENTITIES"])
    end = {"listing": listing}
    run = {"method": "graphiti", "ticket": ticket.ticket_id, "detail": {"end": end}}
    counts = graphiti_disputes({"split": "dev3", "runs": [run]})
    assert sum(counts["open"].values()) + sum(counts["settled"].values()) == len(disputes)
    assert set(counts["open"]) | set(counts["settled"]) == {"closed / current"}
