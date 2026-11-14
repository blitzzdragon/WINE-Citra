"""Game tab: in-game overlay and real-time settings."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QSpinBox,
    QVBoxLayout, QWidget, QPushButton, QCheckBox, QMessageBox,
)

from Core.Runtime.application_runtime import (
    load_config, save_config,
)


class GameOverlayDialog(QDialog):
    """Small overlay that lets a few settings be changed while a game runs."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.config = window.config
        self.setWindowTitle("WINE Citra - Game Overlay")
        self.setModal(False)
        self.resize(360, 260)
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel("Change a few settings in real time. "
                      "Some changes only take effect on the next launch.")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(0, 1000)
        self.fps_spin.setValue(int(self.config.get("fps_limit", 0)))
        self.fps_spin.setSpecialValueText("Unlimited")
        form.addRow("DXVK FPS limit:", self.fps_spin)

        self.chk_trap_cursor = QCheckBox("Trap mouse in game window")
        self.chk_trap_cursor.setChecked(
            bool(self.config.get("trap_cursor", False)))
        form.addRow("", self.chk_trap_cursor)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply_settings)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(apply_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def apply_settings(self):
        self.config.update({
            "fps_limit": self.fps_spin.value(),
            "trap_cursor": self.chk_trap_cursor.isChecked(),
        })
        if save_config(self.config):
            self.window.on_settings_saved()
            self.window.set_status("Game overlay settings applied.")
        else:
            self.window.message("Save Failed", "Could not save settings.",
                                QMessageBox.Icon.Critical)


class GameTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel("Game tab: open the in-game overlay with F9 while a "
                      "program is running to adjust a few settings in real time.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.running_list = None
        from PyQt6.QtWidgets import QListWidget
        self.running_list = QListWidget()
        layout.addWidget(self.running_list, 1)

        row = QHBoxLayout()
        self.btn_overlay = QPushButton("Open Game Overlay")
        self.btn_overlay.clicked.connect(self.open_overlay)
        self.btn_refresh = QPushButton("Refresh Running")
        self.btn_refresh.clicked.connect(self.refresh_running)
        row.addWidget(self.btn_overlay)
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)

        self.refresh_running()

    def refresh_running(self):
        if self.running_list is None:
            return
        self.running_list.clear()
        for pid, (proc, name) in self.window.running.items():
            self.running_list.addItem(f"{name}  (PID {pid})")

    def open_overlay(self):
        self._overlay = GameOverlayDialog(self.window, self)
        self._overlay.show()
