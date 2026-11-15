"""Crash reporting for WINE Citra."""

import faulthandler
import sys
import traceback

from Core.Runtime.application_runtime import GLOBAL_LOG, log
from UI.Audio.sounds import play_sound

_native_crash_log = None


def global_exception_handler(exc_type, exc_value, exc_tb):
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log(text)
    play_sound("error")
    try:
        from UI.Dialouges.crash_popup import show_crash_dialog
        show_crash_dialog(GLOBAL_LOG, exc_type, exc_value, text)
    except Exception:
        # Never let the reporting path itself raise.
        pass


def install_exception_hook():
    """Route uncaught exceptions through the application crash handler."""
    sys.excepthook = global_exception_handler
    install_native_crash_logging(GLOBAL_LOG)


def install_native_crash_logging(log_path):
    """Write Python thread stacks to the log before a native fatal exit."""
    global _native_crash_log
    if _native_crash_log is not None:
        return
    handle = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "a", encoding="utf-8")
        faulthandler.enable(file=handle, all_threads=True)
    except (OSError, RuntimeError) as error:
        if handle is not None:
            handle.close()
        log(f"Could not enable native crash diagnostics: {error}")
        return
    _native_crash_log = handle
