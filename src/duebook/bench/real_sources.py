"""Freeze public support messages and draw auditable evaluation cohorts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from ..events import Actor, AssigneeChanged, MessagePosted, Role
from .generate import Gold, SimTicket

TWCS = "https://www.kaggle.com/datasets/thoughtvector/customer-support-on-twitter"
TEAM = {"OWNER", "MEMBER", "COLLABORATOR"}
TWCS_PROMISE = re.compile(
    r"\b(?:(?:we|i)['’]ll|(?:we|i)\s+will|follow[\s-]+up|get back to you|within|"
    r"by tomorrow|24 hours?|business days?|refund|replace|send)\b",
    re.IGNORECASE,
)
SIGN_OFF = re.compile(r"[*^-]([A-Z]{2,4})\s*$")
TWCS_FILTERS = ("fewer_than_six_messages", "fewer_than_two_per_side", "no_company_promise_wording")


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


class _HashedCSV(io.FileIO):
    def __init__(self, path, hasher):
        super().__init__(path)
        self.hasher = hasher

    def readinto(self, buffer):
        size = super().readinto(buffer)
        self.hasher.update(memoryview(buffer)[:size])
        return size


def twitter(csv_path: Path, hasher, seed: int):
    # Spill text to disk so only IDs and reply components scale with the corpus in RAM.
    fields = (
        "author_id", "inbound", "created_at", "text",
        "response_tweet_id", "in_response_to_tweet_id",
    )  # fmt: skip
    with TemporaryDirectory() as scratch, closing(sqlite3.connect(Path(scratch) / "twcs.db")) as db:
        db.execute(
            "CREATE TABLE tweets (id INTEGER PRIMARY KEY, actor TEXT, inbound TEXT, "
            "created TEXT, body TEXT, replies TEXT, parent TEXT)"
        )
        try:
            with io.TextIOWrapper(
                io.BufferedReader(_HashedCSV(csv_path, hasher)), encoding="utf-8-sig", newline=""
            ) as stream:
                db.executemany(
                    "INSERT INTO tweets VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        (int(row["tweet_id"]), *(row[field] for field in fields))
                        for row in csv.DictReader(stream)
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("TWCS tweet IDs must be unique integers") from error
        yield from _twitter_threads(db, seed)


def _twitter_components(db):
    parents = {key: key for (key,) in db.execute("SELECT id FROM tweets ORDER BY id")}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for key, replies, parent in db.execute("SELECT id, replies, parent FROM tweets"):
        for linked in filter(None, [parent, *(replies or "").split(",")]):
            neighbor = int(linked)
            if neighbor in parents:
                left, right = sorted((root(key), root(neighbor)))
                parents[right] = left
    threads, roots = defaultdict(list), []
    for key in parents:
        thread = root(key)
        if key == thread:
            roots.append(key)
        else:
            threads[thread].append(key)
    return roots, threads


def _twitter_threads(db, seed):
    roots, threads = _twitter_components(db)
    random.Random(seed).shuffle(roots)
    roles = {"true": Role.CUSTOMER, "false": Role.AGENT}
    for thread in roots:
        case = {"id": f"twcs:{thread}", "url": TWCS, "source": "twcs"}
        ids = threads.pop(thread, ())
        if len(ids) < 5:
            yield case, TWCS_FILTERS[0]
            continue
        messages = []
        for key in (thread, *ids):
            actor, inbound, created, body, replies, parent = db.execute(
                "SELECT actor, inbound, created, body, replies, parent FROM tweets WHERE id = ?",
                (key,),
            ).fetchone()
            messages.append(
                {
                    "id": str(key),
                    "text": body,
                    "created_at": created,
                    "actor_id": actor,
                    "inbound": inbound,
                    "role": roles.get((inbound or "").lower()),
                    "role_basis": "derived:twcs_inbound",
                    "reply_ids": replies,
                    "parent_id": parent,
                }
            )
        counts = Counter(m["role"] for m in messages if (m["text"] or "").strip())
        if min(counts[Role.CUSTOMER], counts[Role.AGENT]) < 2:
            yield case, TWCS_FILTERS[1]
        elif not any(
            m["role"] == Role.AGENT and TWCS_PROMISE.search(m["text"] or "") for m in messages
        ):
            yield case, TWCS_FILTERS[2]
        else:
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


def _eligible(candidates, counts, exclusions):
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
            yield case
    if unseen:
        raise ValueError(f"local review exclusions absent from source frame: {sorted(unseen)}")


def draw_twitter(csv_path, hasher, seed, count, exclusions=None):
    counts = Counter(dict.fromkeys(TWCS_FILTERS, 0))
    brands = Counter()
    selected = []
    candidates = twitter(csv_path, hasher, seed)
    for case in _eligible(candidates, counts, exclusions or {}):
        accounts = {m["actor_id"] for m in case["messages"] if m["role"] == Role.AGENT}
        if len(selected) < count and all(brands[account] < 5 for account in accounts):
            brands.update(accounts)
            selected.append(case | {"cohort": "random"})
    if len(selected) != count:
        raise ValueError(f"selected only {len(selected)} of {count} TWCS cases: {dict(counts)}")
    return {
        "seed": seed,
        "frame_counts": dict(counts),
        **({"local_review_exclusions": exclusions} if exclusions else {}),
        "cases": selected,
    }


def draw(
    candidates,
    seed: int,
    random_count: int,
    challenge_count: int,
    exclusions: dict[str, str] | None = None,
) -> dict:
    counts: Counter[str] = Counter()
    eligible = list(_eligible(candidates, counts, exclusions or {}))
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
    events = []
    previous_signer = None
    for message in case["messages"]:
        event_id = f"{case['source']}:{message['id']}"
        at = instant(message["at"])
        role = Role(message["role"])
        actor = Actor(message["actor_id"], role)
        if case["source"] == "twcs" and role == Role.AGENT:
            sign_off = SIGN_OFF.search(message["text"])
            if sign_off:
                initials = sign_off[1]
                actor = Actor(f"{actor.id}:{initials}", role, initials)
                if previous_signer and previous_signer.name != initials:
                    events.append(
                        AssigneeChanged(
                            id=f"{event_id}:handoff",
                            ticket_id=case["id"],
                            seq=len(events) + 1,
                            at=at,
                            previous=previous_signer.id,
                            current=actor.id,
                        )
                    )
            previous_signer = actor if sign_off else None
        events.append(
            MessagePosted(
                id=event_id,
                ticket_id=case["id"],
                seq=len(events) + 1,
                at=at,
                author=actor,
                text=message["text"],
            )
        )
    if any(before.at > after.at for before, after in zip(events, events[1:], strict=False)):
        raise ValueError("bundle messages are out of order")
    return SimTicket(case["id"], tuple(events), (), Gold(), events[-1].at)


def source_messages(case, ticket):
    return {
        event.seq: message
        for event, message in zip(
            (e for e in ticket.events if isinstance(e, MessagePosted)),
            case["messages"],
            strict=True,
        )
    }


def twitter_manifest(bundle, bundle_sha):
    selected = []
    for case in bundle["cases"]:
        handoffs = sum(isinstance(e, AssigneeChanged) for e in to_ticket(case).events)
        messages = case["messages"]
        selected.append(
            {
                "thread_id": case["id"],
                "tweet_ids": [m["id"] for m in messages],
                "brand_ids": sorted({m["actor_id"] for m in messages if m["role"] == Role.AGENT}),
                "messages": len(messages),
                "handoffs": handoffs,
            }
        )
    return {
        "source": TWCS,
        "source_snapshot_sha256": bundle["source_snapshot_sha256"],
        "bundle_sha256": bundle_sha,
        "seed": bundle["seed"],
        "frame_counts": bundle["frame_counts"],
        "threads_with_handoffs": sum(thread["handoffs"] > 0 for thread in selected),
        "selected": selected,
    }
