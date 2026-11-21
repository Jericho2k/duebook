"""Snapshot public Jira conversations and draw long support cases."""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from math import inf
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..events import Role
from .real_sources import instant, normalized, read, write

TRACKERS = {
    "atlassian": "https://jira.atlassian.com",
    "redhat": "https://redhat.atlassian.net",
    "apache": "https://issues.apache.org/jira",
}
PROJECT = re.compile(r"[A-Z][A-Z0-9_]*")
ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")
BOT = re.compile(
    r"\b(?:bot|automation)\b|\b(?:jira|atlassian|redhat|apache)bot\b|"
    r"^(?:asfbot|hadoop\s?qa|hudson|jenkins|yetus)$",
    re.I,
)
UNDERTAKING = re.compile(
    r"\bfix(?:ed)?\s+(?:versions?|in)\b|\bnext release\b|"
    r"(?:\bwill|['’]ll)\s+(?:be\s+)?(?:"
    r"fix(?:ed)?|release[ds]?|includ(?:e|ed)|ship(?:ped)?|publish(?:ed)?|"
    r"backport(?:ed)?|merge[ds]?|get back to you\s+by)\b",
    re.I,
)
FENCE = re.compile(r"\{(?:code|noformat)(?::[^}]+)?\}|```")
LOG_LINE = re.compile(
    r"^\s*(?:\[?(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b|"
    r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d|at [\w.$]+\([^\n]+\)|Caused by:)"
)
QUESTION = re.compile(
    r"\?|^(?:how|what|when|where|why|which|who|can|could|would|should|is|are|does|any update)\b",
    re.I,
)
PROMISE = re.compile(r"\b(?:i|we)(?:\s+will|['’]ll)\b", re.I)
ISSUE_TYPES = {"bug", "support request"}
PER_PROJECT = 80
MAX_ISSUES, MIN_COMMENTS, MAX_EVENTS = 10, 100, 300
MIN_PROJECTS, MAX_PER_PROJECT = 3, 5
EXPECTED_CAP, STRESS_CAP = 0.40, 1.50


def get_json(tracker, route, **params):
    url = f"{TRACKERS[tracker]}{route}?{urlencode(params)}"
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=35) as response:
        return json.load(response)


def cached(path, loader):
    if path.exists():
        return read(path)
    data = loader()
    write(path, data)
    return data


def search_project(cache, tracker, project):
    route = "/rest/api/3/search/jql" if tracker == "redhat" else "/rest/api/2/search"
    types = "= Bug" if tracker == "apache" else 'in (Bug, "Support Request")'
    return cached(
        cache / f"{tracker}-{project}-watchers-{PER_PROJECT}.json",
        lambda: get_json(
            tracker,
            route,
            jql=f"project = {project} AND issuetype {types} AND assignee CHANGED "
            "ORDER BY watchers DESC, key ASC",
            maxResults=PER_PROJECT,
            fields="project,issuetype,comment",
        ),
    )["issues"][:PER_PROJECT]


def paginated(tracker, route, key, page=None):
    if page is None:
        page = get_json(tracker, route, startAt=0, maxResults=100)
    total = page["total"]
    records = []
    while True:
        if page.get("startAt", 0) != len(records) or page["total"] != total:
            raise ValueError("Jira paginated data changed during snapshot")
        records.extend(page[key])
        if len(records) == total:
            break
        if not page[key] or len(records) > total:
            raise ValueError("incomplete Jira page")
        page = get_json(tracker, route, startAt=len(records), maxResults=100)
    if len({record["id"] for record in records}) != total:
        raise ValueError("duplicate Jira records across pages")
    return records


