"""Crash notification dialog."""

from PyQt6.QtWidgets import QMessageBox

from Core.Runtime.application_runtime import APP_NAME, GLOBAL_LOG


def show_crash_dialog(log_path=GLOBAL_LOG):
    """Tell the user where the traceback was written."""
    QMessageBox.critical(
        None,
        f"{APP_NAME} - Error",
        "An unexpected error occurred.\n"
        f"Details were saved to:\n{log_path}",
    )
