"""Mendeley reconstruction, labels and use of the shared benchmark runner."""

import json
from dataclasses import replace

import httpx
import pytest

from duebook.bench import real
from duebook.bench.experiment import make_methods
from duebook.bench.real_labels import DRAFT_ANNOTATOR, compare, reviewed, validate
from duebook.bench.real_sources import digest, read, to_ticket, write
from duebook.bench.result import Promise, PromiseStatus, Snapshot, checkpoints
from duebook.events import Side
from duebook.reducer import RULES, replay
from scripts.label import Labels


def case():
    return {
        "id": "M1",
        "source": "mendeley",
        "cohort": "exploratory",
        "changes": [],
        "messages": [
            {"id": "1", "text": "Please check.", "actor_id": "1", "role": "customer",
             "at": "2017-11-01T00:00:00+00:00"},
            {"id": "2", "text": "I'll check.", "actor_id": "2", "role": "agent",
             "at": "2017-11-02T00:00:00+00:00"},
        ],
    }  # fmt: skip


def test_unknown_fulfillment_is_unjudged_and_quotes_must_be_verbatim():
    conversation = case()
    label = {"commitments": [{"id": "a", "action": "check", "owner_side": "company",
              "owner_actor_id": "2", "opening": [{"message_id": "2", "quote": "I'll check."}],
              "deadline": {"kind": "ambiguous", "at": None,
                           "evidence": [{"message_id": "2", "quote": "check"}]},
              "fulfillment": {"state": "unknown", "evidence": []}}], "ambiguous": []}  # fmt: skip
    validate(conversation, label)
    (check,) = checkpoints(to_ticket(conversation))
    snap = Snapshot(check, (Promise(Side.COMPANY, 2, "check", None, PromiseStatus.FULFILLED),))
    counts, metrics, errors = compare(conversation, label, snap)
    assert counts.tp == 1 and metrics == {"excluded_anchors": 0, "fulfillment_unknown": 1}
    assert errors == []
    label["commitments"][0]["opening"][0]["quote"] = "invented quote"
    with pytest.raises(ValueError, match="invalid evidence"):
        validate(conversation, label)


def test_labeling_page_writes_the_validated_annotation_sheet(tmp_path):
    conversation = case()
    promise, confirmation = "I'll check today.", "I got the update."
    conversation["messages"][1]["text"] = promise
    conversation["messages"].append(
        conversation["messages"][0]
        | {"id": "3", "at": conversation["messages"][1]["at"], "text": confirmation}
    )
    bundle = {"schema": 1, "cases": [conversation], "model_output": "ignored"}
    bundle_path, labels_path = tmp_path / "bundle.json", tmp_path / "labels.json"
    write(bundle_path, bundle)
    page = Labels(bundle_path, labels_path)
    initial = page.data()
    assert "model_output" not in initial
    (original,) = initial["labels"]["tickets"]
    ticket = original | {
        "annotator": DRAFT_ANNOTATOR,
        "commitments": [{
            "id": "P1",
            "action": "check",
            "owner_side": "company",
            "owner_actor_id": "2",
            "opening": [{"message_id": "2", "quote": promise}],
            "deadline": {
                "kind": "exact",
                "at": "2017-11-02T23:59:00+00:00",
                "evidence": [{"message_id": "2", "quote": "today"}],
            },
            "fulfillment": {
                "state": "fulfilled",
                "evidence": [{"message_id": "3", "quote": confirmation}],
            },
        }],
    }
    page.save(original, ticket)
    reopened = Labels(bundle_path, labels_path)
    assert reopened.data()["labels"]["tickets"] == [ticket]
    with pytest.raises(ValueError, match="Enter your name"):
        reopened.save(ticket, ticket | {"review": "complete"})
    updated = ticket | {"annotator": "Reviewer", "review": "complete"}
    reopened.save(ticket, updated)
    assert reviewed(bundle, read(labels_path), digest(bundle_path)) == {"M1": updated}


