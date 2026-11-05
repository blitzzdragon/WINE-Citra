"""Settings tab: Wine, graphics, performance and display options."""

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import (
    KIND_LABEL, RES_PATTERN, install_desktop_entry, list_gpus, plan_gpu,
    resolve_wine, save_config,
)


class SettingsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
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
        gpu_box = QGroupBox("Graphics card")
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

        self.gpu_info = QLabel()
        self.gpu_info.setWordWrap(True)
        self.gpu_info.setStyleSheet("color: gray;")
        gl.addWidget(self.gpu_info)
        layout.addWidget(gpu_box)

        self.gpu_combo.currentIndexChanged.connect(self.update_gpu_info)
        self.gpu_driver_combo.currentIndexChanged.connect(self.update_gpu_info)
        self.update_gpu_info()

        perf = QGroupBox("Performance")
        pl = QVBoxLayout(perf)
        self.chk_gamemode = QCheckBox("Use GameMode")
        self.chk_mangohud = QCheckBox("Use MangoHud")
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
        layout.addWidget(perf)

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

    def update_gpu_info(self, *_):
        if self.gpu_list:
            lines = ["Detected GPUs:"]
            for g in self.gpu_list:
                lines.append(f"  \u2022 {g.name} - {KIND_LABEL[g.kind]}"
                             f"  [driver: {g.driver or 'unknown'}]")
        else:
            lines = ["No GPU information available."]
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
        })

        if not save_config(self.window.config):
            self.window.message("Save Failed", "Could not save settings. See the global log.",
                                QMessageBox.Icon.Critical)
            return
        self.window.on_settings_saved()
        self.window.message("Saved", "Settings saved.")

    def add_desktop_entry(self):
        try:
            entry = install_desktop_entry()
        except OSError as e:
            self.window.message("Failed", str(e), QMessageBox.Icon.Critical)
            return
        self.window.message("Done", f"Created:\n{entry}")
