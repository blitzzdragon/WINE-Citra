"""Compact status bar with quick application actions."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class StatusToolbar(QWidget):
    refreshRequested = pyqtSignal()
    openPrefixesRequested = pyqtSignal()
    openLogsRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label, 1)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Refresh prefixes, logs and running programs")
        self.refresh_button.clicked.connect(self.refreshRequested)
        layout.addWidget(self.refresh_button)

        self.prefixes_button = QPushButton("Prefixes Folder")
        self.prefixes_button.setToolTip("Open the folder that contains all prefixes")
        self.prefixes_button.clicked.connect(self.openPrefixesRequested)
        layout.addWidget(self.prefixes_button)

        self.logs_button = QPushButton("Logs Folder")
        self.logs_button.setToolTip("Open the folder that contains the logs")
        self.logs_button.clicked.connect(self.openLogsRequested)
        layout.addWidget(self.logs_button)

    def set_status(self, text):
        self.status_label.setText(text)
