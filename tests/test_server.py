"""PR #40 review: backend/app/server.py closes the database on every way out, with the right exit code."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest
import uvicorn
from psycopg import OperationalError

from backend.app import main, server
from backend.app.server import GracefulServer, run


async def _app(scope, receive, send):
    """A bare ASGI app: the server under test, not the API."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            await send({"type": f"{message['type']}.complete"})
            if message["type"] == "lifespan.shutdown":
                return


def _server(port: int = 0) -> GracefulServer:
    return GracefulServer(uvicorn.Config(_app, host="127.0.0.1", port=port, log_config=None, timeout_graceful_shutdown=1))


@pytest.fixture
def closed(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(server, "disconnect_db", lambda: calls.append(1))
    return calls


def _run_with(srv: GracefulServer, action) -> int:
    """Run the server and call action(loop) on its event loop once it is up."""
    startup = srv.startup

    async def started(sockets=None):
        await startup(sockets=sockets)
        loop = asyncio.get_running_loop()
        loop.call_soon(action, loop)
        loop.call_later(10, setattr, srv, "should_exit", True)  # never hang the test run

    srv.startup = started
    return run(srv)


def test_unhandled_async_error_stops_the_server_with_code_1(closed):
    def boom(loop):
        raise OperationalError("the connection is closed")

    assert _run_with(_server(), boom) == 1
    assert closed == [1]


def test_uncaught_thread_error_stops_the_server_with_code_1(closed):
    def boom():
        raise OperationalError("server closed the connection unexpectedly")

    hook = threading.excepthook
    assert _run_with(_server(), lambda loop: threading.Thread(target=boom).start()) == 1
    assert closed == [1] and threading.excepthook is hook


def test_sigterm_is_a_normal_stop_with_code_0(closed):
    srv = _server()
    assert _run_with(srv, lambda loop: srv.handle_exit(signal.SIGTERM, None)) == 0
    assert closed == [1]


def test_a_client_that_disconnects_does_not_stop_the_server(closed):
    srv, seen = _server(), []

    def gone(loop):
        loop.call_exception_handler({"message": "socket", "exception": ConnectionResetError("client gone")})
        seen.append(srv.should_exit)
        srv.should_exit = True

    assert _run_with(srv, gone) == 0
    assert seen == [False]


def test_uncaught_main_thread_error_closes_the_database(closed, monkeypatch):
    srv = _server()

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(srv, "run", boom)
    assert run(srv) == 1
    assert closed == [1]


def test_a_port_in_use_closes_the_database(closed):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        assert run(_server(taken.getsockname()[1])) != 0
    assert closed == [1]


def test_disconnect_db_closes_everything_even_when_one_fails(monkeypatch):
    order: list[str] = []

    class Pool:
        def close(self):
            order.append("pool")
            raise OperationalError("already gone")

    class Saver:
        conn = Pool()

    class Store:
        def close(self):
            order.append("store")

    monkeypatch.setattr(main.telemetry, "stop", lambda: order.append("log"))
    main._checkpointer, main._store = Saver(), Store()
    main.disconnect_db()
    main.disconnect_db()  # again: nothing left but the (idempotent) log stop
    assert order == ["log", "pool", "store", "log"]
    assert main._checkpointer is None and main._store is None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM to catch")
def test_the_real_process_exits_0_on_sigterm():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen([sys.executable, "-m", "backend.app.server", "--port", str(port)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                assert proc.poll() is None, proc.stdout.read()
                time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    finally:
        proc.kill()
    assert proc.returncode == 0, out
    assert "SIGTERM received" in out
