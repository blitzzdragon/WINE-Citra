"""Settings tab: Wine, graphics, audio, performance and display options."""

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QMenu, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import (
    CONFIG_DIR, KIND_LABEL, RES_PATTERN, bug_fix_check,
    gamemode_status, install_desktop_entry, list_audio_devices, list_gpus,
    list_monitors, nvidia_info, plan_gpu, resolve_wine,
    save_config,
)


class SettingsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._autosave_ready = False
        self.build_ui()
        self._connect_autosave()
        self._autosave_ready = True

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(
            "QPushButton { padding: 4px 9px; margin: 1px; }"
            "QComboBox, QSpinBox, QLineEdit { padding: 3px; }")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        c = self.window.config

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Settings categories")
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        graphics_tab = QWidget()
        graphics_layout = QVBoxLayout(graphics_tab)
        audio_tab = QWidget()
        audio_layout = QVBoxLayout(audio_tab)
        display_tab = QWidget()
        display_layout = QVBoxLayout(display_tab)
        advanced_tab = QWidget()
        advanced_layout = QVBoxLayout(advanced_tab)


        wine_box = QGroupBox("Wine")
        form = QFormLayout(wine_box)

        row = QHBoxLayout()
        self.wine_input = QLineEdit(c["default_wine"])
        self.wine_input.setClearButtonEnabled(True)
        self.wine_input.setAccessibleName("Wine executable")
        wb = QPushButton("Browse...")
        wb.clicked.connect(self.browse_wine)
        wb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(self.wine_input, 1)
        row.addWidget(wb)
        form.addRow("Wine executable:", row)

        self.arch_combo = QComboBox()
        self.arch_combo.addItems(["win64", "win32"])
        self.arch_combo.setCurrentText(c["default_arch"])
        self.arch_combo.setToolTip(
            "This applies to new Wine prefixes; existing prefixes keep their type.")
        form.addRow("New prefix type:", self.arch_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("One shared prefix for all programs", "shared")
        self.mode_combo.addItem("A separate prefix for each program", "per_app")
        self.mode_combo.setCurrentIndex(0 if c["prefix_mode"] == "shared" else 1)
        self.mode_combo.setToolTip(
            "Shared prefixes use less disk space. Separate prefixes isolate "
            "each program's Windows settings and installed components.")
        form.addRow("Prefix mode:", self.mode_combo)
        general_layout.addWidget(wine_box)

        launcher_box = QGroupBox("Launcher behavior")
        launcher_layout = QVBoxLayout(launcher_box)
        self.chk_minimize_on_launch = QCheckBox(
            "Minimize the launcher after starting a program")
        self.chk_minimize_on_launch.setToolTip(
            "Wine programs keep running when the launcher is minimized.")
        self.chk_minimize_on_launch.setChecked(
            c.get("minimize_on_launch", False))
        launcher_layout.addWidget(self.chk_minimize_on_launch)
        self.chk_confirm_launch = QCheckBox(
            "Ask before launching a program")
        self.chk_confirm_launch.setToolTip(
            "Review the executable and prefix before every launch.")
        self.chk_confirm_launch.setChecked(c.get("confirm_launch", False))
        launcher_layout.addWidget(self.chk_confirm_launch)
        self.chk_remember_last_executable = QCheckBox(
            "Restore the last program when the launcher opens")
        self.chk_remember_last_executable.setChecked(
            c.get("remember_last_executable", True))
        self.chk_remember_last_executable.setToolTip(
            "Turn this off to avoid storing the most recently launched file.")
        launcher_layout.addWidget(self.chk_remember_last_executable)
        ui_form = QFormLayout()
        self.text_scale_combo = QComboBox()
        for label, scale in (("Standard (100%)", 100), ("Large (115%)", 115),
                             ("Larger (130%)", 130), ("Largest (150%)", 150)):
            self.text_scale_combo.addItem(label, scale)
        scale_index = self.text_scale_combo.findData(
            c.get("ui_text_scale", 100))
        self.text_scale_combo.setCurrentIndex(max(scale_index, 0))
        self.text_scale_combo.setAccessibleName("Interface text size")
        self.text_scale_combo.setToolTip(
            "Increase text size throughout the launcher for easier reading.")
        self.text_scale_combo.currentIndexChanged.connect(
            lambda *_: self.window.apply_accessibility_scale(
                self.text_scale_combo.currentData()))
        ui_form.addRow("Interface text size:", self.text_scale_combo)
        launcher_layout.addLayout(ui_form)
        general_layout.addWidget(launcher_box)

        # ---- GPU ----
        gpu_box = QGroupBox("Graphics card / NVIDIA")
        gl = QVBoxLayout(gpu_box)
        gform = QFormLayout()

        self.gpu_list = list_gpus(with_names=True)
        self.gpu_combo = QComboBox()
        self.fill_gpu_combo(c["gpu_choice"])
        gform.addRow("Run programs on:", self.gpu_combo)

        self.gpu_driver_combo = QComboBox()
        self.gpu_driver_combo.addItem("Auto-detect", "auto")
        self.gpu_driver_combo.addItem("Mesa (AMD / Intel / Nouveau)", "mesa")
        self.gpu_driver_combo.addItem("NVIDIA proprietary", "nvidia")
        self.gpu_driver_combo.setCurrentIndex(
            max(self.gpu_driver_combo.findData(c["gpu_driver"]), 0))
        gform.addRow("Driver (advanced):", self.gpu_driver_combo)
        gl.addLayout(gform)

        self.chk_nvapi = QCheckBox(
            "Enable NVIDIA NVAPI through DXVK/VKD3D (recommended for NVIDIA)")
        self.chk_nvapi.setChecked(c["enable_nvapi"])
        gl.addWidget(self.chk_nvapi)

        self.chk_raytracing = QCheckBox("Enable VKD3D-Proton ray tracing (DXR)")
        self.chk_raytracing.setChecked(c["vkd3d_raytracing"])
        gl.addWidget(self.chk_raytracing)

        self.gpu_info = QLabel()
        self.gpu_info.setWordWrap(True)
        self.gpu_info.setStyleSheet("color: gray;")
        gl.addWidget(self.gpu_info)
        graphics_layout.addWidget(gpu_box)

        self.gpu_combo.currentIndexChanged.connect(self.update_gpu_info)
        self.gpu_driver_combo.currentIndexChanged.connect(self.update_gpu_info)
        self.update_gpu_info()

        # ---- Performance ----
        perf = QGroupBox("Performance")
        pl = QVBoxLayout(perf)
        self.chk_gamemode = QCheckBox("Use GameMode")
        self.chk_esync = QCheckBox("Enable Esync")
        self.chk_fsync = QCheckBox("Enable Fsync")
        self.chk_dxvk_hud = QCheckBox("Show DXVK overlay")
        for chk, key in ((self.chk_gamemode, "use_gamemode"),
                         (self.chk_esync, "enable_esync"),
                         (self.chk_fsync, "enable_fsync"),
                         (self.chk_dxvk_hud, "dxvk_hud")):
            chk.setChecked(c[key])
            pl.addWidget(chk)

        self.gamemode_info = QLabel()
        self.gamemode_info.setWordWrap(True)
        self.gamemode_info.setStyleSheet("color: gray;")
        pl.addWidget(self.gamemode_info)

        graphics_layout.addWidget(perf)

        # ---- Audio ----
        audio = QGroupBox("Audio devices")
        al = QVBoxLayout(audio)
        aform = QFormLayout()
        self.audio_output_combo = QComboBox()
        self.audio_input_combo = QComboBox()
        aform.addRow("Output device:", self.audio_output_combo)
        aform.addRow("Input device:", self.audio_input_combo)
        al.addLayout(aform)

        audio_row = QHBoxLayout()
        self.audio_refresh = QPushButton("Refresh Devices")
        self.audio_refresh.clicked.connect(self.refresh_audio_devices)
        self.audio_refresh.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        audio_row.addWidget(self.audio_refresh)
        audio_row.addStretch()
        al.addLayout(audio_row)

        self.audio_info = QLabel(
            "Choose a device for the game, or leave 'System default' to follow "
            "the desktop's current audio device.")
        self.audio_info.setWordWrap(True)
        self.audio_info.setStyleSheet("color: gray;")
        al.addWidget(self.audio_info)
        audio_layout.addWidget(audio)
        self.refresh_audio_devices()

        # ---- Display ----
        disp = QGroupBox("Display")
        dform = QFormLayout(disp)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(0, 1000)
        self.fps_spin.setValue(c["fps_limit"])
        self.fps_spin.setSpecialValueText("Unlimited")
        dform.addRow("DXVK FPS limit:", self.fps_spin)

        self.chk_virtual = QCheckBox("Use a Wine virtual desktop")
        self.chk_virtual.setToolTip(
            "Run the program inside a managed desktop window at the selected size.")
        self.chk_virtual.setChecked(c["virtual_desktop"])
        dform.addRow("", self.chk_virtual)

        self.res_input = QLineEdit(c["desktop_res"])
        self.res_input.setPlaceholderText("1920x1080")
        self.res_input.setEnabled(self.chk_virtual.isChecked())
        self.chk_virtual.toggled.connect(self.res_input.setEnabled)
        dform.addRow("Desktop size:", self.res_input)

        self.monitor_combo = QComboBox()
        self.refresh_monitors(c["launch_monitor"])
        dform.addRow("Launch on monitor:", self.monitor_combo)

        self.chk_fullscreen_keys = QCheckBox(
            "Use F11 for borderless fullscreen (block Ctrl+Enter)")
        self.chk_fullscreen_keys.setToolTip(
            "F11 toggles borderless fullscreen on the running Windows program.\n"
            "Ctrl+Enter is swallowed so the program never receives it.")
        self.chk_fullscreen_keys.setChecked(c["intercept_fullscreen_keys"])
        dform.addRow("", self.chk_fullscreen_keys)

        self.chk_trap_cursor = QCheckBox(
            "Trap the mouse inside the game window")
        self.chk_trap_cursor.setToolTip(
            "Confines the X11 cursor to the focused Wine window, making "
            "accidental tab-outs much harder. The cursor is released as soon "
            "as the game loses focus.")
        self.chk_trap_cursor.setChecked(c["trap_cursor"])
        dform.addRow("", self.chk_trap_cursor)
        display_layout.addWidget(disp)

        self.chk_debug = QCheckBox("Write extra Wine debug output to logs")
        self.chk_debug.setChecked(c["wine_debug"])
        advanced_layout.addWidget(self.chk_debug)

        adv_note = QLabel("Advanced options may affect compatibility and performance.")
        adv_note.setWordWrap(True)
        adv_note.setStyleSheet("color: gray;")
        advanced_layout.addWidget(adv_note)
        advanced_layout.addStretch()

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save_settings)
        save.setDefault(True)
        save.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tools = QToolButton()
        tools.setText("Tools")
        tools.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tools.setAccessibleName("Settings tools")
        tools_menu = QMenu(tools)
        tools_menu.addAction("Add to Applications Menu",
                             self.add_desktop_entry)
        tools_menu.addAction("Run Bug Fix Check", self.run_bug_fix_check)
        tools_menu.addAction(
            "Open Config Folder",
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(CONFIG_DIR))))
        tools.setMenu(tools_menu)
        buttons.addWidget(save, 1)
        buttons.addWidget(tools)

        for tab_layout in (general_layout, graphics_layout, audio_layout, display_layout):
            tab_layout.addStretch()

        self.tabs.addTab(general_tab, "General")
        self.tabs.addTab(graphics_tab, "Graphics")
        self.tabs.addTab(audio_tab, "Audio")
        self.tabs.addTab(display_tab, "Display")
        self.tabs.addTab(advanced_tab, "Advanced")

        layout.addWidget(self.tabs)

        save_note = QLabel("Changes are saved automatically.")
        save_note.setWordWrap(True)
        save_note.setStyleSheet("color: gray;")
        layout.addWidget(save_note)
        layout.addLayout(buttons)

        # Make every control expand to fill available width instead of
        # clustering on the left and leaving dead space on the right.
        for widget in self.findChildren(QComboBox) + self.findChildren(QLineEdit) + self.findChildren(QSpinBox):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            widget.setMinimumWidth(0)
            if isinstance(widget, QComboBox):
                widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                widget.setMinimumContentsLength(8)
        for form in self.findChildren(QFormLayout):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        for checkbox in self.findChildren(QCheckBox):
            checkbox.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            checkbox.setMinimumWidth(0)
        for group in self.findChildren(QGroupBox):
            group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            group.setMinimumWidth(0)

        layout.addStretch()
        self.update_runtime_info()

    # --------------------------------------------------------
    # Populating dynamic choices
    # --------------------------------------------------------

    def fill_gpu_combo(self, current):
        self.gpu_combo.blockSignals(True)
        self.gpu_combo.clear()
        self.gpu_combo.addItem("Auto - dedicated GPU if there is one (recommended)", "auto")
        self.gpu_combo.addItem("Dedicated GPU", "dedicated")
        self.gpu_combo.addItem("Integrated GPU (CPU graphics)", "integrated")
        for g in self.gpu_list:
            self.gpu_combo.addItem(f"Specific: {g.name} ({KIND_LABEL[g.kind]})",
                                   f"pci:{g.pci}")
        self.gpu_combo.addItem("System default (no GPU selection)", "default")
        self.gpu_combo.setCurrentIndex(max(self.gpu_combo.findData(current), 0))
        self.gpu_combo.blockSignals(False)

    def refresh_audio_devices(self):
        devices = list_audio_devices()
        for combo, key, entries in (
                (self.audio_output_combo, "audio_output", devices["outputs"]),
                (self.audio_input_combo, "audio_input", devices["inputs"])):
            current = combo.currentData() or self.window.config.get(key, "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("System default", "")
            for device in entries:
                combo.addItem(device["description"], device["name"])
            index = combo.findData(current)
            combo.setCurrentIndex(max(index, 0))
            combo.blockSignals(False)
        if not devices["outputs"] and not devices["inputs"]:
            self.audio_info.setText(
                "No PulseAudio/PipeWire devices were found. The game will use "
                "the system default; install pulseaudio-utils for device lists.")

    def refresh_monitors(self, current=""):
        self.monitor_combo.clear()
        self.monitor_combo.addItem("Current monitor (system default)", "")
        for monitor in list_monitors():
            self.monitor_combo.addItem(
                f"{monitor['name']} ({monitor['width']}x{monitor['height']})",
                monitor["name"])
        index = self.monitor_combo.findData(current or "")
        self.monitor_combo.setCurrentIndex(max(index, 0))

    # --------------------------------------------------------
    # Status text
    # --------------------------------------------------------

    def update_runtime_info(self):
        self.update_gpu_info()
        available, note = gamemode_status()
        self.gamemode_info.setText(
            ("GameMode: " if available else "GameMode unavailable: ") + note)
        self.gamemode_info.setStyleSheet(
            "color: gray;" if available else "color: #b05a00;")

    def update_gpu_info(self, *_):
        if self.gpu_list:
            lines = ["Detected GPUs:"]
            for g in self.gpu_list:
                lines.append(f"  \u2022 {g.name} - {KIND_LABEL[g.kind]}"
                             f"  [driver: {g.driver or 'unknown'}]")
        else:
            lines = ["No GPU information available."]

        nvidia = nvidia_info()
        if nvidia["detected"]:
            lines.append("")
            lines.append(f"NVIDIA: {nvidia['name'] or 'detected'}"
                         f"  driver {nvidia['driver'] or 'unknown'}"
                         f"  Vulkan ICD {'found' if nvidia['icd'] else 'missing'}")
        else:
            lines.append("")
            lines.append("NVIDIA: no NVIDIA GPU detected on this machine.")

        _, note = plan_gpu(self.gpu_combo.currentData(),
                           self.gpu_driver_combo.currentData(), self.gpu_list)
        lines.append("")
        lines.append(note)
        self.gpu_info.setText("\n".join(lines))

    def browse_wine(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Wine Executable",
                                              "/usr/bin", "All Files (*)")
        if path:
            self.wine_input.setText(path)

    # --------------------------------------------------------
    # Saving
    # --------------------------------------------------------

    def _read_settings(self):
        return {
            "default_wine": self.wine_input.text().strip(),
            "default_arch": self.arch_combo.currentText(),
            "prefix_mode": self.mode_combo.currentData(),
            "minimize_on_launch": self.chk_minimize_on_launch.isChecked(),
            "confirm_launch": self.chk_confirm_launch.isChecked(),
            "remember_last_executable":
                self.chk_remember_last_executable.isChecked(),
            "ui_text_scale": self.text_scale_combo.currentData(),
            "gpu_choice": self.gpu_combo.currentData(),
            "gpu_driver": self.gpu_driver_combo.currentData(),
            "use_gamemode": self.chk_gamemode.isChecked(),
            "enable_esync": self.chk_esync.isChecked(),
            "enable_fsync": self.chk_fsync.isChecked(),
            "dxvk_hud": self.chk_dxvk_hud.isChecked(),
            "fps_limit": self.fps_spin.value(),
            "virtual_desktop": self.chk_virtual.isChecked(),
            "desktop_res": self.res_input.text().strip(),
            "wine_debug": self.chk_debug.isChecked(),
            "intercept_fullscreen_keys": self.chk_fullscreen_keys.isChecked(),
            "trap_cursor": self.chk_trap_cursor.isChecked(),
            "launch_monitor": self.monitor_combo.currentData() or "",
            "audio_output": self.audio_output_combo.currentData() or "",
            "audio_input": self.audio_input_combo.currentData() or "",
            "enable_nvapi": self.chk_nvapi.isChecked(),
            "vkd3d_raytracing": self.chk_raytracing.isChecked(),
        }

    def _connect_autosave(self):
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(500)
        self._autosave_timer.timeout.connect(self._autosave)
        for widget in self.findChildren(QCheckBox):
            widget.toggled.connect(self._schedule_autosave)
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self._schedule_autosave)
        for widget in self.findChildren(QSpinBox):
            widget.valueChanged.connect(self._schedule_autosave)
        for widget in self.findChildren(QLineEdit):
            widget.textChanged.connect(self._schedule_autosave)

    def _schedule_autosave(self, *_):
        if self._autosave_ready:
            self._autosave_timer.start()

    def flush_autosave(self):
        """Save any pending change immediately, e.g. when the app closes."""
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()
            self._autosave()

    def _autosave(self):
        """Persist valid settings quietly after a control changes."""
        if not self._autosave_ready:
            return
        values = self._read_settings()
        res = values["desktop_res"]
        if RES_PATTERN.fullmatch(res):
            values["desktop_res"] = res
        else:
            values.pop("desktop_res")
        wine_path = values["default_wine"]
        if wine_path and resolve_wine(wine_path):
            values["default_wine"] = wine_path
        else:
            values.pop("default_wine")
        self.window.config.update(values)
        if not values["remember_last_executable"]:
            self.window.config["last_executable"] = ""
        if not save_config(self.window.config):
            self.window.set_status(
                "Could not auto-save settings; see the global log.")
        else:
            self.window.on_settings_saved()

    def save_settings(self):
        self._autosave_timer.stop()
        res = self.res_input.text().strip()
        if not RES_PATTERN.fullmatch(res):
            self.window.message("Invalid Size", "Enter a size such as 1920x1080.",
                                QMessageBox.Icon.Warning)
            return

        wine_path = self.wine_input.text().strip()
        if not wine_path or not resolve_wine(wine_path):
            self.window.message(
                "Wine Not Found",
                "That Wine executable could not be found or is not executable.",
                QMessageBox.Icon.Warning)
            return

        self.window.config.update(self._read_settings())
        if not self.window.config["remember_last_executable"]:
            self.window.config["last_executable"] = ""

        if not save_config(self.window.config):
            self.window.message("Save Failed", "Could not save settings. See the global log.",
                                QMessageBox.Icon.Critical)
            return
        self.update_runtime_info()
        self.window.on_settings_saved()
        self.window.message("Saved", "Settings saved.")

    def add_desktop_entry(self):
        try:
            entry = install_desktop_entry()
        except OSError as e:
            self.window.message("Failed", str(e), QMessageBox.Icon.Critical)
            return
        self.window.message("Done", f"Created:\n{entry}")

    def run_bug_fix_check(self):
        issues = bug_fix_check(self.window.config)
        self.window.message("Bug Fix Check", "\n\n".join(issues))
