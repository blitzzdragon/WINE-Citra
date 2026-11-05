"""Logs tab: browse and read the application logs."""

import os

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QVBoxLayout, QWidget,
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
        self.log_list.currentItemChanged.connect(self.show_log)
        splitter.addWidget(self.log_list)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("monospace", 9))
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        splitter.addWidget(self.log_view)
        splitter.setSizes([230, 520])
        layout.addWidget(splitter, 1)

        row = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_logs)
        folder = QPushButton("Open Log Folder")
        folder.clicked.connect(lambda: open_path(self, LOG_DIR))
        clear = QPushButton("Delete Selected Log")
        clear.clicked.connect(self.delete_log)
        for button in (refresh, folder, clear):
            row.addWidget(button)
        layout.addLayout(row)

    def refresh_logs(self):
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
        self.log_view.clear()

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
        try:
            Path(item.data(Qt.ItemDataRole.UserRole)).unlink()
        except OSError as e:
            self.window.message("Failed", str(e), QMessageBox.Icon.Critical)
        self.refresh_logs()
