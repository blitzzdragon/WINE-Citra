"""Settings tab: Wine, graphics, audio, performance and display options."""

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QMenu,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from Core.Runtime.application_runtime import (
    CONFIG_DIR, DATA_DIR, KIND_LABEL, RES_PATTERN, bug_fix_check,
    gamemode_status, install_desktop_entry, list_audio_devices, list_gpus,
    list_monitors, nvidia_info, plan_gpu, resolve_wine,
    save_config,
)
from UI.theme import MUTED_TEXT_COLOR
from UI.Notifications.screenshot_notification import ScreenshotNotification


class SettingsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._autosave_ready = False
        self._screenshot_notification_preview = None
        self._retiring_screenshot_previews = []
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
            "QComboBox, QSpinBox, QLineEdit { padding: 3px; }"
            "QLineEdit::clear-button { subcontrol-origin: padding; "
            "subcontrol-position: right center; margin: 0 4px 0 0; "
            "min-width: 18px; max-width: 18px; min-height: 18px; "
            "max-height: 18px; padding: 0; }"
            "QLineEdit QToolButton { min-width: 18px; max-width: 18px; "
            "min-height: 18px; max-height: 18px; padding: 0; margin: 0; }")

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
        capture_tab = QWidget()
        capture_layout = QVBoxLayout(capture_tab)
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

        post_box = QGroupBox("Vulkan post-processing (vkBasalt)")
        post_layout = QVBoxLayout(post_box)
        post_form = QFormLayout()
        self.vkbasalt_combo = QComboBox()
        for label, value in (
                ("Off", "off"), ("FXAA anti-aliasing", "fxaa"),
                ("SMAA anti-aliasing", "smaa"),
                ("CAS sharpening", "cas"),
                ("SMAA + CAS", "smaa:cas")):
            self.vkbasalt_combo.addItem(label, value)
        self.vkbasalt_combo.setMinimumWidth(260)
        self.vkbasalt_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.vkbasalt_combo.setCurrentIndex(
            max(self.vkbasalt_combo.findData(c.get("vkbasalt_effect", "off")), 0))
        self.vkbasalt_combo.setToolTip(
            "Requires vkBasalt and a Vulkan game. The Home key toggles effects "
            "during gameplay. Applies to programs started after changing it.")
        post_form.addRow("Effect:", self.vkbasalt_combo)
        post_layout.addLayout(post_form)
        post_info = QLabel(
            "Use FXAA or SMAA to smooth edges. vkBasalt is optional and only "
            "works with Vulkan through DXVK/VKD3D.")
        post_info.setWordWrap(True)
        post_info.setStyleSheet(
            f"color: {MUTED_TEXT_COLOR}; font-size: 11pt;")
        post_layout.addWidget(post_info)
        post_layout.setSpacing(10)
        gl.addWidget(post_box)

        self.gpu_info = QLabel()
        self.gpu_info.setWordWrap(True)
        self.gpu_info.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
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
        self.gamemode_info.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
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
        self.audio_info.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
        al.addWidget(self.audio_info)
        audio_layout.addWidget(audio)

        sound_box = QGroupBox("Launcher sound effects")
        sound_form = QFormLayout(sound_box)
        self.chk_error_sounds = QCheckBox(
            "Play error and crash sounds")
        self.chk_error_sounds.setChecked(
            c.get("error_sounds_enabled", True))
        sound_form.addRow("", self.chk_error_sounds)
        self.error_sound_volume = QSpinBox()
        self.error_sound_volume.setRange(0, 100)
        self.error_sound_volume.setSingleStep(5)
        self.error_sound_volume.setSuffix("%")
        self.error_sound_volume.setValue(c.get("error_sound_volume", 70))
        self.error_sound_volume.setToolTip(
            "Set this to 0% to mute error and crash sounds.")
        sound_form.addRow("Error sound volume:", self.error_sound_volume)
        audio_layout.addWidget(sound_box)
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

        self.chk_launcher_hotkey = QCheckBox(
            "Use F8 to open or focus WINE Citra")
        self.chk_launcher_hotkey.setChecked(
            c.get("launcher_hotkey_enabled", True))
        self.chk_launcher_hotkey.setToolTip(
            "While a WINE Citra-launched program is running, F8 opens the "
            "launcher or brings its existing window forward.")
        dform.addRow("", self.chk_launcher_hotkey)

        self.chk_trap_cursor = QCheckBox(
            "Trap the mouse inside the game window")
        self.chk_trap_cursor.setToolTip(
            "Confines the X11 cursor to the focused Wine window, making "
            "accidental tab-outs much harder. The cursor is released as soon "
            "as the game loses focus.")
        self.chk_trap_cursor.setChecked(c["trap_cursor"])
        dform.addRow("", self.chk_trap_cursor)
        display_layout.addWidget(disp)

        screenshot_box = QGroupBox("Screenshots")
        screenshot_form = QFormLayout(screenshot_box)
        self.chk_screenshots = QCheckBox("Enable F12 screenshot capture")
        self.chk_screenshots.setChecked(c.get("screenshots_enabled", True))
        screenshot_form.addRow("", self.chk_screenshots)

        self.screenshot_position_combo = QComboBox()
        for label, value in (
                ("Top left", "top-left"), ("Top right", "top-right"),
                ("Bottom left", "bottom-left"),
                ("Bottom right", "bottom-right")):
            self.screenshot_position_combo.addItem(label, value)
        self.screenshot_position_combo.setCurrentIndex(
            max(self.screenshot_position_combo.findData(
                c.get("screenshot_position", "bottom-right")), 0))
        screenshot_form.addRow("Preview position:",
                               self.screenshot_position_combo)

        self.screenshot_duration_spin = QSpinBox()
        self.screenshot_duration_spin.setRange(1, 10)
        self.screenshot_duration_spin.setSuffix(" seconds")
        self.screenshot_duration_spin.setValue(
            c.get("screenshot_duration", 3))
        screenshot_form.addRow("Preview duration:",
                               self.screenshot_duration_spin)

        self.screenshot_notification_width = QSpinBox()
        self.screenshot_notification_width.setRange(260, 900)
        self.screenshot_notification_width.setSuffix(" px")
        self.screenshot_notification_width.setValue(
            c.get("screenshot_notification_width", 360))
        screenshot_form.addRow("Notification width:",
                               self.screenshot_notification_width)

        self.screenshot_notification_height = QSpinBox()
        self.screenshot_notification_height.setRange(160, 700)
        self.screenshot_notification_height.setSuffix(" px")
        self.screenshot_notification_height.setValue(
            c.get("screenshot_notification_height", 240))
        screenshot_form.addRow("Notification height:",
                               self.screenshot_notification_height)

        self.screenshot_animation_duration = QSpinBox()
        self.screenshot_animation_duration.setRange(450, 1500)
        self.screenshot_animation_duration.setSingleStep(50)
        self.screenshot_animation_duration.setSuffix(" ms")
        self.screenshot_animation_duration.setValue(
            c.get("screenshot_animation_duration", 600))
        self.screenshot_animation_duration.setToolTip(
            "Longer durations make the slide-in and fade smoother.")
        screenshot_form.addRow("Slide animation:",
                               self.screenshot_animation_duration)

        self.chk_screenshot_notification_sounds = QCheckBox(
            "Play notification sounds")
        self.chk_screenshot_notification_sounds.setChecked(
            c.get("screenshot_notification_sounds", True))
        screenshot_form.addRow("", self.chk_screenshot_notification_sounds)

        self.screenshot_notification_volume = QSpinBox()
        self.screenshot_notification_volume.setRange(0, 100)
        self.screenshot_notification_volume.setSingleStep(5)
        self.screenshot_notification_volume.setSuffix("%")
        self.screenshot_notification_volume.setValue(
            c.get("screenshot_notification_volume", 50))
        self.screenshot_notification_volume.setToolTip(
            "Set this to 0% to mute screenshot notification sounds.")
        screenshot_form.addRow("Notification sound volume:",
                               self.screenshot_notification_volume)

        self.screenshot_notification_preview_button = QPushButton(
            "Preview Notification")
        self.screenshot_notification_preview_button.clicked.connect(
            self.preview_screenshot_notification)
        screenshot_form.addRow("", self.screenshot_notification_preview_button)

        screenshot_path_row = QHBoxLayout()
        self.screenshot_directory_input = QLineEdit(
            c.get("screenshot_directory", str(DATA_DIR / "screenshots")))
        self.screenshot_directory_input.setClearButtonEnabled(True)
        self.screenshot_directory_input.setAccessibleName(
            "Screenshot save folder")
        screenshot_browse = QPushButton("Browse...")
        screenshot_browse.clicked.connect(self.browse_screenshot_directory)
        screenshot_open = QPushButton("Open")
        screenshot_open.clicked.connect(self.open_screenshot_directory)
        screenshot_path_row.addWidget(self.screenshot_directory_input, 1)
        screenshot_path_row.addWidget(screenshot_browse)
        screenshot_path_row.addWidget(screenshot_open)
        screenshot_form.addRow("Save screenshots to:", screenshot_path_row)

        clip_box = QGroupBox("Clip recording")
        clip_form = QFormLayout(clip_box)
        self.chk_clips = QCheckBox("Enable Ctrl+F12 rolling clips")
        self.chk_clips.setChecked(c.get("clips_enabled", True))
        self.chk_clips.setToolTip(
            "Requires FFmpeg. Keeps a rolling buffer for the first Wine "
            "window; wait for the selected clip length after launch or a "
            "window-size change "
            "before saving a clip. Capture restarts at the new size when "
            "entering or leaving fullscreen. Recording can use some CPU.")
        clip_form.addRow("", self.chk_clips)

        self.clip_duration_spin = QSpinBox()
        self.clip_duration_spin.setRange(5, 300)
        self.clip_duration_spin.setSuffix(" seconds")
        self.clip_duration_spin.setValue(c.get("clip_duration", 30))
        self.clip_duration_spin.setToolTip(
            "Choose a rolling clip duration from 5 seconds to 5 minutes. "
            "Changing this restarts the rolling buffer.")
        clip_form.addRow("Clip length:", self.clip_duration_spin)

        self.chk_clip_audio = QCheckBox(
            "Include game audio when a system monitor is available")
        self.chk_clip_audio.setChecked(c.get("clip_audio_enabled", True))
        self.chk_clip_audio.setToolTip(
            "Records the selected output device's PulseAudio/PipeWire monitor. "
            "If no monitor is available, the clip is saved without audio.")
        clip_form.addRow("", self.chk_clip_audio)

        self.chk_clip_animated_preview = QCheckBox(
            "Animated clip preview in the notification (no audio)")
        self.chk_clip_animated_preview.setChecked(
            c.get("clip_animated_preview_enabled", True))
        self.chk_clip_animated_preview.setToolTip(
            "The notification appears immediately, then starts a short silent "
            "loop as soon as FFmpeg renders its preview.")
        clip_form.addRow("", self.chk_clip_animated_preview)

        clip_path_row = QHBoxLayout()
        self.clip_directory_input = QLineEdit(
            c.get("clip_directory", str(DATA_DIR / "clips")))
        self.clip_directory_input.setClearButtonEnabled(True)
        self.clip_directory_input.setAccessibleName("Clip save folder")
        clip_browse = QPushButton("Browse...")
        clip_browse.clicked.connect(self.browse_clip_directory)
        clip_open = QPushButton("Open")
        clip_open.clicked.connect(self.open_clip_directory)
        clip_path_row.addWidget(self.clip_directory_input, 1)
        clip_path_row.addWidget(clip_browse)
        clip_path_row.addWidget(clip_open)
        clip_form.addRow("Save clips to:", clip_path_row)
        capture_layout.addWidget(screenshot_box)
        capture_layout.addWidget(clip_box)

        self.chk_debug = QCheckBox("Write extra Wine debug output to logs")
        self.chk_debug.setChecked(c["wine_debug"])
        advanced_layout.addWidget(self.chk_debug)

        adv_note = QLabel("Advanced options may affect compatibility and performance.")
        adv_note.setWordWrap(True)
        adv_note.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
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

        for tab_layout in (general_layout, graphics_layout, audio_layout,
                           display_layout, capture_layout):
            tab_layout.addStretch()

        self.tabs.addTab(general_tab, "General")
        self.tabs.addTab(graphics_tab, "Graphics")
        self.tabs.addTab(audio_tab, "Audio")
        self.tabs.addTab(display_tab, "Display")
        self.tabs.addTab(capture_tab, "Capture")
        self.tabs.addTab(advanced_tab, "Advanced")

        layout.addWidget(self.tabs)

        save_note = QLabel("Changes are saved automatically.")
        save_note.setWordWrap(True)
        save_note.setStyleSheet(f"color: {MUTED_TEXT_COLOR};")
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
        self.vkbasalt_combo.setMinimumWidth(260)
        self.vkbasalt_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
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
            f"color: {MUTED_TEXT_COLOR};" if available else
            f"color: {self.palette().color(QPalette.ColorRole.BrightText).name()};")

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

    def browse_screenshot_directory(self):
        start = self.screenshot_directory_input.text().strip()
        if not start:
            start = str(DATA_DIR / "screenshots")
        path = QFileDialog.getExistingDirectory(
            self, "Choose Screenshot Folder", start,
            QFileDialog.Option.ShowDirsOnly)
        if path:
            self.screenshot_directory_input.setText(path)

    def open_screenshot_directory(self):
        path = self.screenshot_directory_input.text().strip()
        directory = (Path(path).expanduser() if path
                     else DATA_DIR / "screenshots")
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.window.message(
                "Could Not Open Screenshot Folder", str(error),
                QMessageBox.Icon.Warning)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.window.message(
                "Could Not Open Screenshot Folder",
                f"The desktop could not open:\n{directory}",
                QMessageBox.Icon.Warning)

    def browse_clip_directory(self):
        start = self.clip_directory_input.text().strip()
        if not start:
            start = str(DATA_DIR / "clips")
        path = QFileDialog.getExistingDirectory(
            self, "Choose Clip Folder", start,
            QFileDialog.Option.ShowDirsOnly)
        if path:
            self.clip_directory_input.setText(path)

    def open_clip_directory(self):
        path = self.clip_directory_input.text().strip()
        directory = Path(path).expanduser() if path else DATA_DIR / "clips"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.window.message(
                "Could Not Open Clip Folder", str(error),
                QMessageBox.Icon.Warning)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.window.message(
                "Could Not Open Clip Folder",
                f"The desktop could not open:\n{directory}",
                QMessageBox.Icon.Warning)

    def preview_screenshot_notification(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            self.window.message(
                "Preview Unavailable", "No display screen is available.",
                QMessageBox.Icon.Warning)
            return

        image = QPixmap(640, 360)
        palette = QApplication.palette()
        image.fill(palette.color(QPalette.ColorRole.Base))
        painter = QPainter(image)
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        painter.drawText(
            image.rect(), Qt.AlignmentFlag.AlignCenter,
            "Screenshot notification preview")
        painter.end()

        config = dict(self.window.config)
        config.update({
            "screenshot_position": self.screenshot_position_combo.currentData(),
            "screenshot_duration": self.screenshot_duration_spin.value(),
            "screenshot_notification_width":
                self.screenshot_notification_width.value(),
            "screenshot_notification_height":
                self.screenshot_notification_height.value(),
            "screenshot_animation_duration":
                self.screenshot_animation_duration.value(),
            "screenshot_notification_sounds":
                self.chk_screenshot_notification_sounds.isChecked(),
            "screenshot_notification_volume":
                self.screenshot_notification_volume.value(),
        })
        if (self._screenshot_notification_preview is not None
                and self._screenshot_notification_preview.isVisible()):
            previous = self._screenshot_notification_preview
            previous.dismissed.connect(
                self._release_retiring_screenshot_preview)
            self._retiring_screenshot_previews.append(previous)
            previous.dismiss()
        preview = ScreenshotNotification(image, screen, config)
        self._screenshot_notification_preview = preview
        preview.show()

    def _release_retiring_screenshot_preview(self):
        previous = self.sender()
        self._retiring_screenshot_previews = [
            preview for preview in self._retiring_screenshot_previews
            if preview is not previous]

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
            "launcher_hotkey_enabled": self.chk_launcher_hotkey.isChecked(),
            "trap_cursor": self.chk_trap_cursor.isChecked(),
            "clips_enabled": self.chk_clips.isChecked(),
            "clip_duration": self.clip_duration_spin.value(),
            "clip_audio_enabled": self.chk_clip_audio.isChecked(),
            "clip_animated_preview_enabled":
                self.chk_clip_animated_preview.isChecked(),
            "clip_directory":
                self.clip_directory_input.text().strip()
                or str(DATA_DIR / "clips"),
            "screenshots_enabled": self.chk_screenshots.isChecked(),
            "screenshot_notification_sounds":
                self.chk_screenshot_notification_sounds.isChecked(),
            "screenshot_position":
                self.screenshot_position_combo.currentData(),
            "screenshot_duration": self.screenshot_duration_spin.value(),
            "screenshot_notification_width":
                self.screenshot_notification_width.value(),
            "screenshot_notification_height":
                self.screenshot_notification_height.value(),
            "screenshot_animation_duration":
                self.screenshot_animation_duration.value(),
            "screenshot_notification_volume":
                self.screenshot_notification_volume.value(),
            "screenshot_directory":
                self.screenshot_directory_input.text().strip()
                or str(DATA_DIR / "screenshots"),
            "launch_monitor": self.monitor_combo.currentData() or "",
            "audio_output": self.audio_output_combo.currentData() or "",
            "audio_input": self.audio_input_combo.currentData() or "",
            "enable_nvapi": self.chk_nvapi.isChecked(),
            "vkd3d_raytracing": self.chk_raytracing.isChecked(),
            "vkbasalt_effect": self.vkbasalt_combo.currentData(),
            "error_sounds_enabled": self.chk_error_sounds.isChecked(),
            "error_sound_volume": self.error_sound_volume.value(),
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
