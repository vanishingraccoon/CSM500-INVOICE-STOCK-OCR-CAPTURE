"""Start the laptop server: the portal and phone API, and the background readers.

    python -m app.serve                        # http://127.0.0.1:5000, this laptop only
    INVOICE_HOST=0.0.0.0 python -m app.serve   # reachable from the phones on the office Wi-Fi
    python -m app.serve --workers 1            # fewer readers on a slower laptop

On start it brings the database up to date, puts back any photograph a crashed run was reading, and removes
temporary files a crash left behind. Ctrl+C stops the web server, lets each reader finish or hand back its
photograph, and exits. Flask's own threaded server is used (no extra dependency): it serves the few phones and
browsers of one shop, on the office network only.
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
import threading

from app import context, db, pairing, storage, worker
from app.settings import Settings, configure


def recover(settings: Settings, run_id: str) -> dict:
    conn = context.connection()
    requeued = worker.requeue_stale(conn, run_id)
    removed = storage.remove_temp_files(settings.tmp, settings.photos, settings.thumbs, settings.reports)
    with db.transaction(conn):
        db.event(conn, "server_started", run_id=run_id, requeued=requeued, temp_files_removed=removed,
                 workers=settings.workers, host=settings.host, port=settings.port)
    return {"requeued": requeued, "temp_files_removed": removed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="overrides INVOICE_HOST")
    ap.add_argument("--port", type=int, help="overrides INVOICE_PORT")
    ap.add_argument("--workers", type=int, help="overrides INVOICE_WORKERS")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    changes = {k: v for k, v in (("host", args.host), ("port", args.port), ("workers", args.workers)) if v is not None}
    return run(settings.with_(**changes) if changes else settings)


def run(settings: Settings, stop=None, ready=None) -> int:
    """Serve until interrupted or the owning launcher's stop event is set."""
    from werkzeug.serving import make_server

    settings = configure(settings).ensure_dirs()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log = logging.getLogger("invoice.serve")

    from app.server import app
    server = make_server(settings.host, settings.port, app, threaded=True)
    run_id = secrets.token_hex(4)
    found = recover(settings, run_id)
    log.info("data in %s; %d photograph(s) put back in the queue, %d temporary file(s) removed",
             settings.app_data, found["requeued"], found["temp_files_removed"])

    pool = worker.WorkerPool(settings, settings.workers, run_id)
    log.info("portal on http://%s:%d/ ; phones pair at /pair and use %s", settings.host, settings.port,
             pairing.server_url(settings))
    if settings.host in ("127.0.0.1", "localhost"):
        log.info("listening on this laptop only; set INVOICE_HOST=0.0.0.0 for the phones on the office Wi-Fi")
    try:
        pool.start()
        pool.wait_ready(cancelled=stop)
        if ready is not None:
            ready.put({"status": "ready", "url": f"http://127.0.0.1:{settings.port}/"})
        if stop is not None:
            def wait_for_stop():
                stop.wait()
                server.shutdown()
            threading.Thread(target=wait_for_stop, name="server-stop", daemon=True).start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("stopping the readers")
        pool.shutdown()
        server.server_close()
        conn = context.connection()
        with db.transaction(conn):
            db.event(conn, "server_stopped", run_id=run_id)
        db.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
