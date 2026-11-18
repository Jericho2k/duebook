# An unreadable message is recorded, not held forever

Status **Accepted by the owner** in chat on 2026-11-18, as relayed by the
Round 7 task brief.

## Context

In Round 6 the extractor answered one message with JSON that looped until
the output limit, on every attempt. The pipeline held both tickets at that
message for good: the 11 promises after it were never read, and at
temperature 0 every retry paid for the same failure.

## Options

1. Keep holding. The card stops, every later promise is lost, retries are paid.
2. Apply the message with no proposals after a few failures. That turns a
   failure into "nothing found": the message is silently unread.
3. Apply it with an explicit unreadable record that a person must check,
   and alert the assignee.

We take 3.

## Rule

- An attempt reads the message. If the answer is cut off, empty or not JSON,
  the same attempt reads it once more with reasoning "low".
- A failed attempt holds the ticket with the existing backoff.
- When the third attempt at a message ends in an unreadable answer, the event
  is applied with an `Unreadable` record: the whole message and the error.
  The ticket continues with the next message.
- The card holds it as an `Unread` item with its source. The brief leads with
  "Сообщение №N не удалось прочитать — проверьте его вручную". `lifecycle`
  derives an `unreadable` alert, keyed by the item, which the outbox sends to
  the current assignee.
- Timeouts, rate limits and outages do not count: they say nothing about the
  message, and the ticket waits as before.

## Consequences

- No ticket stops on one message. An unreadable message costs at most six
  calls, about $0.006 at the 800- and 4,000-token caps.
- What the message said is not on the card until a person reads it; the item
  and the alert say so. The item stays on the card: there is no way yet to
  mark it checked.
- The record is stored with the event like a proposal, so the card still
  rebuilds without a model. Migration 0007 lets an outbox row name a message
  instead of a commitment.
