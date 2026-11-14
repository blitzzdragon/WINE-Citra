"""Logs tab: browse and read the application logs."""

import os

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import APP_NAME, GLOBAL_LOG, LOG_DIR


def open_path(parent, path):
    path = Path(path)
    if not path.exists():
        QMessageBox.warning(parent, APP_NAME, f"Path does not exist:\n{path}")
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


class LogsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.log_list = QListWidget()
        self.log_list.setAccessibleName("Application log files")
        self.log_list.setAccessibleDescription(
            "Select a log to read its recent contents.")
        self.log_list.currentItemChanged.connect(self.show_log)
        splitter.addWidget(self.log_list)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAccessibleName("Selected log contents")
        self.log_view.setFont(QFont("monospace", 9))
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        splitter.addWidget(self.log_view)
        splitter.setSizes([230, 520])
        layout.addWidget(splitter, 1)

        filter_row = QHBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter logs by name...")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.setAccessibleName("Filter log files")
        self.filter_input.textChanged.connect(self.filter_logs)
        filter_row.addWidget(self.filter_input, 1)
        self.chk_auto_refresh = QCheckBox("Auto-refresh")
        filter_row.addWidget(self.chk_auto_refresh)
        layout.addLayout(filter_row)

        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.refresh_logs)
        self.chk_auto_refresh.toggled.connect(
            lambda on: self.auto_timer.start(2000) if on else self.auto_timer.stop())

        row = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_logs)
        refresh.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        folder = QPushButton("Open Log Folder")
        folder.clicked.connect(lambda: open_path(self, LOG_DIR))
        folder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        clear = QPushButton("Delete Selected Log")
        clear.clicked.connect(self.delete_log)
        clear.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for button in (refresh, folder, clear):
            row.addWidget(button)
        layout.addLayout(row)

    def refresh_logs(self):
        selected_path = None
        current = self.log_list.currentItem()
        if current:
            selected_path = current.data(Qt.ItemDataRole.UserRole)
        self.log_list.blockSignals(True)
        self.log_list.clear()
        paths = sorted(LOG_DIR.glob("*.log"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in paths:
            label = "Global log" if p == GLOBAL_LOG else p.name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.log_list.addItem(item)
        self.log_list.blockSignals(False)
        self.filter_logs(self.filter_input.text())
        restored = None
        for index in range(self.log_list.count()):
            item = self.log_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == selected_path:
                restored = item
                break
        if restored is None:
            restored = next(
                (self.log_list.item(i) for i in range(self.log_list.count())
                 if not self.log_list.item(i).isHidden()),
                None)
        self.log_list.setCurrentItem(restored)
        if restored is None:
            self.log_view.clear()

    def filter_logs(self, text):
        needle = (text or "").lower()
        for i in range(self.log_list.count()):
            item = self.log_list.item(i)
            item.setHidden(needle not in item.text().lower())

    def show_log(self, item, *_):
        if not item:
            self.log_view.clear()
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                if size > 300_000:
                    f.seek(-300_000, os.SEEK_END)
                text = f.read().decode("utf-8", errors="replace")
        except OSError as e:
            text = f"Could not read log: {e}"
        self.log_view.setPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def delete_log(self):
        item = self.log_list.currentItem()
        if not item:
            return
        answer = QMessageBox.question(
            self, "Delete Log",
            f"Permanently delete '{item.text()}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            Path(item.data(Qt.ItemDataRole.UserRole)).unlink()
        except OSError as e:
            self.window.message("Failed", str(e), QMessageBox.Icon.Critical)
        self.refresh_logs()