def issue_snapshot(cache, tracker, project, entry):
    key = entry["key"]
    if not ISSUE_KEY.fullmatch(key):
        raise ValueError("invalid Jira issue key")
    fields = entry["fields"]
    embedded = fields["comment"]
    issue = {
        "tracker": tracker,
        "project": project,
        "key": key,
        "comment_count": embedded["total"],
        "type": fields["issuetype"]["name"],
    }
    if embedded["total"] < MIN_COMMENTS or issue["type"].casefold() not in ISSUE_TYPES:
        return issue
    route = f"/rest/api/2/issue/{key}"
    try:
        detail = cached(
            cache / f"{tracker}-{key}-issue.json",
            lambda: get_json(
                tracker, route, fields="project,issuetype,reporter,assignee", expand="changelog"
            ),
        )
        if (
            detail["key"] != key
            or detail["fields"]["project"]["key"] != project
            or detail["fields"]["issuetype"]["name"] != issue["type"]
        ):
            raise ValueError("Jira issue moved or changed type during snapshot")
        history = detail["changelog"]
        histories = history["histories"]
        if len(histories) != history["total"] or history.get("startAt", 0):
            version = 3 if tracker == "redhat" else 2
            histories = paginated(tracker, f"/rest/api/{version}/issue/{key}/changelog", "values")
            if len(histories) != history["total"]:
                raise ValueError("Jira changelog changed during snapshot")
        comments = paginated(
            tracker, f"{route}/comment", "comments", None if tracker == "redhat" else embedded
        )
        if len(comments) != embedded["total"] or any(
            not isinstance(c["body"], str) for c in comments
        ):
            raise ValueError("Jira comment count or raw body differs from search")
    except (OSError, ValueError, KeyError):
        return issue
    return issue | {"fields": detail["fields"], "histories": histories, "comments": comments}


def snapshot_jira(path, projects):
    parsed = list(dict.fromkeys(tuple(value.split(":", 1)) for value in projects))
    for pair in parsed:
        if len(pair) != 2 or pair[0] not in TRACKERS or not PROJECT.fullmatch(pair[1]):
            raise ValueError("use tracker:PROJECT with atlassian, redhat, or apache")
    cache = path.parent / "search"
    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = pool.map(lambda pair: search_project(cache, *pair), parsed)
        work = [
            (tracker, project, entry)
            for (tracker, project), entries in zip(parsed, frames, strict=True)
            for entry in entries
        ]
        issues = list(pool.map(lambda row: issue_snapshot(cache, *row), work))
    if len({(i["tracker"], i["key"]) for i in issues}) != len(issues):
        raise ValueError("duplicate Jira issues in search frame")
    write(path, {"projects": projects, "per_project": PER_PROJECT, "issues": issues})


def identities(person):
    return [str(person[key]) for key in ("accountId", "key", "name") if person.get(key)]


def change_field(item):
    return (item.get("fieldId") or item["field"]).lower().replace(" ", "")


def change_values(item, field):
    if field == "assignee":
        return item.get("from") or None, item.get("to") or None
    return tuple(item.get(k + "String") or item.get(k) or "" for k in ("from", "to"))


def issue_prefix(issue):
    # Cut the timeline before inferring roles so later activity cannot identify staff.
    def comment_position(comment):
        return instant(comment["created"]), 1, int(comment["id"]), 0

    def history_position(history):
        return instant(history["created"]), 0, int(history["id"])

    comments, histories = issue["comments"], issue["histories"]
    positions = [comment_position(c) for c in comments]
    for history in histories:
        position = history_position(history)
        for index, item in enumerate(history["items"]):
            field = change_field(item)
            if field in ("assignee", "status"):
                before, after = change_values(item, field)
                if before != after:
                    positions.append((*position, index))
    if len(positions) <= MAX_EVENTS:
        return issue
    cutoff = sorted(positions)[MAX_EVENTS - 1]
    clipped = []
    for history in histories:
        position = history_position(history)
        items = [item for i, item in enumerate(history["items"]) if (*position, i) <= cutoff]
        if items:
            clipped.append(history | {"items": items})
    return issue | {
        "fields": issue["fields"] | {"assignee": None},
        "comments": [comment for comment in comments if comment_position(comment) <= cutoff],
        "histories": clipped,
        "truncated": True,
    }


