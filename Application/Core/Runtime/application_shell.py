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
import subprocess
import sys
import time
import traceback

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import QApplication

from Core.CrashHandler.crash_service_runtime import (
    install_native_crash_logging,
)
from Core.Runtime.application_runtime import (
    APP_NAME, CONFIG_FILE, FullscreenKeyInterceptor, PROJECT_ROOT, SHELL_LOG,
    SHELL_HEARTBEAT_FILE, SHELL_LOCK_FILE, clear_launcher_tab_request, log,
    load_config, request_launcher_tab,
)
from Core.Runtime.clip_recorder import RollingClipRecorder
from UI.Audio.sounds import play_sound
from UI.Notifications.screenshot_notification import ScreenshotNotification

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


class ClipPreviewWorker(QThread):
    preview_ready = pyqtSignal(object, object, object)

    def __init__(self, clip_path, clip_duration, parent=None):
        super().__init__(parent)
        self.clip_path = Path(clip_path)
        self.clip_duration = clip_duration
        self.preview_path = None
        self.error = None

    def run(self):
        try:
            self.preview_path, self.error = RollingClipRecorder.create_preview(
                self.clip_path, self.clip_duration)
        except Exception as error:
            self.error = str(error)
            log("Animated clip preview generation failed:\n"
                + traceback.format_exc())
        self.preview_ready.emit(self, self.preview_path, self.error)


