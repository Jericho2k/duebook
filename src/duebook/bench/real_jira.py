"""Snapshot public Jira conversations and draw long support cases."""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import zip_longest
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
BOT = re.compile(r"\b(?:bot|automation)\b|\b(?:jira|atlassian|redhat|apache)bot\b", re.I)
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
PER_PROJECT = 20
DRAW_SIZE = 15


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
    return cached(
        cache / f"{tracker}-{project}.json",
        lambda: get_json(
            tracker,
            route,
            jql=f"project = {project} ORDER BY votes DESC, key ASC",
            maxResults=PER_PROJECT,
            fields="project,created,updated,comment",
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
    embedded = entry["fields"]["comment"]
    issue = {"tracker": tracker, "project": project, "key": key, "comment_count": embedded["total"]}
    if embedded["total"] < 50:
        return issue
    route = f"/rest/api/2/issue/{key}"
    try:
        detail = cached(
            cache / f"{tracker}-{key}-issue.json",
            lambda: get_json(
                tracker, route, fields="project,created,reporter,assignee", expand="changelog"
            ),
        )
        if detail["key"] != key or detail["fields"]["project"]["key"] != project:
            raise ValueError("Jira issue moved during snapshot")
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


def jira_case(issue):
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
            field = (item.get("fieldId") or item["field"]).lower().replace(" ", "")
            if field == "assignee":
                before, after = (aliases.get(item.get(k), item.get(k)) for k in ("from", "to"))
                staff.update((before, after))
            elif field in ("status", "fixversion", "fixversions"):
                staff.add(actor(history.get("author")))
                if field != "status":
                    continue
                before, after = (
                    item.get(k + "String") or item.get(k) or "" for k in ("from", "to")
                )
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
        "messages": messages,
        "changes": changes,
    }


def project_draw(cases, seed):
    rng = random.Random(seed)
    groups = defaultdict(list)
    for case in sorted(cases, key=lambda c: c["id"]):
        groups[case["tracker"], case["project"]].append(case)
    keys = sorted(groups)
    rng.shuffle(keys)
    keys.sort(key=lambda key: min(5, len(groups[key])), reverse=True)

    def priority(case):
        return case["tracker"] == "apache", len(case["messages"]) < 100, not case["undertakings"]

    for group in groups.values():
        rng.shuffle(group)
        group.sort(key=priority)
    preferred = [
        next((k for k in keys if k[0] == tracker), None) for tracker in ("atlassian", "redhat")
    ]
    balanced = list(dict.fromkeys([*(k for k in preferred if k), *keys]))[:5]
    if sum(min(5, len(groups[k])) for k in balanced) < DRAW_SIZE:
        balanced = keys[:5]
    rows = [c for batch in zip_longest(*(groups[k][:5] for k in balanced)) for c in batch if c]
    rows.sort(key=priority)
    preferred_first = [
        next((c for c in rows if c["tracker"] == tracker), None)
        for tracker in ("atlassian", "redhat")
    ]
    first = [c for c in preferred_first if c]
    return [*first, *(c for c in rows if c not in first)][:DRAW_SIZE]


def draw_jira(snapshot, seed):
    counts = Counter(
        dict.fromkeys(
            (
                "fewer_than_50_comments",
                "snapshot_unavailable",
                "no_assignee_change",
                "span_under_two_weeks",
                "without_company_undertaking",
                "eligible_100_plus",
                "eligible_50_to_99",
            ),
            0,
        )
    )
    eligible = []
    for issue in snapshot["issues"]:
        if issue["comment_count"] < 50:
            counts["fewer_than_50_comments"] += 1
            continue
        if "comments" not in issue:
            counts["snapshot_unavailable"] += 1
            continue
        case = jira_case(issue)
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
        undertakings = sum(
            bool(UNDERTAKING.search(m["text"])) for m in case["messages"] if m["role"] == Role.AGENT
        )
        counts["without_company_undertaking"] += undertakings == 0
        counts["eligible_100_plus" if len(case["messages"]) >= 100 else "eligible_50_to_99"] += 1
        eligible.append(case | {"undertakings": undertakings})
    for pool in ([c for c in eligible if c["tracker"] != "apache"], eligible):
        for floor in (100, 50):
            selected = project_draw([c for c in pool if len(c["messages"]) >= floor], seed)
            if len(selected) == DRAW_SIZE:
                return {
                    "seed": seed,
                    "frame_counts": dict(counts),
                    "cases": selected,
                    "search_projects": snapshot["projects"],
                    "per_project": snapshot["per_project"],
                    "comment_floor": floor,
                }
    raise ValueError(f"Jira frame has {len(eligible)} eligible cases with a cap of 5 per project")


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
        rows.append(
            {
                "issue": case["id"].rsplit("/", 1)[1],
                "url": case["url"],
                "tracker": case["tracker"],
                "project": case["project"],
                "comments": len(case["messages"]),
                "handoffs": sum(c["kind"] == "assignee" for c in case["changes"]),
                "status_changes": sum(c["kind"] == "status" for c in case["changes"]),
                "undertaking_comments": case["undertakings"],
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
        "comment_floor",
    )
    return {k: bundle[k] for k in keys} | {
        "bundle_sha256": bundle_sha,
        "below_100_comments": sum(row["comments"] < 100 for row in rows),
        "selected": rows,
    }