def test_live_preflight_precedes_any_request_and_reuses_the_meters_and_report(
    tmp_path, monkeypatch
):
    conversation = case()
    company = conversation["messages"][1]
    conversation["messages"].append(company | {"id": "3", "text": "I'll reply."})
    bundle = {"cases": [conversation]}
    labels = real.annotation_sheet(bundle, "source-sha")
    labels["tickets"][0]["annotator"] = DRAFT_ANNOTATOR
    labels_path, out = tmp_path / "labels.json", tmp_path / "run.json"
    write(labels_path, labels)
    monkeypatch.setenv("DUEBOOK_OPENROUTER_API_KEY", "fake-test-key")
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "provider": "DeepInfra",
                "usage": {"prompt_tokens": 100, "completion_tokens": 1, "cost": 0.001},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:

        def fake_methods(*args, **kwargs):
            (method,) = make_methods(*args, **kwargs)
            method.model = replace(method.model, http=client)
            return [method]

        monkeypatch.setattr(real, "make_methods", fake_methods)
        with pytest.raises(ValueError, match="documented source"):
            real.live(bundle, labels_path, "source-sha", {}, 0.5, out)
        assert not requests and not out.exists()
        approval = {"bundle_sha256": "source-sha", "labels_sha256": digest(labels_path),
                    "route": real.ROUTE, "external_inference": True,
                    "approved_by": "test", "rights_and_provider_basis": "mocked source",
                    "max_usd": 1, "estimate": real.estimate(bundle)}  # fmt: skip
        real.live(bundle, labels_path, "source-sha", approval, 0.5, out)
    assert len(requests) == 3 and all(r["provider"]["zdr"] for r in requests)
    assert all(r["provider"]["data_collection"] == "deny" for r in requests)
    assert all(not r["provider"]["allow_fallbacks"] for r in requests)
    record = read(out)
    assert record["spend_usd"] == pytest.approx(0.003)
    rendered = real.report(bundle, reviewed(bundle, labels, "source-sha"), record)
    assert "| M1 | 0 / 0 / 0 |" in rendered
    assert "Draft V1 scores (machine-drafted, not human-reviewed, n=1)" in rendered


def test_mendeley_rebuilds_public_boundaries_and_keeps_uncertain_roles_local(tmp_path):
    from duebook.bench.real_mendeley import prepare

    at = "2022-06-05 09:45:56+00:00"
    later = "2022-06-06 08:00:00+00:00"
    source = tmp_path / "release"
    source.mkdir()
    (source / "sample_utterances.csv").write_text(
        "issueid,id,comment_seq,utr_seq,author,created,is_private,actionbody,author_role\n"
        f"42,c1,1,2,r,{at},0.0,there,reporter\n"
        f"42,c1,1,1,r,{at},0.0,is anyone,reporter\n"
        f"42,c2,2,1,a,{later},0.0,will send to person@example.org in CityName,assignee\n"
        f"42,c3,3,1,x,{later},0.0,wait for me,others\n"
        f"42,c4,4,1,r,{later},1.0,private text,reporter\n",
        encoding="utf-8",
    )
    (source / "issues.csv").write_text("id,issue_reporter\n42,r\n", encoding="utf-8")
    (source / "issues_change_history.csv").write_text(
        "id,issueid,field,value,created\n"
        "1,42,assignee,a,2022-06-05 11:00:00+00:00\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "source.jsonl"
    manifest.write_text(json.dumps({"ticket_id": "42", "comment_ids": ["c1", "c2", "c3", "c4"]}))
    bundle_path, source_review = tmp_path / "bundle.json", tmp_path / "review.json"
    selection = {"M1": {"id": "42", "visible": 3},
                 "redact": [{"kind": "location", "pattern": r"\bcityname\b"}]}  # fmt: skip
    prepare(source, manifest, selection, bundle_path, source_review)
    rebuilt = tmp_path / "second.json"
    prepare(source, manifest, selection, rebuilt, tmp_path / "second-review.json")
    assert digest(bundle_path) == digest(rebuilt)

    (selected,) = read(bundle_path)["cases"]
    assert [(m["id"], m["text"], m["role"]) for m in selected["messages"]] == [
        ("c1", "is anyone there", "customer"),
        ("c2", "will send to [redacted email] in [redacted location]", "agent"),
    ]
    assert selected["messages"][0]["at"] == at
    review = Labels(bundle_path, tmp_path / "labels.json", source_review).data()["cases"][0]
    assert [(m["id"], m["withheld"]) for m in review["messages"]] == [
        ("c1", False), ("c2", False), ("c3", True),
    ]
    assert [e.id for e in to_ticket(selected).events] == [
        "mendeley:c1", "mendeley:1", "mendeley:c2",
    ]


def test_zero_call_replay_records_each_event_without_changing_v1_checkpoints(tmp_path, monkeypatch):
    from duebook.extract.client import ChatClient
    from duebook.store.codec import decode_card

    def reject_call(*args, **kwargs):
        pytest.fail("offline path called the model")

    monkeypatch.setattr(ChatClient, "complete_json", reject_call)
    sample = case() | {"changes": [
        {"id": "handoff", "kind": "assignee", "previous": "1", "current": "2",
         "at": "2017-11-01T10:00:00+00:00"}
    ]}
    bundle = {"cases": [sample]}
    out = tmp_path / "offline.json"
    real.offline(bundle, real.annotation_sheet(bundle, "frozen"), "frozen", out)
    (record,) = read(out)["runs"]
    assert record["usage"]["calls"] == 0
    ticket = to_ticket(sample)
    assert real.run_of(record).snapshots[0].checkpoint == checkpoints(ticket)[0]
    trace = record["detail"]["event_trace"]
    assert [item["event_id"] for item in trace] == [event.id for event in ticket.events]
    assert record["detail"]["proposals"] == [[], [], []]
    assert record["detail"]["closing_rules"] == RULES
    assert decode_card(trace[-1]["card"]) == replay(
        ticket.ticket_id, [(event, ()) for event in ticket.events]
    )
