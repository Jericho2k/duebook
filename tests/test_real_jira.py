from collections import Counter

from duebook.bench import real, real_jira
from duebook.bench.real_sources import digest, read, source_messages, to_ticket, write
from duebook.events import AssigneeChanged, MessagePosted, Role, StatusChanged

EARLY = "2024-05-01T10:00:00+0200"
LATE = "2024-05-15T10:00:00+0200"


def comment(number, author, text, at=LATE):
    return {"id": str(number), "author": author, "body": text, "created": at}


def change(number, author, field, before, after, *, at=LATE, **labels):
    return {
        "id": str(number),
        "created": at,
        "author": author,
        "items": [{"field": field, "from": before, "to": after, **labels}],
    }


def test_jira_snapshot_paginates_anonymous_comments_and_cloud_history(tmp_path, monkeypatch):
    person = {"accountId": "someone"}
    comments = [comment(i, person, f"Message {i}") for i in range(100)]
    histories = [
        change(1, person, "assignee", None, "someone"),
        change(2, person, "status", "1", "2"),
    ]
    fields = {"project": {"key": "TEST"}, "assignee": person, "issuetype": {"name": "Bug"}}
    calls = []

    def get(tracker, route, **params):
        assert tracker == "redhat"
        calls.append(route)
        if route.endswith("/search/jql"):
            assert 'issuetype in (Bug, "Support Request") AND assignee CHANGED' in params["jql"]
            assert "votes" not in params["jql"]
            return {"issues": [{"key": "TEST-1", "fields": fields | {"comment": {"total": 100}}}]}
        if route.endswith("/TEST-1"):
            return {
                "key": "TEST-1",
                "fields": fields,
                "changelog": {"total": 2, "histories": histories[:1]},
            }
        offset = params["startAt"]
        if route.endswith("/changelog"):
            assert route == "/rest/api/3/issue/TEST-1/changelog"
            return {"total": 2, "startAt": offset, "values": histories[offset : offset + 1]}
        assert route == "/rest/api/2/issue/TEST-1/comment"
        return {"total": 100, "startAt": offset, "comments": comments[offset : offset + 50]}

    monkeypatch.setattr(real_jira, "get_json", get)
    output = tmp_path / "snapshot.json"
    real_jira.snapshot_jira(output, ["redhat:TEST"])
    (issue,) = read(output)["issues"]
    assert issue["comments"] == comments and issue["histories"] == histories
    assert len(calls) == 6


def test_jira_orders_change_events_and_preserves_verbatim_comments_with_inferred_roles():
    reporter = {"key": "customer"}
    dev = {"key": "development", "name": "dev"}
    current = {"key": "oncall"}
    fixer = {"key": "fixer"}
    status_author = {"key": "status-editor"}
    bot = {"accountId": "robot-123", "accountType": "app"}
    log = "{code:java}\r\nERROR méssage\r\n{code}\r\n"
    issue = {
        "tracker": "atlassian",
        "key": "TEST-1",
        "project": "TEST",
        "type": "Bug",
        "comment_count": 6,
        "fields": {"reporter": reporter, "assignee": current},
        "histories": [
            change(2, status_author, "status", "1", "2", fromString="Open", toString="Done"),
            change(1, reporter, "assignee", "dev", "oncall"),
            change(3, fixer, "Fix Version", None, "3"),
            change(4, reporter, "fixVersions", None, "4"),
        ],
        "comments": [
            comment(6, fixer, "Release"),
            comment(5, bot, "Automated"),
            comment(4, status_author, "Resolution"),
            comment(3, current, "Next release"),
            comment(2, dev, log),
            comment(1, reporter, "Problem", EARLY),
        ],
    }
    case = real_jira.jira_case(issue)
    assert real_jira.normalized(case, None) is None
    ticket = to_ticket(case)
    message, assignee, status, *rest = ticket.events
    assert isinstance(assignee, AssigneeChanged) and isinstance(status, StatusChanged)
    assert (assignee.previous, assignee.current) == ("development", "oncall")
    assert (status.previous, status.current) == ("Open", "Done")
    assert all(isinstance(event, MessagePosted) for event in [message, *rest])
    assert [event.author.role for event in [message, *rest]] == [
        Role.CUSTOMER,
        Role.AGENT,
        Role.AGENT,
        Role.AGENT,
        Role.SYSTEM,
        Role.AGENT,
    ]
    assert [event.seq for event in ticket.events] == list(range(1, 9))
    assert rest[0].text == source_messages(case, ticket)[rest[0].seq]["text"] == log
    counts = real_jira.code_log_counts(case["messages"])
    assert counts["code_log_comments"] == 1 and counts["code_log_lines"] == 3
    assert counts["code_log_bytes"] == len(log.encode("utf-8"))


