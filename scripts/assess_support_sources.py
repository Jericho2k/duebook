"""Profile frozen support releases locally and emit metadata without transcript text."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

THRESHOLDS = (50, 75, 100, 150, 200, 300)
ROLES = {"reporter", "assignee"}
PLACEHOLDER = re.compile(r"\bph_[a-z_]+\b", re.I)
WORD = re.compile(r"[^\W_]")
SIGNALS = {
    "request": re.compile(r"\b(?:please|could you|can you)\b", re.I),
    "follow_up": re.compile(r"\b(?:any updates?|follow[ -]up|update us|status update)\b", re.I),
    "planned_action": re.compile(r"\b(?:we|i)(?:['’]ll| will)\b", re.I),
    "relative_time": re.compile(
        r"\b(?:tomorrow|tonight|next week|next month|within\s+\d+|by\s+(?:"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
        re.I,
    ),
    "diagnostics": re.compile(r"\b(?:logs?|check|reproduc\w*|trace|deploy\w*)\b", re.I),
    "resolution": re.compile(r"\b(?:resolved|fixed|closed|completed|working now)\b", re.I),
    "approval": re.compile(r"\b(?:confirm|approved|acceptance|validate)\b", re.I),
}
PRIVACY = {
    "email_like": re.compile(r"\b[\w.%+-]+@[\w.-]+\.[a-z]{2,}\b", re.I),
    "ipv4_like": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "phone_format_like": re.compile(r"(?<!\w)\+\d{1,3}(?:[ .-]+\d{2,5}){2,5}(?!\w)"),
}
CALLCENTRE_SAMPLE = "customer_service_general_inbound.zip"


def json_file(path):
    return json.loads(path.read_bytes())


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def distribution(lengths):
    cuts = statistics.quantiles(lengths, n=100, method="inclusive")
    return {
        "min": min(lengths),
        "median": statistics.median(lengths),
        **{f"p{p}": cuts[p - 1] for p in (75, 90, 95, 99)},
        "max": max(lengths),
        "at_least": {str(n): sum(x >= n for x in lengths) for n in THRESHOLDS},
        "in_100_to_300": sum(100 <= x <= 300 for x in lengths),
    }


def publisher_files(root):
    files = {}
    for record in json_file(root / "publisher-files.json"):
        name = record["filename"]
        reference = record["content_details"]
        actual = digest(root / name)
        if actual != reference["sha256_hash"]:
            raise ValueError(f"publisher checksum mismatch for {name}")
        files[name] = {"sha256": actual, "bytes": (root / name).stat().st_size}
    return files


def sample_workbook_ids(path):
    with ZipFile(path) as book:
        sheet = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))
        strings = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
    cells = []
    for row in sheet.findall(".//{*}row"):
        cell = row.find(f"{{*}}c[@r='A{row.get('r')}']")
        if cell is None:
            continue
        value = cell.findtext("{*}v")
        if value:
            cells.append("".join(strings[int(value)].itertext()) if cell.get("t") == "s" else value)
    if cells.pop(0) != "id":
        raise ValueError("unexpected sample workbook first column")
    return cells


def assignment_history(root):
    counters = Counter()
    assignments = defaultdict(list)
    seen = set()
    for row in csv_rows(root / "issues_change_history.csv"):
        counters["rows"] += 1
        counters["duplicate_history_ids"] += row["id"] in seen
        seen.add(row["id"])
        counters[f"{row['field']}_rows"] += 1
        if row["field"] == "assignee":
            assignments[row["issueid"]].append(
                (
                    datetime.fromisoformat(row["created"]),
                    int(row["id"].removesuffix(".0")),
                    row["value"].strip(),
                )
            )
    handoffs = {
        ticket: sum(
            bool(before[2] and after[2] and before[2] != after[2])
            for before, after in pairwise(sorted(events))
        )
        for ticket, events in assignments.items()
    }
    return dict(counters) | {
        "tickets_with_assignee_history": len(assignments),
        "tickets_with_handoffs": sum(bool(n) for n in handoffs.values()),
        "handoffs": sum(handoffs.values()),
        "distinct_nonempty_assignee_masks": len(
            {event[2] for events in assignments.values() for event in events if event[2]}
        ),
    }, handoffs


def released_comments(root):
    groups = defaultdict(list)
    for row in csv_rows(root / "sample_utterances.csv"):
        groups[row["issueid"], row["id"]].append(row)

    checks = Counter()
    checks["utterance_rows"] = sum(map(len, groups.values()))
    by_ticket = defaultdict(list)
    for (ticket, comment_id), fragments in groups.items():
        fragments.sort(key=lambda row: int(row["utr_seq"]))
        fields = ("author", "author_role", "created", "is_private", "comment_seq")
        if any(len({row[key] for row in fragments}) != 1 for key in fields):
            raise ValueError(f"conflicting metadata for comment {comment_id}")
        first = fragments[0]
        sequence = [int(row["utr_seq"]) for row in fragments]
        checks["duplicate_fragment_keys"] += len(sequence) - len(set(sequence))
        checks["comments_with_fragment_sequence_gap"] += set(sequence) != set(range(len(sequence)))
        checks["blank_utterance_rows"] += sum(not row["actionbody"].strip() for row in fragments)
        body = " ".join(row["actionbody"].strip() for row in fragments).strip()
        placeholders = {x.lower() for x in PLACEHOLDER.findall(body)}
        checks["blank_grouped_bodies"] += not body
        checks["placeholder_comments"] += bool(placeholders)
        readable = bool(WORD.search(PLACEHOLDER.sub("", body)))
        visible = first["is_private"] == "0.0"
        checks["private_comments"] += not visible
        checks["visible_comments_without_readable_words"] += visible and not readable
        for name, pattern in PRIVACY.items():
            checks[f"{name}_comments"] += bool(pattern.search(body))
        created = datetime.fromisoformat(first["created"]) if first["created"] else None
        checks["missing_comment_timestamp"] += created is None
        checks["timestamps_without_offset"] += bool(created and created.utcoffset() is None)
        checks["missing_author"] += not first["author"].strip()
        checks["missing_role"] += not first["author_role"].strip()
        by_ticket[ticket].append(
            {
                "id": comment_id,
                "author": first["author"],
                "role": first["author_role"],
                "created": created,
                "seq": int(first["comment_seq"]),
                "visible": visible,
                "readable": readable,
                "fragment_gap": set(sequence) != set(range(len(sequence))),
                "placeholders": placeholders,
                "body": body,
            }
        )
    return by_ticket, checks


def assess_mendeley(root, manifest):
    version = json_file(root / "release.json")
    if version["version"] != 3 or version["id"] != "btm76zndnt":
        raise ValueError("expected the original Mendeley v3 release")
    files = publisher_files(root)
    issues = {row["id"]: row for row in csv_rows(root / "issues.csv")}
    sample_ids = sample_workbook_ids(root / "issues_snapshot_sample.xlsx")
    history, handoffs = assignment_history(root)
    comments, checks = released_comments(root)
    normalized_sample = {value.removesuffix(".0") for value in sample_ids}
    if set(comments) - issues.keys() or normalized_sample != {
        value.removesuffix(".0") for value in comments
    }:
        raise ValueError("issue, utterance and workbook populations do not reconcile")

    released_lengths, visible_lengths, known_lengths = [], [], []
    released_roles, visible_roles = Counter(), Counter()
    participants, exclusions = set(), Counter()
    candidates, coverage = [], Counter()
    for issue_id, row in issues.items():
        group = sorted(comments.get(issue_id, []), key=lambda m: m["seq"])
        n = len(group)
        if n:
            released_lengths.append(n)
            seq = [m["seq"] for m in group]
            checks["tickets_with_comment_sequence_gap"] += seq != list(range(n))
            times = [m["created"] for m in group if m["created"]]
            checks["tickets_with_reversed_created_order"] += any(a > b for a, b in pairwise(times))
            keys = {(m["author"], m["created"], m["body"]) for m in group}
            checks["same_ticket_author_time_payload_excess"] += n - len(keys)
            coverage[f"released_minus_metadata_{n - int(row['issue_comments_count']):+d}"] += 1
        visible = [m for m in group if m["visible"] and m["readable"]]
        known = [m for m in visible if m["role"] in ROLES]
        both = {m["role"] for m in known} >= ROLES
        coverage["readable_two_sided"] += both
        coverage["known_roles_visible_100_to_300"] += both and 100 <= len(known) <= 300
        coverage["visible_100_to_300_with_both_roles"] += both and 100 <= len(visible) <= 300
        if n:
            visible_lengths.append(len(visible))
            known_lengths.append(len(known))
        released_roles.update(m["role"] for m in group)
        visible_roles.update(m["role"] for m in visible)
        participants.update(m["author"] for m in group if m["author"])
        reasons = []
        if not group:
            reasons.append("no_text_released")
        else:
            if len(visible) < 100:
                reasons.append("under_100_readable_customer_visible_comments")
            if not both:
                reasons.append("requester_assignee_exchange_not_observed")
            if len(known) < 100 <= len(visible):
                reasons.append("under_100_attributed_requester_assignee_comments")
            reasons.append("only_curated_fragments_original_completeness_unverified")
        exclusions.update(reasons)
        entry = {
            "ticket_id": issue_id,
            "ticket_type": row["issue_type"],
            "reported_comment_count": int(row["issue_comments_count"]),
            "released_comment_count": n,
            "readable_visible_comment_count": len(visible),
            "visible_roles": dict(Counter(m["role"] for m in visible)),
            "readable_known_side_count": len(known),
            "private_comments": sum(not m["visible"] for m in group),
            "unique_released_author_masks": len({m["author"] for m in group if m["author"]}),
            "handoffs": handoffs.get(issue_id, 0),
            "exclusion_reasons": reasons,
        }
        if n:
            entry["comment_ids"] = [m["id"] for m in group]
        if n >= 100:
            entry["comments_with_fragment_gaps"] = sum(m["fragment_gap"] for m in group)
            entry["visible_placeholder_counts"] = dict(
                Counter(token for m in visible for token in sorted(m["placeholders"]))
            )
            entry["visible_workflow_markers"] = {
                role: {
                    name: sum(bool(pattern.search(m["body"])) for m in known if m["role"] == role)
                    for name, pattern in SIGNALS.items()
                }
                for role in sorted(ROLES)
            }
            entry["visible_known_role_switches"] = sum(
                a["role"] != b["role"] for a, b in pairwise(known)
            )
            candidates.append({k: v for k, v in entry.items() if k != "comment_ids"})
        manifest.write(json.dumps(entry, sort_keys=True) + "\n")

    return {
        "doi": "10.17632/btm76zndnt.3",
        "license": version["data_licence"]["short_name"],
        "files": files,
        "archive_sha256": digest(root / "help-desk-tickets-v3.zip"),
        "publisher_index_sha256": digest(root / "publisher-files.json"),
        "ticket_count": len(issues),
        "tickets_with_released_text": len(comments),
        "sample_workbook_explicit_issue_ids": len(sample_ids),
        "checks": dict(checks),
        "coverage": dict(coverage),
        "exclusion_reason_counts": dict(exclusions),
        "published_metadata_all_tickets": distribution(
            [int(row["issue_comments_count"]) for row in issues.values()]
        ),
        "released_comment_ids_text_sample": distribution(released_lengths),
        "readable_visible_comments_text_sample": distribution(visible_lengths),
        "readable_visible_known_sides_text_sample": distribution(known_lengths),
        "released_comments_by_role": dict(released_roles),
        "readable_visible_comments_by_role": dict(visible_roles),
        "unique_released_author_masks": len(participants),
        "history": history,
        "verified_qualifying": 0,
        "length_screen_candidates": sorted(candidates, key=lambda x: -x["released_comment_count"]),
    }


def assess_csds(root):
    counts, sources = Counter(), {}
    sessions, lengths = set(), []
    two_sided = 0
    for split in ("train", "val", "test"):
        path = root / f"{split}.json"
        rows = json_file(path)
        sources[split] = {"sha256": digest(path), "dialogs": len(rows)}
        for row in rows:
            if row["Session_id"] in sessions:
                raise ValueError("duplicate CSDS session")
            sessions.add(row["Session_id"])
            dialogue = row["Dialogue"]
            counts.update(turn["speaker"] for turn in dialogue)
            lengths.append(len(dialogue))
            two_sided += {"A", "Q"} <= {
                turn["speaker"] for turn in dialogue if turn["utterance"].strip()
            }
    if max(lengths) >= 100:
        raise ValueError("CSDS release has new length-screen candidates requiring an audit")
    return {
        "files": sources,
        "dialogues": len(sessions),
        "two_sided": two_sided,
        "entries_by_speaker": dict(counts),
        "released_dialogue_entries": distribution(lengths),
        "verified_qualifying": 0,
    }


def assess_callcenteren(root):
    archive = root / CALLCENTRE_SAMPLE
    schemas = Counter()
    speaker_labels = Counter()
    calls_with_labels = 0
    word_fields = set()
    with ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if name.endswith(".json") and not name.startswith("__MACOSX/"):
                data = json.loads(bundle.read(name))
                schemas[tuple(sorted(data))] += 1
                words = data.get("words") or []
                word_fields.update(k for word in words for k in word)
                labels = Counter(word.get("speaker") or "" for word in words)
                speaker_labels.update(labels)
                calls_with_labels += any(labels.keys() - {""})
    if calls_with_labels:
        raise ValueError("speaker-attributed calls require a separate turn and role audit")
    return {
        "archive": archive.name,
        "sha256": digest(archive),
        "json_records": sum(schemas.values()),
        "record_schemas": [
            {"keys": list(keys), "records": n} for keys, n in sorted(schemas.items())
        ],
        "word_keys": sorted(word_fields),
        "word_rows_without_speaker": speaker_labels[""],
        "calls_with_nonblank_speaker": calls_with_labels,
        "verified_qualifying_in_archive": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    root, output = args.source_root, args.output_directory
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (output / "mendeley-ticket-manifest.jsonl").open("x", encoding="utf-8") as manifest:
        summary = {
            "mendeley": assess_mendeley(root / "mendeley-v3", manifest),
            "csds": assess_csds(root / "csds"),
            "callcenteren": assess_callcenteren(root / "callcenteren"),
        }
    # Ticket identifiers stay in the private manifests.
    candidates = summary["mendeley"]["length_screen_candidates"]
    (output / "mendeley-length-screen.json").write_text(
        json.dumps(candidates, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for row in candidates:
        del row["ticket_id"]
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
