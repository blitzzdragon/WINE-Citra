"""Launcher tab: choose an executable, launch it and watch running programs."""

import os
import shlex
import signal

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import (
    HOME, application_log, choose_prefix, focus_wine_process, list_prefixes,
    prefix_exists, save_config, start_wine,
)
from UI.Audio.sounds import play_sound
from UI.Dialouges.crash_popup import show_game_crash_dialog

AUTO_PREFIX = "Automatic (default in Settings)"


class LauncherTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.exe_paths = {}
        self._intentional_stops = set()
        self.build_ui()
        last = (self.window.config.get("last_executable", "")
                if self.window.config.get("remember_last_executable", True)
                else "")
        if last and Path(last).is_file():
            self.exe_input.setText(last)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_processes)
        self.timer.start(1000)

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        box = QGroupBox("Start a Windows program")
        form = QFormLayout(box)

        row = QHBoxLayout()
        self.exe_input = QLineEdit()
        self.exe_input.setPlaceholderText("Choose a Windows .exe file")
        self.exe_input.setClearButtonEnabled(True)
        self.exe_input.setAccessibleName("Windows program executable")
        self.exe_input.setAccessibleDescription(
            "Enter a Windows executable path, browse for a file, or drop an "
            "executable here.")
        self.exe_input.returnPressed.connect(self.launch_selected)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse_exe)
        browse.setShortcut("Ctrl+O")
        browse.setToolTip("Choose a Windows executable (Ctrl+O)")
        browse.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.exe_input, 1)
        row.addWidget(browse)
        form.addRow("Executable:", row)

        layout.addWidget(box)

        self.btn_launch = QPushButton("Launch")
        self.btn_launch.clicked.connect(self.launch_selected)
        self.btn_launch.setShortcut("Ctrl+Return")
        self.btn_launch.setMinimumHeight(36)
        self.btn_launch.setAccessibleDescription(
            "Start the selected Windows program with the chosen options.")
        layout.addWidget(self.btn_launch)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced launch options")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setAccessibleName("Advanced launch options")
        self.advanced_toggle.setAccessibleDescription(
            "Show optional program arguments and a manual Wine prefix choice.")
        layout.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.advanced_options = QWidget()
        advanced_form = QFormLayout(self.advanced_options)
        self.args_input = QLineEdit()
        self.args_input.setPlaceholderText("Optional program arguments")
        self.args_input.setAccessibleName("Program arguments")
        advanced_form.addRow("Arguments:", self.args_input)

        self.prefix_combo = QComboBox()
        self.prefix_combo.setAccessibleName("Wine prefix")
        advanced_form.addRow("Prefix:", self.prefix_combo)

        self.exe_info = QLabel(
            "Prefix assignment follows the default selected in Settings.")
        self.exe_info.setWordWrap(True)
        self.exe_info.setAccessibleName("Selected prefix information")
        advanced_form.addRow("", self.exe_info)
        self.advanced_options.hide()
        layout.addWidget(self.advanced_options)
        self.advanced_toggle.toggled.connect(self.advanced_options.setVisible)
        self.advanced_toggle.toggled.connect(
            lambda expanded: self.advanced_toggle.setArrowType(
                Qt.ArrowType.DownArrow if expanded
                else Qt.ArrowType.RightArrow))
        self.exe_input.textChanged.connect(self.update_exe_info)
        self.prefix_combo.currentIndexChanged.connect(self.update_exe_info)

        run_box = QGroupBox("Running programs")
        run_layout = QVBoxLayout(run_box)
        self.running_list = QListWidget()
        self.running_list.setAccessibleName("Running Windows programs")
        self.running_list.setAccessibleDescription(
            "Double-click a running program to bring its window forward. "
            "Select one to open its log or stop it.")
        self.running_list.itemDoubleClicked.connect(
            self.focus_selected_application)
        self.running_list.currentItemChanged.connect(
            self._update_running_actions)
        run_layout.addWidget(self.running_list)
        self.running_empty = QLabel("No programs are running.")
        self.running_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        run_layout.addWidget(self.running_empty)
        run_buttons = QHBoxLayout()
        self.btn_open_log = QPushButton("Open Log")
        self.btn_open_log.clicked.connect(self.open_selected_log)
        self.btn_open_log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_stop = QPushButton("Stop selected")
        self.btn_stop.clicked.connect(self.stop_selected)
        self.btn_stop.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_stop.setAccessibleDescription(
            "Stop the selected running Windows program.")
        run_buttons.addWidget(self.btn_open_log)
        run_buttons.addWidget(self.btn_stop)
        run_layout.addLayout(run_buttons)
        layout.addWidget(run_box, 1)

        self._update_running_actions()
        self.setAcceptDrops(True)

    # --------------------------------------------------------
    # Actions
    # --------------------------------------------------------

    def browse_exe(self):
        last = self.window.config.get("last_executable", "")
        start = str(Path(last).parent) if last else str(HOME)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Windows Executable", start,
            "Windows Executables (*.exe *.EXE);;All Files (*)")
        if path:
            self.exe_input.setText(path)

    def open_selected_log(self, *_):
        item = self.running_list.currentItem()
        if not item:
            self.window.message("No Program", "Select a running program first.",
                                QMessageBox.Icon.Warning)
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        exe = self.exe_paths.get(pid)
        if not exe:
            return
        log_path = application_log(exe)
        if not log_path.exists():
            self.window.message("No Log", "No log file exists for this program yet.",
                                QMessageBox.Icon.Warning)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))

    def focus_selected_application(self, item=None):
        item = item or self.running_list.currentItem()
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if focus_wine_process(pid):
            name = self.window.running.get(pid, (None, item.text()))[1]
            self.window.set_status(f"Focused {name}.")
        else:
            self.window.set_status(
                "Could not find the running program window to bring forward.")

    # Drag and drop an .exe onto the launcher.
    def dragEnterEvent(self, event):
        if any(url.toLocalFile().lower().endswith(".exe")
               for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".exe") and Path(path).is_file():
                self.exe_input.setText(path)
                event.acceptProposedAction()
                return

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

    def _update_running_actions(self, *_):
        count = self.running_list.count()
        selected = self.running_list.currentItem() is not None
        self.btn_open_log.setEnabled(selected)
        self.btn_stop.setEnabled(selected)
        self.running_list.setVisible(count > 0)
        self.running_empty.setVisible(count == 0)

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

        prefix = choose_prefix(
            self.window.config, exe, self.selected_prefix_override())
        if self.window.config.get("confirm_launch", False):
            answer = QMessageBox.question(
                self, "Confirm Launch",
                f"Start this Windows program?\n\n{exe}\n\nWine prefix:\n{prefix}",
                QMessageBox.StandardButton.Ok
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Ok:
                return

        try:
            proc, _prefix, _cmd = start_wine(
                self.window.config, exe, args, self.selected_prefix_override())
        except RuntimeError as e:
            self.window.message("Launch Failed", str(e), QMessageBox.Icon.Critical)
            return

        self.window.running[proc.pid] = (proc, Path(exe).name)
        self.exe_paths[proc.pid] = str(Path(exe).expanduser().resolve())
        self.window.config["last_executable"] = (
            str(Path(exe).expanduser().resolve())
            if self.window.config.get("remember_last_executable", True) else "")
        if not save_config(self.window.config):
            self.window.set_status(
                "Program launched, but settings could not be saved.")
        self.refresh_running()
        self.window.refresh_prefixes()
        self.update_exe_info()
        if self.window.config.get("minimize_on_launch", False):
            self.window.showMinimized()

    def poll_processes(self):
        changed = False
        for pid, (proc, name) in list(self.window.running.items()):
            code = proc.poll()
            if code is not None:
                exe = self.exe_paths.get(pid)
                del self.window.running[pid]
                self.exe_paths.pop(pid, None)
                changed = True
                if code != 0:
                    if pid not in self._intentional_stops:
                        play_sound("error", self.window.config)
                        if exe:
                            show_game_crash_dialog(
                                name, code, application_log(exe))
                    self.window.set_status(
                        f"{name} exited with code {code} (see Logs)")
                else:
                    self.window.set_status(f"{name} finished.")
                self._intentional_stops.discard(pid)
        if changed:
            self.refresh_running()

    def refresh_running(self):
        self.running_list.clear()
        for pid, (proc, name) in self.window.running.items():
            item = QListWidgetItem(f"{name}  (PID {pid})")
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self.running_list.addItem(item)
        if self.running_list.count():
            self.running_list.setCurrentRow(0)
        self._update_running_actions()
        if self.window.running:
            self.window.set_status(f"{len(self.window.running)} program(s) running")

    def stop_selected(self):
        item = self.running_list.currentItem()
        if not item:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid not in self.window.running:
            return
        name = self.window.running[pid][1]
        answer = QMessageBox.question(
            self, "Stop Program", f"Stop '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            self._intentional_stops.add(pid)
        except ProcessLookupError:
            self.poll_processes()
        except (PermissionError, OSError) as exc:
            self.window.message(
                "Could Not Stop Program", str(exc),
                QMessageBox.Icon.Warning)
