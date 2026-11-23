"""Reconstruct the two privately selected Mendeley v3 cases from local release files."""

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .real_sources import digest, instant, write

DOI = "https://doi.org/10.17632/btm76zndnt.3"
SOURCES = ("sample_utterances.csv", "issues.csv", "issues_change_history.csv")
KNOWN = {"reporter": "customer", "assignee": "agent"}
REDACTIONS = {
    "email": re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I),
    "network": re.compile(r"(?:https?://[^\s<>]+|\b(?:\d{1,3}\.){3}\d{1,3}\b)", re.I),
    "phone": re.compile(r"(?<!\w)\+\d(?:[ .()-]*\d){8,14}(?!\d)"),
    "credential": re.compile(
        r"\b(?:password|passwd|secret|token|api[ _-]?key)\s*[:=]\s*[^\s,;]+", re.I
    ),
}


def rows(path, key, ids):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return [row for row in csv.DictReader(source) if row[key] in ids]


def ordinal(value):
    return int(value.removesuffix(".0"))


def reconstruct(groups):
    comments = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: ordinal(item["utr_seq"]))
        row = ordered[0]
        fields = ("id", "comment_seq", "created", "author", "author_role", "is_private")
        if any(any(item[field] != row[field] for field in fields) for item in ordered):
            raise ValueError("conflicting source fragment metadata")
        start = ordinal(row["utr_seq"])
        if start not in (0, 1) or [ordinal(r["utr_seq"]) for r in ordered] != list(
            range(start, start + len(ordered))
        ):
            raise ValueError("missing or duplicate Mendeley fragment")
        if row["is_private"] not in ("0.0", "1.0"):
            raise ValueError("unknown Mendeley visibility flag")
        if row["is_private"] == "1.0":
            continue
        comments.append({
            "id": row["id"], "comment_seq": row["comment_seq"], "at": row["created"],
            "actor_id": row["author"], "author_role": row["author_role"],
            "text": " ".join(r["actionbody"] for r in ordered),
        })
    return sorted(comments, key=lambda m: (instant(m["at"]), ordinal(m["comment_seq"])))


def timeline(history, comments):
    previous = {"status": None, "assignee": None}
    first, last = instant(comments[0]["at"]), instant(comments[-1]["at"])
    changes = []
    for row in sorted(history, key=lambda r: (instant(r["created"]), ordinal(r["id"]))):
        kind = row["field"]
        if kind not in previous:
            continue
        current = row["value"]
        at = instant(row["created"])
        before = previous[kind]
        previous[kind] = current
        if first <= at <= last and current != before and (kind == "assignee" or before):
            changes.append({
                "id": row["id"], "kind": kind, "at": row["created"],
                "previous": before, "current": current,
            })
    return changes


def prepare(source: Path, manifest: Path, selection: dict, bundle_path: Path, review_path: Path):
    redaction_patterns = {
        entry["kind"]: re.compile(entry["pattern"], re.I) for entry in selection.get("redact", [])
    } | REDACTIONS
    selected = {alias: spec for alias, spec in selection.items() if alias != "redact"}
    ids = {case["id"] for case in selected.values()}
    with manifest.open(encoding="utf-8") as stream:
        frame = {row["ticket_id"]: row for line in stream
                 if (row := json.loads(line))["ticket_id"] in ids}
    paths = [source / name for name in SOURCES]
    fingerprints = {path.name: digest(path) for path in [*paths, manifest]}
    issues = {r["id"]: r for r in rows(paths[1], "id", ids)}
    utterances = defaultdict(lambda: defaultdict(list))
    for row in rows(paths[0], "issueid", ids):
        utterances[row["issueid"]][row["id"]].append(row)
    history = defaultdict(list)
    for row in rows(paths[2], "issueid", ids):
        history[row["issueid"]].append(row)
    cases, review = [], []
    for alias, spec in selected.items():
        key = spec["id"]
        if set(utterances[key]) != set(frame[key]["comment_ids"]):
            raise ValueError(f"{alias} does not match the original candidate manifest")
        visible = reconstruct(utterances[key])
        if len(visible) != spec["visible"] or any(not m["text"].strip() for m in visible):
            raise ValueError(f"unexpected readable comment count for {alias}")
        messages, excluded = [], Counter()
        redactions = Counter()
        assignees = {r["value"] for r in history[key] if r["field"] == "assignee"}
        for comment in visible:
            author, role = comment["actor_id"], comment["author_role"]
            # Per-message publisher roles must agree with this issue's actor metadata.
            known = (role == "reporter" and author == issues[key]["issue_reporter"]) or (
                role == "assignee" and author in assignees
            )
            if not known:
                excluded[role] += 1
                continue
            body = comment["text"]
            for name, pattern in redaction_patterns.items():
                body, count = pattern.subn(f"[redacted {name}]", body)
                if count:
                    redactions[name] += count
            messages.append(comment | {"role": KNOWN[role], "text": body})
        changes = timeline(history[key], visible)
        base = {"id": alias, "source": "mendeley", "cohort": "exploratory", "url": DOI,
                "observed_at": visible[-1]["at"], "changes": changes}
        cases.append(base | {"messages": messages})
        review.append(base | {
            "release_id": key, "messages": visible,
            "counts": {"private": len(utterances[key]) - len(visible),
                       "withheld_roles": dict(excluded), "redactions": dict(redactions)},
        })
    if fingerprints != {path.name: digest(path) for path in [*paths, manifest]}:
        raise ValueError("Mendeley source changed while preparing the cases")
    provenance = {"schema": 1, "source_files_sha256": fingerprints}
    write(bundle_path, provenance | {"cases": cases})
    write(review_path, provenance | {"bundle_sha256": digest(bundle_path), "cases": review})
