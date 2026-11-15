"""Crash notification dialog."""

import html
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)

from Core.Runtime.application_runtime import APP_NAME, GLOBAL_LOG
from UI.theme import MUTED_TEXT_COLOR


class CrashDialog(QDialog):
    def __init__(self, log_path=GLOBAL_LOG, exc_type=None, exc_value=None,
                 traceback_text="", *, report_title=None, heading_text=None,
                 info_text=None, error_text=None, close_text="Close and exit",
                 quit_on_close=True):
        super().__init__(None)
        self.report_title = report_title or f"{APP_NAME} crash report"
        self.quit_on_close = quit_on_close
        self.setWindowTitle(report_title or f"{APP_NAME} - Crash Report")
        self.setFixedSize(720, 520)
        self.traceback_text = traceback_text or ""
        self.log_path = log_path
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)
        palette = self.palette()
        error_color = "#ff3333"
        text_color = palette.color(QPalette.ColorRole.Text).name()
        muted_color = MUTED_TEXT_COLOR
        base_color = palette.color(QPalette.ColorRole.Base).name()
        border_color = palette.color(QPalette.ColorRole.Mid).name()

        title = QLabel(
            heading_text or "WINE Citra encountered an unexpected error.")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {error_color};")
        layout.addWidget(title)

        code = error_text or getattr(exc_type, "__name__", "UnknownError")
        if error_text is None and exc_value is not None:
            code = f"{code}: {exc_value}"
        error_label = QLabel(f"Error code: {code}")
        error_label.setStyleSheet(
            f"font-family: monospace; color: {text_color};")
        error_label.setWordWrap(True)
        layout.addWidget(error_label)

        info = QLabel(
            info_text or
            "A traceback was saved. The most recent log lines are shown below:")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Friendly, human-readable summary of the failure.
        hint = self._hint(exc_type, exc_value)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet(
                f"color: {muted_color}; font-style: italic;")
            layout.addWidget(hint_label)

        tail = self._tail_log(log_path, lines=60)
        summary = self._summary(exc_type, exc_value, traceback_text, tail)
        summary_label = QLabel(summary)
        summary_label.setWordWrap(True)
        summary_label.setTextFormat(Qt.TextFormat.RichText)
        summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        summary_label.setStyleSheet(
            f"background: {base_color}; color: {text_color}; padding: 8px; "
            f"border: 1px solid {border_color}; border-radius: 4px;")
        layout.addWidget(summary_label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlainText(tail)
        self.log_view.setStyleSheet("font-family: monospace; font-size: 9pt;")
        layout.addWidget(self.log_view, 1)

        # Collapsible full traceback for advanced users.
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlainText(traceback_text or "(no traceback captured)")
        self.details.setStyleSheet("font-family: monospace; font-size: 8pt;")
        self.details.hide()
        layout.addWidget(self.details, 1)

        toggle = QPushButton("Show full traceback")
        toggle.clicked.connect(self._toggle_details)
        self._toggle_btn = toggle

        btns = QHBoxLayout()
        copy_btn = QPushButton("Copy report")
        copy_btn.clicked.connect(self._copy_report)
        open_btn = QPushButton("Open log folder")
        open_btn.clicked.connect(self._open_log_folder)
        btns.addWidget(copy_btn)
        btns.addWidget(open_btn)
        btns.addWidget(toggle)
        btns.addStretch()
        close_btn = QPushButton(close_text)
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        self._scroll_logs_to_bottom()
        self._center()

    @staticmethod
    def _tail_log(log_path, lines=40):
        try:
            path = Path(log_path)
            if not path.exists():
                return "(log file not found)"
            data = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(data[-lines:]) if data else "(log empty)"
        except OSError as e:
            return f"(could not read log: {e})"

    @staticmethod
    def _hint(exc_type, exc_value):
        name = getattr(exc_type, "__name__", "")
        hints = {
            "ModuleNotFoundError": "A required Python module is missing. "
                                    "Install the dependencies listed by the packager.",
            "FileNotFoundError": "A required file or executable could not be found.",
            "PermissionError": "Permission was denied while accessing a file or folder.",
            "RuntimeError": "The application hit an unexpected runtime condition.",
        }
        base = hints.get(name, "An unexpected error occurred while running WINE Citra.")
        if exc_value is not None:
            return f"{base}\nDetails: {exc_value}"
        return base

    @staticmethod
    def _summary(exc_type, exc_value, traceback_text, log_tail):
        lines = [line for line in (traceback_text or "").splitlines() if line.strip()]
        failure = lines[-1] if lines else f"{getattr(exc_type, '__name__', 'Error')}"
        location = ""
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith('File "') and ", line " in stripped:
                location = stripped
                break
        log_count = len((log_tail or "").splitlines())
        parts = [
            "<b>Summary</b>",
            f"\u2022 Failure: <code>{html.escape(failure)}</code>",
        ]
        if location:
            parts.append(
                f"\u2022 Location: <code>{html.escape(location)}</code>")
        parts.append(f"\u2022 Log lines shown: {log_count}")
        return "<br>".join(parts)

    def _scroll_logs_to_bottom(self):
        for view in (self.log_view, self.details):
            sb = view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _toggle_details(self):
        visible = self.details.isVisible()
        self.details.setVisible(not visible)
        self._toggle_btn.setText(
            "Hide full traceback" if not visible else "Show full traceback")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._scroll_logs_to_bottom)

    def _copy_report(self):
        from PyQt6.QtWidgets import QApplication
        report = (
            f"{self.report_title}\n"
            f"Error: {self.traceback_text}\n\n"
            f"Log tail:\n{self.log_view.toPlainText()}\n"
        )
        QApplication.clipboard().setText(report)

    def _open_log_folder(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from pathlib import Path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.log_path).parent)))

    def showEvent(self, event):
        super().showEvent(event)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._scroll_logs_to_bottom)

    def _center(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())

    def accept(self):
        super().accept()
        if self.quit_on_close:
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()


def show_crash_dialog(log_path=GLOBAL_LOG, exc_type=None, exc_value=None,
                      traceback_text=""):
    """Show the crash dialog and then quit the application."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    dialog = CrashDialog(log_path, exc_type, exc_value, traceback_text)
    dialog.exec()
    app.quit()


def show_game_crash_dialog(application_name, exit_code, log_path):
    """Show diagnostics for a Wine program without closing WINE Citra."""
    import signal

    if exit_code < 0:
        try:
            reason = f"terminated by {signal.Signals(-exit_code).name}"
        except ValueError:
            reason = f"terminated by signal {-exit_code}"
    else:
        reason = f"exited with code {exit_code}"
    detail = (
        f"{application_name} {reason}.\n"
        "The program may have crashed. Review its Wine log for details.")
    dialog = CrashDialog(
        log_path=log_path,
        exc_type=RuntimeError,
        exc_value=detail,
        traceback_text=detail,
        report_title=f"{application_name} crash report",
        heading_text=f"{application_name} ended unexpectedly.",
        info_text="The latest Wine output is shown below.",
        error_text=reason,
        close_text="Close",
        quit_on_close=False,
    )
    dialog.exec()