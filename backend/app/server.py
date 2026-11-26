"""Runs the API process and closes the database on every way out (PR #40 review). The API itself is backend/app/main.py.

    python -m backend.app.server --host 0.0.0.0 --port 8000

| Case                                      | What happens                                              | Exit code |
| uncaught async error (event loop)         | log, drain requests, disconnect_db                        | 1 |
| uncaught exception in a background thread | log, drain requests, disconnect_db                        | 1 |
| uncaught exception in the main thread     | log, disconnect_db                                        | 1 |
| SIGTERM (deployment stops the pod)        | log, drain requests, disconnect_db                        | 0 |

"Drain" = stop accepting connections and let running answers finish for at most KOSIS_SHUTDOWN_TIMEOUT seconds (25, under
Kubernetes' default 30 second grace period). An error inside one request is not one of these cases: FastAPI answers it
(or the SSE `error` event does) and the server keeps running, also when the database is down.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import threading
from types import FrameType
from typing import Any

import uvicorn

from backend.app.main import app, disconnect_db

log = logging.getLogger("uvicorn.error")  # uvicorn configures this logger, so the messages are shown
STARTUP_FAILURE = 3  # uvicorn's own code


class GracefulServer(uvicorn.Server):
    """uvicorn.Server that remembers the exit code and takes SIGTERM as a normal stop (uvicorn raises it again after
    the shutdown, which would end the process before the database is closed)."""

    exit_code = 0

    async def serve(self, sockets: list | None = None) -> None:
        asyncio.get_running_loop().set_exception_handler(self.on_async_error)
        await super().serve(sockets)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if sig == signal.SIGTERM:
            log.info("SIGTERM received, shutting down gracefully")
            self.should_exit = True
        else:
            super().handle_exit(sig, frame)

    def fail(self, what: str, error: BaseException | None) -> None:
        log.error("%s: %r, shutting down", what, error, exc_info=error)
        self.exit_code = 1
        self.should_exit = True  # the main loop sees it within 0.1 s; only a flag, so any thread may set it

    def on_async_error(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        """Unhandled async error (Node: unhandledRejection)."""
        error = context.get("exception")
        # A client that goes away in the middle of a stream ends up here as an OSError: it must not stop the server.
        if error is None or isinstance(error, OSError):
            loop.default_exception_handler(context)
            return
        self.fail("Unhandled async error", error)

    def on_thread_error(self, args: threading.ExceptHookArgs) -> None:
        """Uncaught exception in a background thread (Node: uncaughtException)."""
        if args.exc_type is not SystemExit:
            self.fail("Uncaught exception in a thread", args.exc_value)


def run(server: GracefulServer) -> int:
    """Serve until told to stop, close the database, and return the process exit code."""
    previous = threading.excepthook
    threading.excepthook = server.on_thread_error
    try:
        server.run()
        if not server.started:
            return server.exit_code or STARTUP_FAILURE
        return server.exit_code
    except KeyboardInterrupt:  # Ctrl+C, after uvicorn's own graceful stop
        return 130
    except SystemExit as e:  # uvicorn could not bind the port
        return e.code if isinstance(e.code, int) else 1
    except BaseException as e:  # noqa: BLE001 - last resort, the process ends here
        log.critical("Uncaught exception: %r", e, exc_info=e)
        return 1
    finally:
        threading.excepthook = previous
        disconnect_db()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    timeout = int(os.environ.get("KOSIS_SHUTDOWN_TIMEOUT", "25"))
    code = run(GracefulServer(uvicorn.Config(app, host=args.host, port=args.port, timeout_graceful_shutdown=timeout)))
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    # not sys.exit: an answer still running in a worker thread would keep the process until it is killed
    os._exit(code)


if __name__ == "__main__":
    main()
