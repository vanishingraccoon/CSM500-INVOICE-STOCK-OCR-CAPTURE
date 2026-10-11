"""Real server lifecycle with an isolated database and a fake OCR engine."""
import socket
import time
from urllib.request import urlopen

import pytest

from app.launcher import Controller, InstanceLock


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def stop(controller):
    controller.request_stop()
    deadline = time.monotonic() + 45
    while not controller.finish_stop():
        assert time.monotonic() < deadline, "owned server did not stop"
        time.sleep(0.1)


def test_instance_lock_is_released_after_close(tmp_path):
    lock = InstanceLock(tmp_path / "launcher.lock")
    try:
        with pytest.raises(RuntimeError, match="already open"):
            InstanceLock(tmp_path / "launcher.lock")
    finally:
        lock.close()
    InstanceLock(tmp_path / "launcher.lock").close()


def test_port_conflict_does_not_start_a_child(shop):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        controller = Controller(shop.with_(port=occupied.getsockname()[1]))
        with pytest.raises(RuntimeError, match="already in use"):
            controller.start()
        assert controller.process is None


def test_server_starts_serves_stops_and_restarts_with_owned_readers(shop):
    controller = Controller(shop.with_(port=free_port(), workers=1, ocr_engine="serverkit:FileEngine"))
    for _ in range(2):
        controller.start()
        try:
            deadline = time.monotonic() + 30
            while True:
                message = controller.poll()
                if message is not None:
                    assert message["status"] == "ready", message
                    break
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.1)
            with urlopen(message["url"], timeout=5) as response:
                assert response.status == 200
        finally:
            stop(controller)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", controller.settings.port))
    assert "stopping the readers" in controller.log_path.read_text(encoding="utf-8")


def test_engine_startup_failure_is_reported_and_can_be_stopped(shop):
    controller = Controller(shop.with_(port=free_port(), workers=1, ocr_engine="missing_engine:Missing"))
    controller.start()
    try:
        deadline = time.monotonic() + 30
        while True:
            message = controller.poll()
            if message is not None:
                assert message["status"] == "error"
                break
            assert time.monotonic() < deadline
            time.sleep(0.1)
    finally:
        stop(controller)
    assert "missing_engine" in controller.log_path.read_text(encoding="utf-8")


def test_launcher_defaults_to_one_reader_but_respects_configuration(monkeypatch):
    from app.launcher import launcher_settings

    monkeypatch.delenv("INVOICE_WORKERS", raising=False)
    assert launcher_settings().workers == 1
    monkeypatch.setenv("INVOICE_WORKERS", "3")
    assert launcher_settings().workers == 3


def test_start_failure_releases_queue_resources(shop, monkeypatch):
    controller = Controller(shop.with_(port=free_port()))
    original = controller.ctx.Process

    def fail_start(*args, **kwargs):
        process = original(*args, **kwargs)
        monkeypatch.setattr(process, "start", lambda: (_ for _ in ()).throw(OSError("spawn failed")))
        return process

    monkeypatch.setattr(controller.ctx, "Process", fail_start)
    with pytest.raises(OSError, match="spawn failed"):
        controller.start()
    assert controller.process is None
    assert controller.messages is None
    assert controller.stop_event is None