def test_jira_caps_the_event_prefix_before_using_changes_to_infer_roles():
    reporter, staff, late = {"key": "reporter"}, {"key": "agent"}, {"key": "late"}
    issue = {
        "tracker": "atlassian",
        "key": "TEST-1",
        "project": "TEST",
        "type": "Bug",
        "comment_count": 310,
        "fields": {"reporter": reporter, "assignee": late},
        "histories": [
            change(1, staff, "assignee", None, "agent", at=EARLY),
            change(2, staff, "status", "1", "2"),
            change(3, late, "assignee", "agent", "late", at="2024-05-16T10:00:00+0200"),
            change(4, late, "Fix Version", None, "4", at="2024-05-16T10:00:00+0200"),
        ],
        "comments": [
            comment(1, reporter, "Problem", EARLY),
            comment(2, late, "Only the later changelog identifies this actor"),
            *[comment(i, staff, "Message") for i in range(3, 311)],
        ],
    }
    case = real_jira.jira_case(issue)
    assert real_jira.normalized(case, None) is None
    ticket = to_ticket(case)
    assert case["truncated"] and len(ticket.events) == 300
    assert len(case["messages"]) == 298
    assert ticket.events[-1].id == "jira:298"
    assert ticket.events[-1].at.isoformat() == "2024-05-15T08:00:00+00:00"
    assert case["messages"][1]["role"] == Role.CUSTOMER


def test_jira_draw_shrinks_under_stress_with_ranked_company_comments(tmp_path):
    staff, reporter = {"key": "agent"}, {"key": "customer"}

    def issue(project, number, company, tracker="atlassian", count=120, kind="Bug"):
        return {
            "tracker": tracker,
            "key": f"{project}-{number}",
            "project": project,
            "type": kind,
            "comment_count": count,
            "fields": {"reporter": reporter, "assignee": staff},
            "comments": [comment(1, reporter, "+1", EARLY)]
            + [
                comment(i, staff if i <= company + 1 else reporter, "How to reproduce")
                for i in range(2, count + 1)
            ],
            "histories": [change(1, staff, "assignee", None, "agent")],
        }

    issues = [
        issue("AAA", 1, 22),
        issue("AAA", 2, 29),
        issue("BBB", 1, 24),
        issue("CCC", 1, 23, "redhat", kind="Support Request"),
        issue("DDD", 1, 20),
        issue("FFF", 1, 77, "apache"),
        issue("FFF", 2, 20, count=150),
        issue("FFF", 3, 19),
        issue("FFF", 4, 50, kind="Suggestion"),
        issue("FFF", 5, 50, count=99),
    ]
    snapshot = {"issues": issues, "projects": [], "per_project": 80}
    bundle = real_jira.draw_jira(snapshot, 7373, real.estimate)
    counts = bundle["frame_counts"]
    assert counts["wrong_issue_type"] == counts["fewer_than_100_source_comments"] == 1
    assert counts["fewer_than_20_company_comments"] == counts["company_share_under_15_percent"] == 1
    assert Counter(c["project"] for c in bundle["cases"]) == {"AAA": 2, "BBB": 1, "CCC": 1}
    assert [c["company_comments"] for c in bundle["cases"]] == [29, 24, 23, 22]
    projection = real.estimate(bundle)
    assert projection["expected_usd"] <= 0.40 and projection["stress_usd"] <= 1.50
    path = tmp_path / "bundle.json"
    bundle["source_snapshot_sha256"] = "snapshot"
    write(path, bundle)
    manifest = real_jira.jira_manifest(bundle, digest(path))
    assert [row["short_low_signal_comments"] for row in manifest["selected"]] == [1] * 4
    assert all("text" not in row for row in manifest["selected"])


def test_jira_draw_checks_expected_cap_even_when_stress_fits():
    candidates = [
        ({"project": project, "tracker": "atlassian"}, expected, stress)
        for project, expected, stress in (
            ("AAA", 0.19, 0.50),
            ("BBB", 0.12, 0.40),
            ("CCC", 0.12, 0.40),
        )
    ]
    assert real_jira.affordable(candidates, 3, 0) is None
