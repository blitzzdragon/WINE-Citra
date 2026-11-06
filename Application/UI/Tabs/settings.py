"""Settings tab: Wine, graphics, audio, performance and display options."""

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import (
    KIND_LABEL, MANGOHUD_POSITIONS, RES_PATTERN, gamemode_status,
    install_desktop_entry, list_audio_devices, list_gpus, list_monitors,
    mangohud_status, nvidia_info, plan_gpu, resolve_wine, save_config,
)


class SettingsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.build_ui()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        c = self.window.config

        wine_box = QGroupBox("Wine")
        form = QFormLayout(wine_box)

        row = QHBoxLayout()
        self.wine_input = QLineEdit(c["default_wine"])
        wb = QPushButton("Browse...")
        wb.clicked.connect(self.browse_wine)
        row.addWidget(self.wine_input, 1)
        row.addWidget(wb)
        form.addRow("Wine executable:", row)

        self.arch_combo = QComboBox()
        self.arch_combo.addItems(["win64", "win32"])
        self.arch_combo.setCurrentText(c["default_arch"])
        form.addRow("New prefix type:", self.arch_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("One shared prefix for all programs", "shared")
        self.mode_combo.addItem("A separate prefix for each program", "per_app")
        self.mode_combo.setCurrentIndex(0 if c["prefix_mode"] == "shared" else 1)
        form.addRow("Prefix mode:", self.mode_combo)
        layout.addWidget(wine_box)

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
        layout.addWidget(gpu_box)

        self.gpu_combo.currentIndexChanged.connect(self.update_gpu_info)
        self.gpu_driver_combo.currentIndexChanged.connect(self.update_gpu_info)
        self.update_gpu_info()

        # ---- Performance ----
        perf = QGroupBox("Performance")
        pl = QVBoxLayout(perf)
        self.chk_gamemode = QCheckBox("Use GameMode")
        self.chk_mangohud = QCheckBox("Use MangoHud overlay")
        self.chk_esync = QCheckBox("Enable Esync")
        self.chk_fsync = QCheckBox("Enable Fsync")
        self.chk_dxvk_hud = QCheckBox("Show DXVK overlay")
        for chk, key in ((self.chk_gamemode, "use_gamemode"),
                         (self.chk_mangohud, "use_mangohud"),
                         (self.chk_esync, "enable_esync"),
                         (self.chk_fsync, "enable_fsync"),
                         (self.chk_dxvk_hud, "dxvk_hud")):
            chk.setChecked(c[key])
            pl.addWidget(chk)

        self.gamemode_info = QLabel()
        self.gamemode_info.setWordWrap(True)
        self.gamemode_info.setStyleSheet("color: gray;")
        pl.addWidget(self.gamemode_info)

        mh_form = QFormLayout()
        self.mangohud_position = QComboBox()
        for position in MANGOHUD_POSITIONS:
            self.mangohud_position.addItem(position, position)
        self.mangohud_position.setCurrentIndex(max(
            self.mangohud_position.findData(c["mangohud_position"]), 0))
        mh_form.addRow("MangoHud position:", self.mangohud_position)

        self.mangohud_scale = QSpinBox()
        self.mangohud_scale.setRange(50, 200)
        self.mangohud_scale.setSuffix("%")
        self.mangohud_scale.setValue(c["mangohud_scale"])
        mh_form.addRow("MangoHud scale:", self.mangohud_scale)
        pl.addLayout(mh_form)

        self.mangohud_info = QLabel()
        self.mangohud_info.setWordWrap(True)
        self.mangohud_info.setStyleSheet("color: gray;")
        pl.addWidget(self.mangohud_info)
        layout.addWidget(perf)

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
        audio_row.addWidget(self.audio_refresh)
        audio_row.addStretch()
        al.addLayout(audio_row)

        self.audio_info = QLabel(
            "Choose a device for the game, or leave 'System default' to follow "
            "the desktop's current audio device.")
        self.audio_info.setWordWrap(True)
        self.audio_info.setStyleSheet("color: gray;")
        al.addWidget(self.audio_info)
        layout.addWidget(audio)
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
        self.chk_virtual.setChecked(c["virtual_desktop"])
        dform.addRow("", self.chk_virtual)

        self.res_input = QLineEdit(c["desktop_res"])
        self.res_input.setPlaceholderText("1920x1080")
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
        layout.addWidget(disp)

        self.chk_debug = QCheckBox("Write extra Wine debug output to logs")
        self.chk_debug.setChecked(c["wine_debug"])
        layout.addWidget(self.chk_debug)

        buttons = QHBoxLayout()
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save_settings)
        entry = QPushButton("Add to Applications Menu")
        entry.setToolTip("Lets you right-click a .exe and open it with WINE Citra")
        entry.clicked.connect(self.add_desktop_entry)
        buttons.addWidget(save)
        buttons.addWidget(entry)
        layout.addLayout(buttons)

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
        self.mangohud_info.setText(mangohud_status(self.window.config))

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

    def save_settings(self):
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

        self.window.config.update({
            "default_wine": wine_path,
            "default_arch": self.arch_combo.currentText(),
            "prefix_mode": self.mode_combo.currentData(),
            "gpu_choice": self.gpu_combo.currentData(),
            "gpu_driver": self.gpu_driver_combo.currentData(),
            "use_gamemode": self.chk_gamemode.isChecked(),
            "use_mangohud": self.chk_mangohud.isChecked(),
            "enable_esync": self.chk_esync.isChecked(),
            "enable_fsync": self.chk_fsync.isChecked(),
            "dxvk_hud": self.chk_dxvk_hud.isChecked(),
            "fps_limit": self.fps_spin.value(),
            "virtual_desktop": self.chk_virtual.isChecked(),
            "desktop_res": res,
            "wine_debug": self.chk_debug.isChecked(),
            "intercept_fullscreen_keys": self.chk_fullscreen_keys.isChecked(),
            "trap_cursor": self.chk_trap_cursor.isChecked(),
            "launch_monitor": self.monitor_combo.currentData() or "",
            "audio_output": self.audio_output_combo.currentData() or "",
            "audio_input": self.audio_input_combo.currentData() or "",
            "enable_nvapi": self.chk_nvapi.isChecked(),
            "vkd3d_raytracing": self.chk_raytracing.isChecked(),
            "mangohud_position": self.mangohud_position.currentData(),
            "mangohud_scale": self.mangohud_scale.value(),
        })

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
