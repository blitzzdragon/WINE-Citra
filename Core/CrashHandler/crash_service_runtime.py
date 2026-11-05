"""Crash reporting for WINE Citra."""

import sys
import traceback

from Core.Runtime.application_runtime import GLOBAL_LOG, log


def global_exception_handler(exc_type, exc_value, exc_tb):
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log(text)
    try:
        from UI.Dialouges.crash_popup import show_crash_dialog
        show_crash_dialog(GLOBAL_LOG)
    except Exception:
        # Never let the reporting path itself raise.
        pass


def install_exception_hook():
    """Route uncaught exceptions through the application crash handler."""
    sys.excepthook = global_exception_handler
