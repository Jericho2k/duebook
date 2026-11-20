"""Freeze public support messages and draw auditable evaluation cohorts."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from ..events import Actor, MessagePosted, Role
from .generate import Gold, SimTicket

TWCS = "https://www.kaggle.com/datasets/thoughtvector/customer-support-on-twitter"
TEAM = {"OWNER", "MEMBER", "COLLABORATOR"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_frozen(path: Path):
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def write(path: Path, value) -> None:
    # Write once and atomically publish the complete file. A failed run leaves no partial JSON.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        path.hardlink_to(temporary)
    finally:
        if temporary is not None:
            temporary.unlink()


def instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    if parsed.utcoffset() is None:
        raise ValueError("timestamp without offset")
    return parsed.astimezone(UTC)


def github(frame: Path, comments: Path):
    pages = read(frame)
    issues = [issue for page in pages for issue in page["items"]]
    if any(
        page["incomplete_results"] or page["total_count"] != len(issues) for page in pages
    ) or len({i["id"] for i in issues}) != len(issues):
        raise ValueError("GitHub frame is partial or has duplicate issues")
    for issue in issues:
        if "pull_request" in issue:
            raise ValueError("GitHub frame contains a pull request")
        url = issue["html_url"]
        case = {"id": url, "url": url, "source": "github", "title": issue.get("title")}
        path = comments / f"{issue['number']}.json"
        if not path.is_file():
            yield case, "missing_comment_snapshot"
            continue
        replies, case["comment_snapshot_sha256"] = read_frozen(path)
        if (
            len(replies) != issue["comments"]
            or len({r["id"] for r in replies}) != len(replies)
            or any(r["issue_url"] != issue["url"] for r in replies)
        ):
            yield case, "comment_snapshot_differs_from_frame"
            continue
        messages = []
        reporter = (issue.get("user") or {}).get("id")
        for index, post in enumerate([issue, *replies]):
            author = post.get("user") or {}
            association = post.get("author_association")
            if author.get("type") == "Bot":
                role, basis = Role.BOT, "observed:github_user_type"
            elif author.get("type") == "User" and author.get("id") == reporter:
                role, basis = Role.CUSTOMER, "inferred:issue_reporter"
            elif author.get("type") == "User" and association:
                role = Role.AGENT if association in TEAM else Role.CUSTOMER
                basis = "inferred:author_association"
            else:
                role, basis = None, None
            messages.append(
                {
                    "id": str(post["id"]),
                    "url": post["html_url"],
                    "kind": "comment" if index else "issue",
                    "text": post.get("body"),
                    "created_at": post.get("created_at"),
                    "updated_at": post.get("updated_at"),
                    "actor_id": str(author["id"]) if "id" in author else None,
                    "login": author.get("login"),
                    "user_type": author.get("type"),
                    "author_association": association,
                    "role": role,
                    "role_basis": basis,
                }
            )
        case["messages"] = messages
        yield case, None


def twitter(csv_path: Path):
    # Keep source-local reply IDs, including edges whose endpoints are missing from the export.
    rows, parents = {}, {}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            key = int(row["tweet_id"])
            if key in rows:
                raise ValueError(f"duplicate TWCS tweet_id {key}")
            rows[key] = (
                row["author_id"],
                row["inbound"],
                row["created_at"],
                row["text"],
                row["response_tweet_id"],
                row["in_response_to_tweet_id"],
            )
            parents[key] = key
    for key, (_, _, _, _, replies, parent) in rows.items():
        for linked in filter(None, [parent, *replies.split(",")]):
            neighbor = int(linked)
            if neighbor in parents:
                left, right = sorted((root(key), root(neighbor)))
                parents[right] = left
    threads = defaultdict(list)
    for key in rows:
        threads[root(key)].append(key)
    for thread, ids in threads.items():
        case = {"id": f"twcs:{thread}", "url": TWCS, "source": "twcs"}
        if len(ids) < 2:
            yield case, "single_message"
            continue
        messages = []
        for key in ids:
            actor, inbound, created, body, replies, parent = rows[key]
            role = {"true": Role.CUSTOMER, "false": Role.AGENT}.get((inbound or "").lower())
            messages.append(
                {
                    "id": str(key),
                    "text": body,
                    "created_at": created,
                    "actor_id": actor,
                    "inbound": inbound,
                    "role": role,
                    "role_basis": "derived:twcs_inbound",
                    "reply_ids": replies,
                    "parent_id": parent,
                }
            )
        yield case | {"messages": messages}, None


def normalized(case, exclusion):
    if exclusion:
        return exclusion
    messages = case["messages"]
    if any(not m["actor_id"] or not m["role"] for m in messages):
        return "unknown_actor_or_role"
    if any(not isinstance(m["text"], str) for m in messages):
        return "missing_message_text"
    if not {Role.CUSTOMER, Role.AGENT} <= {m["role"] for m in messages if m["text"].strip()}:
        return "missing_text_from_both_sides"
    try:
        for m in messages:
            m["at"] = instant(m["created_at"]).isoformat()
    except (ValueError, TypeError):
        return "missing_or_unusable_timestamp"
    messages.sort(key=lambda m: (instant(m["at"]), m.get("kind") != "issue", int(m["id"])))
    if case["source"] == "github" and messages[0]["kind"] != "issue":
        return "comment_precedes_issue"
    return None


def span(case) -> timedelta:
    messages = case["messages"]
    return instant(messages[-1]["at"]) - instant(messages[0]["at"])


def draw(
    candidates,
    seed: int,
    random_count: int,
    challenge_count: int,
    exclusions: dict[str, str] | None = None,
) -> dict:
    counts: Counter[str] = Counter()
    eligible = []
    exclusions = exclusions or {}
    unseen = set(exclusions)
    for case, exclusion in candidates:
        if case["id"] in exclusions:
            if not exclusions[case["id"]].strip():
                raise ValueError("a local review exclusion needs a reason")
            unseen.discard(case["id"])
            counts["local_review_exclusion"] += 1
            continue
        reason = normalized(case, exclusion)
        counts[reason or "eligible"] += 1
        if reason is None:
            eligible.append(case)
    if unseen:
        raise ValueError(f"local review exclusions absent from source frame: {sorted(unseen)}")
    if len(eligible) < random_count:
        raise ValueError(f"only {len(eligible)} eligible cases for {random_count} random cases")
    eligible.sort(key=lambda case: case["id"])
    sampled = random.Random(seed).sample(eligible, random_count)
    chosen = {case["id"] for case in sampled}
    challenge = [
        case
        for case in eligible
        if case["id"] not in chosen
        and len(case["messages"]) >= 8
        and span(case) >= timedelta(days=7)
    ]
    challenge.sort(key=lambda case: (-span(case), -len(case["messages"]), case["id"]))
    if len(challenge) < challenge_count:
        raise ValueError(f"only {len(challenge)} long cases for {challenge_count} challenge cases")
    return {
        "seed": seed,
        "frame_counts": dict(counts),
        "eligible_remaining_long": len(challenge),
        **({"local_review_exclusions": exclusions} if exclusions else {}),
        "cases": [
            case | {"cohort": cohort}
            for cohort, cases in (("random", sampled), ("challenge", challenge[:challenge_count]))
            for case in cases
        ],
    }


def to_ticket(case) -> SimTicket:
    events = tuple(
        MessagePosted(
            id=f"{case['source']}:{m['id']}",
            ticket_id=case["id"],
            seq=seq,
            at=instant(m["at"]),
            author=Actor(m["actor_id"], Role(m["role"])),
            text=m["text"],
        )
        for seq, m in enumerate(case["messages"], 1)
    )
    if any(before.at > after.at for before, after in zip(events, events[1:], strict=False)):
        raise ValueError("bundle messages are out of order")
    return SimTicket(case["id"], events, (), Gold(), events[-1].at)
