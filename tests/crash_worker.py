"""A delivery worker run as a real process, so a test can SIGKILL it.

    python crash_worker.py <dsn> <name>

It sends to a stand-in for an external channel: two tables written through a
separate autocommit connection, so a send is NOT part of the outbox
transaction — exactly the situation with a real messenger or mail API.

* channel_attempts logs every send the channel received;
* channel_inbox is what the recipient sees: the channel honours the notice key
  as an idempotency key and shows each alert once.

The short sleeps widen the windows a kill can land in.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta

from duebook.store import Store, Worker, connect


class TableChannel:
    def __init__(self, dsn: str, worker: str) -> None:
        self.conn = connect(dsn)
        self.worker = worker

    def send(self, notice) -> None:
        time.sleep(0.002)
        self.conn.execute(
            "insert into channel_attempts (key, worker) values (%s, %s)", (notice.key, self.worker)
        )
        self.conn.execute(
            "insert into channel_inbox (key) values (%s) on conflict do nothing", (notice.key,)
        )
        time.sleep(0.002)


def main() -> None:
    dsn, name = sys.argv[1], sys.argv[2]
    store = Store(connect(dsn))
    store.outbox.claim_ttl = timedelta(seconds=float(sys.argv[3]))
    worker = Worker(store, TableChannel(dsn, name), name, batch=5)
    print("ready", flush=True)
    while True:
        if not worker.run_once(datetime.now(UTC)).claimed:
            time.sleep(0.02)


if __name__ == "__main__":
    main()