def jira_case(issue):
    issue = issue_prefix(issue)
    tracker, key = issue["tracker"], issue["key"]
    url = f"{TRACKERS[tracker]}/browse/{key}"
    fields, comments, histories = issue["fields"], issue["comments"], issue["histories"]
    people = [fields.get("reporter"), fields.get("assignee")]
    people += [row.get("author") for row in comments + histories]
    aliases, automation = {}, set()
    for person in filter(None, people):
        ids = identities(person)
        if ids:
            aliases.update(dict.fromkeys(ids, ids[0]))
            if person.get("accountType") in ("app", "bot") or any(
                BOT.search(value) for value in [*ids, person.get("displayName", "")]
            ):
                automation.update(ids)

    def actor(person):
        return next((aliases[i] for i in identities(person or {})), None)

    staff = {actor(fields.get("assignee"))}
    reporter = actor(fields.get("reporter"))
    changes = []
    for history in sorted(histories, key=lambda h: (instant(h["created"]), int(h["id"]))):
        for index, item in enumerate(history["items"]):
            field = change_field(item)
            if field == "assignee":
                previous, current = change_values(item, field)
                before, after = aliases.get(previous, previous), aliases.get(current, current)
                staff.update((before, after))
            elif field in ("status", "fixversion", "fixversions"):
                staff.add(actor(history.get("author")))
                if field != "status":
                    continue
                before, after = change_values(item, field)
            else:
                continue
            if before != after:
                changes.append(
                    {
                        "id": f"{key}:{history['id']}:{index}",
                        "kind": field,
                        "at": instant(history["created"]).isoformat(),
                        "previous": before,
                        "current": after,
                    }
                )
    messages = []
    for comment in comments:
        author = actor(comment.get("author"))
        role = (
            Role.SYSTEM
            if author in automation
            else (
                Role.AGENT
                if author is not None and author != reporter and author in staff
                else Role.CUSTOMER
            )
        )
        messages.append(
            {
                "id": comment["id"],
                "kind": "comment",
                "text": comment["body"],
                "created_at": comment["created"],
                "actor_id": author or f"unknown:{comment['id']}",
                "role": role,
                "url": f"{url}?focusedCommentId={comment['id']}#comment-{comment['id']}",
            }
        )
    return {
        "id": url,
        "url": url,
        "source": "jira",
        "cohort": "long",
        "tracker": tracker,
        "project": issue["project"],
        "type": issue["type"],
        "source_comments": issue["comment_count"],
        "truncated": issue.get("truncated", False),
        "messages": messages,
        "changes": changes,
    }


def under_budget(estimated, stress):
    return estimated <= EXPECTED_CAP and stress <= STRESS_CAP


def affordable(candidates, size, apache_limit):
    # Independent lower bounds prune prefixes that cannot fit either budget.
    lower_bounds = [[(0, 0)] + [(inf, inf)] * size]
    for _, expected, stress in reversed(candidates):
        tail = lower_bounds[-1]
        lower_bounds.append(
            [(0, 0)]
            + [
                (
                    min(tail[n][0], expected + tail[n - 1][0]),
                    min(tail[n][1], stress + tail[n - 1][1]),
                )
                for n in range(1, size + 1)
            ]
        )
    lower_bounds.reverse()

    def find(start, chosen, projects, expected, stress, apache_left):
        remaining = size - len(chosen)
        if not remaining:
            if len(projects) >= MIN_PROJECTS and under_budget(expected, stress):
                return chosen
            return None
        if len(projects) + remaining < MIN_PROJECTS:
            return None
        lower_expected, lower_stress = lower_bounds[start][remaining]
        if not under_budget(expected + lower_expected, stress + lower_stress):
            return None
        for index in range(start, len(candidates) - remaining + 1):
            case, candidate_expected, candidate_stress = candidates[index]
            project = case["tracker"], case["project"]
            apache = case["tracker"] == "apache"
            if projects[project] >= MAX_PER_PROJECT or apache > apache_left:
                continue
            result = find(
                index + 1,
                [*chosen, case],
                projects + Counter({project: 1}),
                expected + candidate_expected,
                stress + candidate_stress,
                apache_left - apache,
            )
            if result is not None:
                return result
        return None

    return find(0, [], Counter(), 0, 0, apache_limit)


def short_low_signal(text):
    text = text.strip()
    return len(text) < 30 and not (
        QUESTION.search(text) or PROMISE.search(text) or UNDERTAKING.search(text)
    )


