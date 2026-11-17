import json
import random
from pathlib import Path

from duebook.bench.generate import SPLITS
from duebook.bench.review import main

ROOT = Path(__file__).resolve().parents[1]


def test_test2_spotcheck_reproduces_the_packet_from_archived_rewrites(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    output = tmp_path / "review.md"
    main(["--out", str(output)])
    assert output.read_bytes() == (ROOT / "docs/review/test2-spotcheck.md").read_bytes()
    dialogue, answers = output.read_text(encoding="utf-8").split("## Answer key")
    archive = json.loads((ROOT / "results/round2/tickets/test2.json").read_text(encoding="utf-8"))
    sampled = random.Random(0).sample(SPLITS["test2"].tickets(), 4)
    assert dialogue.count("### SIM-") == answers.count("### SIM-") == 4
    assert " Promise to " not in dialogue
    for ticket in sampled:
        first = archive["tickets"][ticket.ticket_id]["texts"]["e1"]
        assert first in dialogue and f"### {ticket.ticket_id}" in answers
        assert all(f"#{p.seq} Promise to" in answers for p in ticket.gold.commitments)


def test_review_samples_templates_when_a_split_has_no_archive(tmp_path):
    output = tmp_path / "review.md"
    main(["--split", "dev3", "--tickets", "1", "--seed", "7",
          "--results", str(tmp_path), "--out", str(output)])  # fmt: skip
    ticket = random.Random(7).sample(SPLITS["dev3"].tickets(), 1)[0]
    rendered = output.read_text(encoding="utf-8")
    assert "Generated template text." in rendered
    assert rendered.count("### SIM-") == 2 and ticket.events[0].text in rendered
