"""Logs tab: browse and read the application logs."""

import os

from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import APP_NAME, GLOBAL_LOG, LOG_DIR
from UI.Audio.sounds import play_sound


def open_path(parent, path):
    path = Path(path)
    if not path.exists():
        play_sound("error")
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
            "Select one or more logs. The current log's recent contents are shown.")
        self.log_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
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
        self.delete_button = QPushButton("Delete Selected Logs")
        self.delete_button.clicked.connect(self.delete_log)
        self.delete_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for button in (refresh, folder, self.delete_button):
            row.addWidget(button)
        layout.addLayout(row)

    def refresh_logs(self):
        selected_paths = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.log_list.selectedItems()
        }
        current = self.log_list.currentItem()
        current_path = (current.data(Qt.ItemDataRole.UserRole)
                        if current else None)
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
        restored = []
        current_item = None
        for index in range(self.log_list.count()):
            item = self.log_list.item(index)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path in selected_paths:
                item.setSelected(True)
                restored.append(item)
            if path == current_path:
                current_item = item
        if current_item is None:
            current_item = next(
                (self.log_list.item(i) for i in range(self.log_list.count())
                 if not self.log_list.item(i).isHidden()),
                None)
            if current_item is not None and not restored:
                current_item.setSelected(True)
        self.log_list.setCurrentItem(
            current_item, QItemSelectionModel.SelectionFlag.NoUpdate)
        if current_item is None:
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
        items = self.log_list.selectedItems()
        if not items:
            return
        names = "\n".join(f"- {item.text()}" for item in items)
        answer = QMessageBox.question(
            self, "Delete Logs",
            f"Permanently delete {len(items)} selected log(s)?\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        failures = []
        for item in items:
            try:
                Path(item.data(Qt.ItemDataRole.UserRole)).unlink()
            except OSError as error:
                failures.append(f"{item.text()}: {error}")
        self.refresh_logs()
        if failures:
            self.window.message(
                "Could Not Delete Logs", "\n".join(failures),
                QMessageBox.Icon.Critical)