class ApplicationShell(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.config = load_config()
        self.interceptor = FullscreenKeyInterceptor(self.config)
        self.clip_recorder = RollingClipRecorder()
        self._clip_start_failed_for = None
        self._screenshot_preview = None
        self._retiring_screenshot_previews = []
        self._clip_preview_workers = {}

        self._started = time.monotonic()
        self._seen_window = False
        self._idle_since = None
        self._config_mtime = self._current_config_mtime()
        self._last_config_check = 0.0
        self._shutting_down = False

        self._poll = QTimer(self)
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
        self.clip_recorder.stop()
        if self._screenshot_preview is not None:
            self._screenshot_preview.close()
            self._screenshot_preview = None
        for preview in self._retiring_screenshot_previews:
            preview.close()
        self._retiring_screenshot_previews.clear()
        for worker in list(self._clip_preview_workers):
            worker.wait()
            self._clip_preview_ready(
                worker, worker.preview_path, worker.error)
            worker.deleteLater()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    def _tick(self):
        if self._shutting_down:
            return
        try:
            self._reload_config()
            self._open_launcher_if_requested()
            self._sync_clip_recorder()
            self._save_requested_clip()
            self._capture_requested_screenshot()
            self._update_lifetime(self.interceptor.has_wine_windows())
        except Exception:
            log("Application shell tick failed:\n"
                + traceback.format_exc())

    def _open_launcher_if_requested(self):
        if not self.interceptor.take_launcher_request():
            return
        notification = self._screenshot_preview
        requested_tab = (
            getattr(notification, "gallery_tab", None)
            if notification is not None and notification.isVisible()
            else None)
        if requested_tab:
            request_launcher_tab(requested_tab)
        try:
            with open(SHELL_LOG, "a", encoding="utf-8") as output:
                subprocess.Popen(
                    [sys.executable, str(PROJECT_ROOT / "main.py")],
                    cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True)
            if requested_tab:
                log(f"F8 requested the WINE Citra {requested_tab} tab.")
            else:
                log("F8 requested the WINE Citra launcher.")
        except OSError:
            clear_launcher_tab_request()
            log("Could not open the launcher from F8:\n"
                + traceback.format_exc())

    def _sync_clip_recorder(self):
        if not self.config.get("clips_enabled", True):
            self.clip_recorder.stop()
            self._clip_start_failed_for = None
            return

        window_id = self.interceptor.primary_wine_window()
        if window_id is None:
            self.clip_recorder.stop()
            self._clip_start_failed_for = None
            return
        capture_geometry = (
            self.interceptor.primary_wine_window_geometry())
        audio_enabled = self.config.get("clip_audio_enabled", True)
        audio_output = self.config.get("audio_output", "")
        clip_duration = self.config.get("clip_duration", 30)
        options = (window_id, capture_geometry, bool(audio_enabled),
                   int(clip_duration),
                   str(audio_output or ""))
        active_options = (
            self.clip_recorder.window_id,
            self.clip_recorder.capture_geometry,
            self.clip_recorder.audio_enabled,
            self.clip_recorder.clip_duration,
            self.clip_recorder.audio_output,
        )
        if options == active_options:
            if self.clip_recorder.is_running():
                return
            self.clip_recorder.stop()
            self._clip_start_failed_for = options
            return
        if options == self._clip_start_failed_for:
            return

        self._clip_start_failed_for = None
        if not self.clip_recorder.start(
                window_id, audio_enabled, audio_output, capture_geometry,
                clip_duration):
            self._clip_start_failed_for = options

    def _save_requested_clip(self):
        window_id = self.interceptor.take_clip_request()
        if window_id is None:
            return
        if window_id != self.clip_recorder.window_id:
            log("Clip request came from a Wine window without an active "
                "rolling buffer.")
            play_sound("error", self.config)
            return

        directory_value = self.config.get("clip_directory", "").strip()
        directory = (Path(directory_value).expanduser() if directory_value
                     else Path.home() / ".local/share/wine-citra/clips")
        try:
            path, error = self.clip_recorder.save_recent(directory)
        except OSError:
            log("Could not save requested clip:\n" + traceback.format_exc())
            play_sound("error", self.config)
            return
        if error:
            log(f"Clip not saved: {error}")
            play_sound("error", self.config)
        elif path is not None:
            log(f"Clip saved: {path}")
            screen = QGuiApplication.screenAt(QCursor.pos())
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is not None:
                image = screen.grabWindow(window_id)
                if not image.isNull():
                    notification = self._show_capture_notification(
                        image, screen,
                        f"{self.clip_recorder.clip_duration}-second clip saved",
                        gallery_tab="clips")
                    if self.config.get(
                            "clip_animated_preview_enabled", True):
                        worker = ClipPreviewWorker(
                            path, self.clip_recorder.clip_duration, self)
                        self._clip_preview_workers[worker] = notification
                        worker.preview_ready.connect(
                            self._clip_preview_ready)
                        worker.finished.connect(worker.deleteLater)
                        worker.start()

    @pyqtSlot(object, object, object)
    def _clip_preview_ready(self, worker, preview_path, error):
        notification = self._clip_preview_workers.pop(worker, None)
        if notification is None:
            return
        if error:
            log("Animated clip preview unavailable: " + str(error))
        elif preview_path:
            notification.set_animated_preview(preview_path)

    def _capture_requested_screenshot(self):
        window_id = self.interceptor.take_screenshot_request()
        if window_id is None:
            return
        if not self.config.get("screenshots_enabled", True):
            return
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            log("Screenshot failed: no display screen is available.")
            return

        try:
            image = screen.grabWindow(window_id)
        except Exception:
            log("Screenshot capture failed:\n" + traceback.format_exc())
            return
        if image.isNull():
            log(f"Screenshot capture failed for Wine window 0x{window_id:x}.")
            return

        directory_value = self.config.get("screenshot_directory", "").strip()
        directory = (Path(directory_value).expanduser() if directory_value
                     else Path.home() / ".local/share/wine-citra/screenshots")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (
                f"screenshot-{datetime.now():%Y%m%d-%H%M%S-%f}.png")
            if not image.save(str(path), "PNG"):
                raise OSError("Qt could not save the PNG image.")
        except OSError:
            log("Screenshot could not be saved:\n" + traceback.format_exc())
            return

        log(f"Screenshot saved: {path}")
        self._show_capture_notification(
            image, screen, "Screenshot saved", gallery_tab="screenshots")

    def _show_capture_notification(self, image, screen, title,
                                   preview_path=None, gallery_tab=None):
        if (self._screenshot_preview is not None
                and self._screenshot_preview.isVisible()):
            previous = self._screenshot_preview
            previous.dismissed.connect(
                lambda preview=previous:
                self._release_retiring_screenshot_preview(preview))
            self._retiring_screenshot_previews.append(previous)
            previous.dismiss()
        preview = ScreenshotNotification(
            image, screen, self.config, title=title,
            preview_path=preview_path)
        preview.gallery_tab = gallery_tab
        self._screenshot_preview = preview
        preview.show()
        return preview

    def _release_retiring_screenshot_preview(self, previous):
        self._retiring_screenshot_previews = [
            preview for preview in self._retiring_screenshot_previews
            if preview is not previous]

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

    install_native_crash_logging(SHELL_LOG)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
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
        play_sound("error", shell.config)
        return 1
    finally:
        shell.shutdown()
        try:
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())