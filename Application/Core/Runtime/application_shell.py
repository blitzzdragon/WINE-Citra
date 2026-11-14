#!/usr/bin/env python3
"""Detached runtime shell for running Wine applications.

The launcher is only a control panel: the hotkeys and cursor helpers must
keep working after its window is closed.  This module is
started in its own session for every Wine launch (``start_wine`` calls
``start_application_shell``), and it survives the launcher process.

One shell is shared by every Wine application in the session. It owns the
X11 key grabs and watches Wine windows so fullscreen controls and cursor
helpers remain available independently of the launcher.

Run manually with::

    cd Application && python3 -m Core.Runtime.application_shell
"""

import fcntl
import os
import signal
import sys
import time
import traceback

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from Core.Runtime.application_runtime import (
    APP_NAME, CONFIG_FILE, FullscreenKeyInterceptor, PROJECT_ROOT,
    SHELL_HEARTBEAT_FILE, SHELL_LOCK_FILE, log, load_config,
)

POLL_INTERVAL_MS = 250
CONFIG_CHECK_INTERVAL = 2.0

# How long the shell waits for the first Wine window after being started.
STARTUP_TIMEOUT = 300.0

# How long the shell keeps running after the last Wine window disappeared.
# A fresh launch heartbeat extends this so a second application can start
# even when the previous one has already been closed.
IDLE_TIMEOUT = 15.0

# A launch heartbeat younger than this means a Wine program is starting;
# the shell must not shut down while it waits for the first window.
LAUNCH_GRACE = 300.0


def _acquire_singleton():
    """Take an exclusive lock so two shells never fight over X11 grabs."""
    try:
        SHELL_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        handle = open(SHELL_LOCK_FILE, "a+", encoding="utf-8")
    except OSError:
        return open(os.devnull, "w")

    deadline = time.monotonic() + 2.0
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(0.1)

    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n{time.time():.3f}\n")
        handle.flush()
    except OSError:
        pass
    return handle


class ApplicationShell:
    def __init__(self, app):
        self.app = app
        self.config = load_config()
        self.interceptor = FullscreenKeyInterceptor(self.config)

        self._started = time.monotonic()
        self._seen_window = False
        self._idle_since = None
        self._config_mtime = self._current_config_mtime()
        self._last_config_check = 0.0
        self._shutting_down = False

        self._poll = QTimer()
        self._poll.setInterval(POLL_INTERVAL_MS)
        self._poll.timeout.connect(self._tick)

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    def start(self):
        log(f"Application shell started (pid {os.getpid()}).")
        self._retire_legacy_wrappers()
        self.interceptor.start()
        self._poll.start()
        self.app.aboutToQuit.connect(self.shutdown)

    def _retire_legacy_wrappers(self):
        """Stop old launchers that still own the X11 hotkey grabs."""
        project_main = os.path.realpath(PROJECT_ROOT / "main.py")
        excluded = {os.getpid(), os.getppid()}
        retired = 0

        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid in excluded:
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as handle:
                    args = [part.decode("utf-8", "replace")
                            for part in handle.read().split(b"\0") if part]
            except OSError:
                continue
            if len(args) < 3:
                continue

            if not (os.path.basename(args[1]) == "wine-citra"
                    or os.path.realpath(args[1]) == project_main):
                continue
            if not any(arg.lower().endswith(".exe") for arg in args[2:]):
                continue

            try:
                os.kill(pid, signal.SIGTERM)
                retired += 1
            except OSError:
                continue

        if retired:
            log(f"Stopped {retired} legacy hotkey wrapper(s) so the "
                "application shell can take over.")
            time.sleep(0.2)

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        log("Application shell stopping.")
        self._poll.stop()
        try:
            self.interceptor.stop()
        except Exception:
            pass

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    def _tick(self):
        if self._shutting_down:
            return
        try:
            self._reload_config()
            self._update_lifetime(self.interceptor.has_wine_windows())
        except Exception:
            log("Application shell tick failed:\n"
                + traceback.format_exc())

    # --------------------------------------------------------
    # Settings and lifetime
    # --------------------------------------------------------

    def _current_config_mtime(self):
        try:
            return CONFIG_FILE.stat().st_mtime
        except OSError:
            return None

    def _reload_config(self):
        now = time.monotonic()
        if now - self._last_config_check < CONFIG_CHECK_INTERVAL:
            return
        self._last_config_check = now

        mtime = self._current_config_mtime()
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            fresh = load_config()
        except Exception:
            return
        # Update in place because the interceptor holds this dictionary.
        self.config.clear()
        self.config.update(fresh)

    def _heartbeat_fresh(self):
        try:
            return (time.time() - SHELL_HEARTBEAT_FILE.stat().st_mtime
                    < LAUNCH_GRACE)
        except OSError:
            return False

    def _update_lifetime(self, has_windows):
        now = time.monotonic()

        if has_windows:
            self._seen_window = True
            self._idle_since = None
            return

        # A launcher is about to open a Wine window; stay alive for it.
        if self._heartbeat_fresh():
            self._idle_since = None
            return

        if self._seen_window:
            if self._idle_since is None:
                self._idle_since = now
            elif now - self._idle_since >= IDLE_TIMEOUT:
                log("Application shell idle; no Wine windows remain.")
                self.app.quit()
        elif now - self._started >= STARTUP_TIMEOUT:
            log("Application shell exiting; no Wine window appeared.")
            self.app.quit()


def main():
    lock = _acquire_singleton()
    if lock is None:
        # Another shell already owns the session's key grabs.
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    shell = ApplicationShell(app)
    shell.start()

    def _quit(*_args):
        app.quit()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(signum, _quit)
        except (ValueError, OSError):
            pass

    try:
        return app.exec()
    except Exception:
        log("Application shell crashed:\n" + traceback.format_exc())
        return 1
    finally:
        shell.shutdown()
        try:
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())