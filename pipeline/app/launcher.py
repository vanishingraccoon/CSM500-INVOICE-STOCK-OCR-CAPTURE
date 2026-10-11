"""A desktop window that owns the laptop server and its readers."""
from __future__ import annotations

import logging
import multiprocessing
import os
from pathlib import Path
import queue
import socket
import time
import webbrowser

from app.settings import Settings


class InstanceLock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        if path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("The launcher is already open for this data folder.") from None

    def close(self):
        self.stream.close()


def service_main(settings: Settings, stop, messages, log_path: str):
    """Child entry point; errors go to both the log and the launcher window."""
    with open(log_path, "a", encoding="utf-8", buffering=1) as stream:
        import sys
        sys.stdout = sys.stderr = stream
        logging.basicConfig(stream=stream, level=logging.INFO,
                            format="%(asctime)s %(name)s %(levelname)s %(message)s", force=True)
        try:
            from app.serve import run
            run(settings, stop=stop, ready=messages)
        except BaseException as error:
            logging.exception("server stopped unexpectedly")
            messages.put({"status": "error", "error": f"{type(error).__name__}: {error}"})


class Controller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ctx = multiprocessing.get_context("spawn")
        self.process = None
        self.stop_event = None
        self.messages = None
        self.log_path = settings.app_data / "logs" / "server.log"
        self.started = 0.0

    def start(self):
        if self.process is not None:
            raise RuntimeError("Stop the current server before starting another.")
        with socket.socket() as probe:
            try:
                probe.bind((self.settings.host, self.settings.port))
            except OSError:
                raise RuntimeError(f"Port {self.settings.port} is already in use. Choose another port.") from None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_event = self.ctx.Event()
        self.messages = self.ctx.Queue()
        self.process = self.ctx.Process(target=service_main,
                                        args=(self.settings, self.stop_event, self.messages, str(self.log_path)),
                                        name="invoice-server")
        try:
            self.process.start()
        except BaseException:
            self.process.close()
            self.process = None
            self.messages.close()
            self.messages = None
            self.stop_event = None
            raise
        self.started = time.monotonic()

    def poll(self):
        if self.process is None:
            return None
        try:
            return self.messages.get_nowait()
        except queue.Empty:
            if not self.process.is_alive():
                return {"status": "error", "error": "The server stopped. Open the log for details."}
            if time.monotonic() - self.started > 360:
                return {"status": "error", "error": "Server startup exceeded six minutes. Open the log for details."}
        return None

    def request_stop(self):
        if self.stop_event is not None:
            self.stop_event.set()

    def finish_stop(self):
        """Release the owned process after it has exited; the UI polls without blocking."""
        if self.process is not None and self.process.is_alive():
            return False
        if self.process is not None:
            self.process.join()
            self.process.close()
            self.process = None
        if self.messages is not None:
            self.messages.close()
            self.messages = None
        self.stop_event = None
        return True


def launcher_settings() -> Settings:
    """Use one model-owning reader by default on a laptop; retain explicit settings."""
    settings = Settings.from_env().with_(host="0.0.0.0")
    return settings if "INVOICE_WORKERS" in os.environ else settings.with_(workers=1)


def main():
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("News Cafe Eastgate - Invoice Server")
    root.geometry("540x290")
    settings = launcher_settings()
    try:
        lock = InstanceLock(settings.app_data / ".launcher.lock")
    except RuntimeError as error:
        messagebox.showerror("Invoice Server", str(error))
        root.destroy()
        return
    controller = Controller(settings)
    status = tk.StringVar(value="Stopped")
    port = tk.StringVar(value=str(settings.port))
    workers = tk.StringVar(value=str(settings.workers))
    closing = False
    stopping = False
    ready = False
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Invoice Server", font=("Segoe UI", 16)).pack(anchor="w")
    ttk.Label(frame, textvariable=status, wraplength=490).pack(anchor="w", pady=12)
    fields = ttk.Frame(frame)
    fields.pack(anchor="w")
    ttk.Label(fields, text="Port").pack(side="left")
    ttk.Entry(fields, textvariable=port, width=8).pack(side="left", padx=8)
    ttk.Label(fields, text="Readers").pack(side="left")
    ttk.Entry(fields, textvariable=workers, width=5).pack(side="left", padx=8)

    def start():
        nonlocal ready, stopping
        try:
            chosen_port, chosen_workers = int(port.get()), int(workers.get())
            if not 1 <= chosen_port <= 65535 or not 1 <= chosen_workers <= 8:
                raise ValueError("Use a port from 1 to 65535 and between 1 and 8 readers.")
            controller.settings = settings.with_(port=chosen_port, workers=chosen_workers)
            controller.start()
            ready, stopping = False, False
            status.set("Starting the server and loading OCR models. First startup can take several minutes...")
        except (ValueError, RuntimeError, OSError) as error:
            messagebox.showerror("Cannot start", str(error))

    def stop():
        nonlocal stopping, ready
        stopping, ready = True, False
        controller.request_stop()
        status.set("Stopping the server and its readers...")

    def close():
        nonlocal closing
        closing = True
        stop()

    def open_portal():
        if ready:
            webbrowser.open(f"http://127.0.0.1:{controller.settings.port}/")

    buttons = ttk.Frame(frame)
    buttons.pack(anchor="w", pady=14)
    for label, command in (("Start", start), ("Stop", stop), ("Open portal", open_portal)):
        ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 8))
    ttk.Button(buttons, text="Open log", command=lambda: os.startfile(controller.log_path)
               if controller.log_path.exists() else messagebox.showinfo("Log", "Start the server to create its log.")
               ).pack(side="left")
    ttk.Label(frame, text="Pair the Samsung from the portal's Pair page. Use the laptop's Wi-Fi address.",
              wraplength=490).pack(anchor="w")

    def tick():
        nonlocal ready
        if stopping:
            if controller.finish_stop():
                status.set("Stopped")
                if closing:
                    lock.close()
                    root.destroy()
                    return
        elif not ready:
            message = controller.poll()
            if message:
                if message["status"] == "ready":
                    ready = True
                    status.set("Server and OCR readers are ready.")
                    open_portal()
                else:
                    status.set(message["error"])
                    stop()
                    messagebox.showerror("Server startup", message["error"])
        elif controller.process is not None and not controller.process.is_alive():
            stop()
            messagebox.showerror("Server stopped", "Open the log for details.")
        root.after(250, tick)

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(0, start)
    root.after(250, tick)
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
