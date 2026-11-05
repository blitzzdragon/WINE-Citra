"""Launcher tab: choose an executable, launch it and watch running programs."""

import os
import shlex
import signal

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import (
    HOME, choose_prefix, list_prefixes, prefix_exists, save_config, start_wine,
)

AUTO_PREFIX = "Automatic (see Settings)"


class LauncherTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_processes)
        self.timer.start(1000)

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):
        layout = QVBoxLayout(self)

        box = QGroupBox("Program")
        form = QFormLayout(box)

        row = QHBoxLayout()
        self.exe_input = QLineEdit()
        self.exe_input.setPlaceholderText("Path to a Windows .exe")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse_exe)
        row.addWidget(self.exe_input, 1)
        row.addWidget(browse)
        form.addRow("Executable:", row)

        self.args_input = QLineEdit()
        self.args_input.setPlaceholderText('Optional, e.g. -windowed --config "My File.ini"')
        form.addRow("Arguments:", self.args_input)

        self.prefix_combo = QComboBox()
        form.addRow("Prefix:", self.prefix_combo)

        self.exe_info = QLabel("")
        self.exe_info.setWordWrap(True)
        form.addRow("", self.exe_info)

        # Connect once here (refresh_prefix_combo used to reconnect every call).
        self.exe_input.textChanged.connect(self.update_exe_info)
        self.prefix_combo.currentIndexChanged.connect(self.update_exe_info)

        layout.addWidget(box)

        self.btn_launch = QPushButton("Launch")
        self.btn_launch.clicked.connect(self.launch_selected)
        layout.addWidget(self.btn_launch)

        run_box = QGroupBox("Running programs")
        run_layout = QVBoxLayout(run_box)
        self.running_list = QListWidget()
        run_layout.addWidget(self.running_list)
        self.btn_stop = QPushButton("Stop selected")
        self.btn_stop.clicked.connect(self.stop_selected)
        run_layout.addWidget(self.btn_stop)
        layout.addWidget(run_box, 1)

    # --------------------------------------------------------
    # Actions
    # --------------------------------------------------------

    def browse_exe(self):
        last = self.window.config["last_executable"]
        start = str(Path(last).parent) if last else str(HOME)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Windows Executable", start,
            "Windows Executables (*.exe *.EXE);;All Files (*)")
        if path:
            self.exe_input.setText(path)

    def selected_prefix_override(self):
        data = self.prefix_combo.currentData()
        return data or None

    def update_exe_info(self, *_):
        exe = self.exe_input.text().strip()
        if not exe:
            self.exe_info.setText("")
            return
        prefix = choose_prefix(self.window.config, exe, self.selected_prefix_override())
        state = "exists" if prefix_exists(prefix) else "will be created on launch"
        self.exe_info.setText(f"Prefix: {prefix} ({state})")

    def refresh_prefix_combo(self):
        current = self.prefix_combo.currentData()
        self.prefix_combo.blockSignals(True)
        self.prefix_combo.clear()
        self.prefix_combo.addItem(AUTO_PREFIX, None)
        for p in list_prefixes():
            self.prefix_combo.addItem(p.name, str(p))
        idx = self.prefix_combo.findData(current)
        self.prefix_combo.setCurrentIndex(max(idx, 0))
        self.prefix_combo.blockSignals(False)

    def launch_selected(self):
        exe = self.exe_input.text().strip()
        if not exe:
            self.window.message("No Program", "Please select an executable first.",
                                QMessageBox.Icon.Warning)
            return
        try:
            args = shlex.split(self.args_input.text())
        except ValueError as e:
            self.window.message("Invalid Arguments", str(e), QMessageBox.Icon.Warning)
            return

        try:
            proc, _prefix, _cmd = start_wine(
                self.window.config, exe, args, self.selected_prefix_override())
        except RuntimeError as e:
            self.window.message("Launch Failed", str(e), QMessageBox.Icon.Critical)
            return

        self.window.running[proc.pid] = (proc, Path(exe).name)
        self.window.config["last_executable"] = str(Path(exe).expanduser().resolve())
        save_config(self.window.config)
        self.refresh_running()
        self.window.refresh_prefixes()
        self.update_exe_info()

    def poll_processes(self):
        changed = False
        for pid, (proc, name) in list(self.window.running.items()):
            code = proc.poll()
            if code is not None:
                del self.window.running[pid]
                changed = True
                if code != 0:
                    self.window.set_status(f"{name} exited with code {code} (see Logs)")
                else:
                    self.window.set_status(f"{name} finished.")
        if changed:
            self.refresh_running()

    def refresh_running(self):
        self.running_list.clear()
        for pid, (proc, name) in self.window.running.items():
            item = QListWidgetItem(f"{name}  (PID {pid})")
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self.running_list.addItem(item)
        if self.window.running:
            self.window.set_status(f"{len(self.window.running)} program(s) running")

    def stop_selected(self):
        item = self.running_list.currentItem()
        if not item:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid not in self.window.running:
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
