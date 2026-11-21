from collections import Counter

from duebook.bench import real_jira
from duebook.bench.real_sources import digest, read, source_messages, to_ticket
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
    comments = [comment(i, person, f"Message {i}") for i in range(50)]
    histories = [
        change(1, person, "assignee", None, "someone"),
        change(2, person, "status", "1", "2"),
    ]
    fields = {"project": {"key": "TEST"}, "assignee": person}
    calls = []

    def get(tracker, route, **params):
        assert tracker == "redhat"
        calls.append(route)
        if route.endswith("/search/jql"):
            assert params["jql"] == "project = TEST ORDER BY votes DESC, key ASC"
            return {"issues": [{"key": "TEST-1", "fields": {"comment": {"total": 50}}}]}
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
        return {"total": 50, "startAt": offset, "comments": comments[offset : offset + 25]}

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


def test_jira_draw_prefers_company_projects_and_only_fills_the_shortfall_at_50(tmp_path):
    staff, reporter = {"key": "agent"}, {"key": "customer"}

    def issue(project, number, tracker="atlassian", count=100, text="A comment"):
        return {
            "tracker": tracker,
            "key": f"{project}-{number}",
            "project": project,
            "comment_count": count,
            "fields": {"reporter": reporter, "assignee": staff},
            "comments": [comment(1, reporter, "Problem", EARLY)]
            + [comment(i, staff, text) for i in range(2, count + 1)],
            "histories": [change(1, staff, "assignee", None, "agent")],
        }

    issues = [issue("AAA", i) for i in range(1, 8)]
    issues[0]["comments"][1]["body"] = "Will be included in the next release."
    issues += [issue("BBB", i) for i in range(1, 6)]
    issues += [issue("CCC", i, "redhat") for i in range(1, 5)]
    issues.append(issue("CCC", 5, "redhat", 50))
    issues += [issue("DDD", i, "apache") for i in range(1, 6)]
    issues.append(issue("EEE", 1, count=49))
    bundle = real_jira.draw_jira({"issues": issues, "projects": [], "per_project": 20}, 7373)
    assert bundle["comment_floor"] == 50
    assert bundle["frame_counts"]["fewer_than_50_comments"] == 1
    assert Counter(c["project"] for c in bundle["cases"]) == {"AAA": 5, "BBB": 5, "CCC": 5}
    assert any(case["id"].endswith("/AAA-1") for case in bundle["cases"])
    path = tmp_path / "bundle.json"
    real_jira.write(path, bundle)
    bundle["source_snapshot_sha256"] = "snapshot"
    manifest = real_jira.jira_manifest(bundle, digest(path))
    assert manifest["below_100_comments"] == 1
    assert all("text" not in row for row in manifest["selected"])
