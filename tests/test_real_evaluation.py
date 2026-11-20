"""The real-data adapter preserves evidence and gates the existing runner."""

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from duebook.bench import real
from duebook.bench.experiment import make_methods
from duebook.bench.real_labels import compare, validate
from duebook.bench.real_sources import digest, draw, github, read, to_ticket, twitter, write
from duebook.bench.result import Promise, PromiseStatus, Snapshot, checkpoints
from duebook.events import MessagePosted, Role, Side


def case():
    return {
        "id": "twcs:1",
        "source": "twcs",
        "url": "https://www.kaggle.com/datasets/thoughtvector/customer-support-on-twitter",
        "cohort": "random",
        "messages": [
            {"id": "1", "text": "Please check.", "actor_id": "1", "role": "customer",
             "at": "2017-11-01T00:00:00+00:00"},
            {"id": "2", "text": "I'll check.", "actor_id": "2", "role": "agent",
             "at": "2017-11-02T00:00:00+00:00"},
        ],
    }  # fmt: skip


def test_twitter_reply_ids_connect_branches_with_text_and_timestamps_intact(tmp_path):
    path = tmp_path / "twcs.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("tweet_id", "author_id", "inbound", "created_at", "text",
                         "response_tweet_id", "in_response_to_tweet_id"))  # fmt: skip
        writer.writerows(
            [
                (4, 9, True, "Thu Nov 02 09:00:00 +0000 2017", "  branch \n text\r\n ", "", "1"),
                (2, 8, False, "Thu Nov 02 09:00:00 +0000 2017", "reply", "", ""),
                (1, 7, True, "Wed Nov 01 09:00:00 +0000 2017", "__email__", "2,4", "99"),
                (8, 7, True, "", "missing time", "9", ""),
                (9, 8, False, "Thu Nov 02 09:00:00 +0000 2017", "other reply", "", "8"),
            ]
        )
    frozen = draw(twitter(path), 7373, 1, 0)
    (conversation,) = frozen["cases"]
    events = to_ticket(conversation).events
    assert frozen["frame_counts"] == {"eligible": 1, "missing_or_unusable_timestamp": 1}
    assert [e.id for e in events] == ["twcs:1", "twcs:2", "twcs:4"]
    assert all(isinstance(e, MessagePosted) for e in events)
    assert events[-1].text == "  branch \n text\r\n "
    assert events[-1].at == datetime(2017, 11, 2, 9, tzinfo=UTC)
    assert conversation["messages"][0]["parent_id"] == "99"


def test_github_reporter_role_is_stable_even_with_team_association(tmp_path):
    frame = tmp_path / "frame.json"
    issue_url = "https://api.github.com/repos/sample/project/issues/1"
    public_url = "https://github.com/sample/project/issues/1"
    opener = {"id": 11, "number": 1, "body": "  raw **Markdown** \r\n",
              "created_at": "2025-01-01T00:00:00Z", "comments": 2,
              "url": issue_url, "html_url": public_url, "author_association": "MEMBER",
              "user": {"type": "User", "id": 1}}  # fmt: skip
    replies = [dict(opener, id=i, issue_url=issue_url, html_url=f"{public_url}#issuecomment-{i}",
                    created_at="2025-02-01T00:00:00Z", user={"type": "User", "id": a})
               for i, a in [(23, 2), (22, 1)]]  # fmt: skip
    write(frame, [{"total_count": 1, "incomplete_results": False, "items": [opener]}])
    write(tmp_path / "1.json", replies)
    (conversation,) = draw(github(frame, tmp_path), 1, 1, 0)["cases"]
    ticket = to_ticket(conversation)
    assert [e.author.role for e in ticket.events] == [Role.CUSTOMER, Role.CUSTOMER, Role.AGENT]
    assert ticket.events[0].text == opener["body"]
    assert ticket.end == ticket.events[-1].at


def test_draw_is_repeatable_and_does_not_move_random_cases_into_challenge():
    sample = case()
    posts = [m | {"id": str(i), "created_at": m["at"]} for i, m in enumerate(sample["messages"], 1)]
    posts.extend(posts[-1] | {"id": str(i), "created_at": "2017-12-01T00:00:00+00:00"}
                 for i in range(3, 9))  # fmt: skip

    def candidates(reverse=False):
        ids = reversed(range(6)) if reverse else range(6)
        return [(sample | {"id": f"twcs:{i}", "messages": [dict(p) for p in posts]}, None)
                for i in ids]  # fmt: skip

    chosen = draw(candidates(), 7373, 3, 2)
    assert chosen == draw(candidates(True), 7373, 3, 2)
    assert len({c["id"] for c in chosen["cases"]}) == 5
    assert chosen["cases"][:3] == draw(candidates(), 7373, 3, 0)["cases"]


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


def test_live_preflight_precedes_any_request_and_reuses_the_meters_and_report(
    tmp_path, monkeypatch
):
    bundle = {"cases": [case()], "seed": 1, "frame_counts": {"eligible": 1}}
    labels = real.annotation_sheet(bundle, "source-sha")
    labels["tickets"][0].update(review="complete", annotator="test reviewer")
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

        def fake_methods(*args):
            (method,) = make_methods(*args)
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
    assert len(requests) == 2 and all(r["provider"]["zdr"] for r in requests)
    assert all(r["provider"]["data_collection"] == "deny" for r in requests)
    assert all(not r["provider"]["allow_fallbacks"] for r in requests)
    record = read(out)
    assert record["spend_usd"] == pytest.approx(0.002)
    rendered = real.report(bundle, {"twcs:1": labels["tickets"][0]}, record,
                           read(Path("results/round10/test5.json")))  # fmt: skip
    assert "twcs / random" in rendered and "0 / 0 / 0" in rendered
    assert "Historical synthetic `test5`" in rendered
