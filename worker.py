"""The background worker, as its own process.

Spec 9 puts the worker on a separate Render background worker so heavy jobs
never block the API. Detection plus significance testing plus forecasting takes
tens of seconds; sharing a process with the web server means requests queue
behind an ARIMA fit.

Locally the API runs its worker in-thread, which is simpler and fine. Set
``BUSYLAB_INLINE_WORKER=0`` on the web service in production so only this
process claims jobs.

Safe to run alongside the API, and safe to run several of: the Postgres
``claim`` uses ``SELECT ... FOR UPDATE SKIP LOCKED``, so concurrent workers
each take a different job rather than colliding on the same one.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

from api.handlers import build_handlers
from api.jobs import Worker, open_store
from api.storage import store_from_env

logging.basicConfig(
    level=os.environ.get("BUSYLAB_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("busylab.worker")


def main() -> int:
    store = open_store()
    files = store_from_env()
    worker = Worker(store, build_handlers(files), poll_seconds=2.0)

    log.info(
        "worker starting (store=%s, files=%s)",
        type(store).__name__,
        files.name,
    )

    stopping = False

    def shutdown(signum, _frame):
        nonlocal stopping
        # Finish the job in hand rather than abandoning it half-written.
        log.info("signal %s received, finishing current job then stopping", signum)
        stopping = True
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except (ValueError, AttributeError):
            pass  # not available on every platform

    worker.start()
    try:
        while not stopping:
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()

    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
