"""Submit bug reports."""

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from Core.Runtime.report_service import ReportServiceError, SupabaseReportService


class _ReportWorker(QThread):
    completed = pyqtSignal(object, object)

    def __init__(self, arguments):
        super().__init__()
        self.arguments = arguments

    def run(self):
        try:
            service = SupabaseReportService()
            result = service.create_report(*self.arguments)
            self.completed.emit(result, None)
        except (ReportServiceError, OSError) as error:
            self.completed.emit(None, str(error))
        except Exception as error:
            self.completed.emit(
                None,
                f"Unexpected report error: {error}")


class ReportsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._worker = None
        self._selected_paths = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Send a bug report or make a suggestion to the maintainer. You can optionally attach "
            "screenshots or clips.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form_group = QGroupBox("Submit a Bug Report (clips commonly exceed the max upload limit)")
        form_layout = QVBoxLayout(form_group)

        fields = QFormLayout()
        self.title_input = QLineEdit()
        self.title_input.setMaxLength(160)
        self.title_input.setPlaceholderText("A short summary of the problem")
        self.title_input.setAccessibleName("Bug report title")
        fields.addRow("Title", self.title_input)
        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText(
            "What happened? Include the steps to reproduce it and what you "
            "expected to happen.")
        self.description_input.setMaximumHeight(190)
        self.description_input.setAccessibleName("Bug report details")
        fields.addRow("Details", self.description_input)
        form_layout.addLayout(fields)

        attachment_row = QHBoxLayout()
        self.attachments_label = QLabel("No attachments selected")
        self.attachments_label.setWordWrap(True)
        attachment_row.addWidget(self.attachments_label, 1)
        screenshots_button = QPushButton("Add Screenshots")
        screenshots_button.clicked.connect(self._select_screenshots)
        attachment_row.addWidget(screenshots_button)
        clips_button = QPushButton("Add Clips")
        clips_button.clicked.connect(self._select_clips)
        attachment_row.addWidget(clips_button)
        clear_attachments = QPushButton("Clear")
        clear_attachments.clicked.connect(lambda: self._set_selected_files([]))
        attachment_row.addWidget(clear_attachments)
        form_layout.addLayout(attachment_row)

        self.attachment_consent = QCheckBox(
            "I agree to upload the selected files with this bug report.")
        form_layout.addWidget(self.attachment_consent)
        self.submit_button = QPushButton("Submit Bug Report")
        self.submit_button.clicked.connect(self.submit_report)
        form_layout.addWidget(
            self.submit_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(form_group)
        layout.addStretch(1)

    def _select_screenshots(self):
        self._select_files(
            "Select screenshots", "screenshot_directory",
            "Images (*.png *.jpg *.jpeg *.webp)")

    def _select_clips(self):
        self._select_files(
            "Select clips", "clip_directory",
            "Videos (*.mp4 *.mkv *.mov *.webm *.avi)")

    def _select_files(self, title, setting, file_filter):
        initial_dir = self.window.config.get(setting, str(Path.home()))
        paths, _ = QFileDialog.getOpenFileNames(
            self, title, initial_dir, file_filter)
        if paths:
            self._set_selected_files([*self._selected_paths, *paths])

    def _set_selected_files(self, paths):
        self._selected_paths = list(dict.fromkeys(paths))
        if self._selected_paths:
            names = [Path(path).name for path in self._selected_paths]
            self.attachments_label.setText(
                f"{len(names)} file(s): " + ", ".join(names))
        else:
            self.attachments_label.setText("No attachments selected")

    def wait_for_idle(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait()

    def submit_report(self):
        title = self.title_input.text().strip()
        description = self.description_input.toPlainText().strip()
        if not title or not description:
            QMessageBox.information(
                self, "Bug Report", "Enter a title and details before submitting.")
            return
        if self._selected_paths and not self.attachment_consent.isChecked():
            QMessageBox.information(
                self, "Attachment Consent",
                "Confirm that you agree to upload the selected files.")
            return
        self._start_worker(title, description, list(self._selected_paths))

    def _start_worker(self, *arguments):
        if self._worker is not None:
            return
        self._set_busy(True)
        self._worker = _ReportWorker(arguments)
        self._worker.completed.connect(self._operation_completed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _set_busy(self, busy):
        self.submit_button.setEnabled(not busy)

    def _operation_completed(self, result, error):
        self._worker = None
        self._set_busy(False)
        if error:
            QMessageBox.warning(self, "Bug Reports", str(error))
            return
        attachment_errors = result.get("attachment_errors", [])
        self.title_input.clear()
        self.description_input.clear()
        self._set_selected_files([])
        self.attachment_consent.setChecked(False)
        if attachment_errors:
            QMessageBox.warning(
                self, "Report Submitted",
                "The bug report was submitted, but some attachments could "
                "not be uploaded:\n\n" + "\n".join(attachment_errors))
        else:
            QMessageBox.information(
                self, "Report Submitted",
                "Your bug report was submitted.")