def draw_jira(snapshot, seed, estimate):
    counts = Counter(
        dict.fromkeys(
            (
                "wrong_issue_type",
                "fewer_than_100_source_comments",
                "snapshot_unavailable",
                "fewer_than_100_observed_comments",
                "missing_text_from_both_sides",
                "no_assignee_change",
                "span_under_two_weeks",
                "fewer_than_20_company_comments",
                "company_share_under_15_percent",
                "over_budget_alone",
            ),
            0,
        )
    )
    eligible = []
    for issue in snapshot["issues"]:
        if issue.get("type", "").casefold() not in ISSUE_TYPES:
            counts["wrong_issue_type"] += 1
            continue
        if issue["comment_count"] < MIN_COMMENTS:
            counts["fewer_than_100_source_comments"] += 1
            continue
        if "comments" not in issue:
            counts["snapshot_unavailable"] += 1
            continue
        case = jira_case(issue)
        comments = len(case["messages"])
        if comments < MIN_COMMENTS:
            counts["fewer_than_100_observed_comments"] += 1
            continue
        if reason := normalized(case, None):
            counts[reason] += 1
            continue
        if not any(change["kind"] == "assignee" for change in case["changes"]):
            counts["no_assignee_change"] += 1
            continue
        earliest, latest = observation(case)
        if latest - earliest < timedelta(weeks=2):
            counts["span_under_two_weeks"] += 1
            continue
        company = sum(message["role"] == Role.AGENT for message in case["messages"])
        if company < 20:
            counts["fewer_than_20_company_comments"] += 1
            continue
        if 100 * company < 15 * comments:
            counts["company_share_under_15_percent"] += 1
            continue
        projection = estimate({"cases": [case]})
        expected, stress = projection["expected_usd"], projection["stress_usd"]
        if not under_budget(expected, stress):
            counts["over_budget_alone"] += 1
            continue
        eligible.append((case | {"company_comments": company}, expected, stress))
    counts["eligible"] = len(eligible)
    eligible.sort(key=lambda row: row[0]["id"])
    random.Random(seed).shuffle(eligible)
    eligible.sort(key=lambda row: row[0]["company_comments"], reverse=True)
    apache_count = sum(case["tracker"] == "apache" for case, _, _ in eligible)
    for size in range(min(MAX_ISSUES, len(eligible)), MIN_PROJECTS - 1, -1):
        for limit in range(min(size, apache_count) + 1):
            selected = affordable(eligible, size, limit)
            if selected is not None:
                return {
                    "seed": seed,
                    "frame_counts": dict(counts),
                    "cases": selected,
                    "search_projects": snapshot["projects"],
                    "per_project": snapshot["per_project"],
                }
    raise ValueError(f"no affordable Jira draw spans {MIN_PROJECTS} projects: {dict(counts)}")


def observation(case):
    dates = [instant(item["at"]) for item in case["messages"] + case["changes"]]
    return min(dates), max(dates)


def code_log_counts(messages):
    counts = Counter(
        {"comment_bytes": 0, "code_log_bytes": 0, "code_log_lines": 0, "code_log_comments": 0}
    )
    for message in messages:
        text = message["text"]
        counts["comment_bytes"] += len(text.encode("utf-8"))
        inside = flagged = False
        for line in text.splitlines(keepends=True):
            markers = FENCE.findall(line)
            if inside or markers or LOG_LINE.search(line):
                flagged = True
                counts["code_log_lines"] += 1
                counts["code_log_bytes"] += len(line.encode("utf-8"))
            if len(markers) % 2:
                inside = not inside
        counts["code_log_comments"] += flagged
    return dict(counts)


def jira_manifest(bundle, bundle_sha):
    rows = []
    for case in bundle["cases"]:
        earliest, latest = observation(case)
        comments = len(case["messages"])
        low_signal = sum(short_low_signal(message["text"]) for message in case["messages"])
        rows.append(
            {
                "issue": case["id"].rsplit("/", 1)[1],
                "url": case["url"],
                "tracker": case["tracker"],
                "project": case["project"],
                "type": case["type"],
                "comments": comments,
                "source_comments": case["source_comments"],
                "events": comments + len(case["changes"]),
                "truncated": case["truncated"],
                "handoffs": sum(c["kind"] == "assignee" for c in case["changes"]),
                "status_changes": sum(c["kind"] == "status" for c in case["changes"]),
                "company_comments": case["company_comments"],
                "company_comment_share": round(case["company_comments"] / comments, 6),
                "short_low_signal_comments": low_signal,
                "short_low_signal_share": round(low_signal / comments, 6),
                "span_days": round((latest - earliest).total_seconds() / 86400, 2),
                **code_log_counts(case["messages"]),
            }
        )
    keys = (
        "source_snapshot_sha256",
        "seed",
        "frame_counts",
        "search_projects",
        "per_project",
    )
    return {k: bundle[k] for k in keys} | {
        "bundle_sha256": bundle_sha,
        "selected": rows,
    }
