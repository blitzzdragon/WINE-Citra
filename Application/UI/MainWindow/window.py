"""Main window and prefix management for WINE Citra."""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import traceback
import urllib.request

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import (
    APP_NAME, APP_VERSION, LOG_DIR, PREFIX_BASE, PROJECT_ROOT,
    apply_gpu_env, build_env, get_prefix_arch, is_inside_prefix_base,
    list_prefixes, load_config, log, prefix_exists, resolve_wine, safe_name,
)
from UI.Tabs.logs import LogsTab
from UI.Tabs.overview import LauncherTab
from UI.Tabs.settings import SettingsTab
from UI.Toolbar.toolbar import StatusToolbar


def open_path(parent, path):
    path = Path(path)
    if not path.exists():
        QMessageBox.warning(parent, APP_NAME, f"Path does not exist:\n{path}")
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))


SETTINGS_MEMORY = PROJECT_ROOT / "SettingsMemory" / "settings_selection.json"


def load_selection():
    try:
        data = json.loads(SETTINGS_MEMORY.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_selection(data):
    try:
        SETTINGS_MEMORY.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_MEMORY.write_text(
            json.dumps(data, indent=4) + "\n", encoding="utf-8")
    except OSError:
        pass


# ============================================================
# DXVK INSTALLER THREAD
# ============================================================

class DXVKWorker(QThread):
    done = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, prefix, wine_binary):
        super().__init__()
        self.prefix = Path(prefix)
        self.wine_binary = wine_binary

    @staticmethod
    def _pick_asset(assets):
        for a in assets:
            name = a.get("name", "")
            if (name.startswith("dxvk-") and name.endswith(".tar.gz")
                    and "native" not in name and "async" not in name
                    and a.get("browser_download_url")):
                return a
        return None

    def run(self):
        try:
            self.progress.emit("Checking for the latest DXVK release...")
            req = urllib.request.Request(
                "https://api.github.com/repos/doitsujin/dxvk/releases/latest",
                headers={"User-Agent": "WINE-Citra",
                         "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                release = json.loads(r.read().decode("utf-8"))

            asset = self._pick_asset(release.get("assets", []))
            if not asset:
                raise RuntimeError("Could not find a DXVK release archive.")

            self.progress.emit(f"Downloading {asset['name']}...")
            with tempfile.TemporaryDirectory(prefix="wine-citra-dxvk-") as tmp:
                tmp = Path(tmp)
                archive_path = tmp / "dxvk.tar.gz"
                req = urllib.request.Request(asset["browser_download_url"],
                                             headers={"User-Agent": "WINE-Citra"})
                with urllib.request.urlopen(req, timeout=120) as r, \
                        open(archive_path, "wb") as out:
                    shutil.copyfileobj(r, out)

                if archive_path.stat().st_size < 1024:
                    raise RuntimeError("The downloaded archive looks incomplete.")

                self.progress.emit("Extracting DXVK...")
                dest = tmp / "extracted"
                dest.mkdir()
                with tarfile.open(archive_path, "r:gz") as tar:
                    for m in tar.getmembers():
                        p = Path(m.name)
                        if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk():
                            raise RuntimeError("Unsafe file found in DXVK archive.")
                    try:
                        tar.extractall(dest, filter="data")
                    except TypeError:      # Python < 3.12
                        tar.extractall(dest)

                scripts = list(dest.rglob("setup_dxvk.sh"))
                if not scripts:
                    raise RuntimeError("DXVK setup script was not found.")

                self.progress.emit("Installing DXVK into the prefix...")
                env = os.environ.copy()
                env["WINEPREFIX"] = str(self.prefix)
                env["WINEDEBUG"] = "-all"
                if self.wine_binary:
                    env["PATH"] = str(Path(self.wine_binary).parent) + os.pathsep + env.get("PATH", "")

                result = subprocess.run(
                    ["bash", str(scripts[0]), "install"], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, timeout=300)
                if result.returncode != 0:
                    raise RuntimeError("DXVK installation failed:\n\n" + result.stdout[-4000:])

            self.done.emit(True, f"DXVK {release.get('tag_name', '')} installed.")
        except Exception as e:
            log(traceback.format_exc())
            self.done.emit(False, str(e))


# ============================================================
# PREFIX TAB
# ============================================================

class PrefixesTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.worker = None
        self.wineboot = None
        self.boot_timer = None
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.prefix_list = QListWidget()
        self.prefix_list.currentItemChanged.connect(self.update_prefix_details)
        splitter.addWidget(self.prefix_list)

        details = QWidget()
        dl = QVBoxLayout(details)
        self.prefix_name_label = QLabel("No prefix selected")
        f = self.prefix_name_label.font()
        f.setBold(True)
        self.prefix_name_label.setFont(f)
        self.prefix_path_label = QLabel("")
        self.prefix_path_label.setWordWrap(True)
        self.prefix_arch_label = QLabel("")
        dl.addWidget(self.prefix_name_label)
        dl.addWidget(self.prefix_path_label)
        dl.addWidget(self.prefix_arch_label)
        dl.addStretch()
        splitter.addWidget(details)
        splitter.setSizes([260, 420])
        layout.addWidget(splitter, 1)

        row1 = QHBoxLayout()
        self.btn_new_prefix = QPushButton("New Prefix...")
        self.btn_new_prefix.clicked.connect(self.new_prefix)
        self.btn_dxvk = QPushButton("Install DXVK")
        self.btn_dxvk.clicked.connect(self.install_dxvk)
        self.btn_winetricks = QPushButton("Winetricks")
        self.btn_winetricks.clicked.connect(self.run_winetricks)
        self.btn_winecfg = QPushButton("Winecfg")
        self.btn_winecfg.clicked.connect(self.run_winecfg)
        for b in (self.btn_new_prefix, self.btn_dxvk,
                  self.btn_winetricks, self.btn_winecfg):
            row1.addWidget(b)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(self.open_selected_prefix)
        del_btn = QPushButton("Delete Prefix")
        del_btn.clicked.connect(self.delete_prefix)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.window.refresh_prefixes)
        for b in (open_btn, del_btn, refresh):
            row2.addWidget(b)
        layout.addLayout(row2)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.prefix_status = QLabel("")
        self.prefix_status.setWordWrap(True)
        layout.addWidget(self.prefix_status)

    def selected_prefix(self):
        item = self.prefix_list.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def reload(self):
        selected = self.selected_prefix()
        self.prefix_list.blockSignals(True)
        self.prefix_list.clear()
        for p in list_prefixes():
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.prefix_list.addItem(item)
            if selected and p == selected:
                self.prefix_list.setCurrentItem(item)
        self.prefix_list.blockSignals(False)
        self.update_prefix_details()

    def update_prefix_details(self, *_):
        p = self.selected_prefix()
        if not p:
            self.prefix_name_label.setText("No prefix selected")
            self.prefix_path_label.setText("")
            self.prefix_arch_label.setText("")
            return
        self.prefix_name_label.setText(p.name)
        self.prefix_path_label.setText(str(p))
        state = "initialized" if prefix_exists(p) else "not initialized yet"
        self.prefix_arch_label.setText(f"Type: {get_prefix_arch(p)} ({state})")

    def open_selected_prefix(self):
        p = self.selected_prefix()
        if p:
            open_path(self, p)

    def need_prefix(self):
        p = self.selected_prefix()
        if not p:
            self.window.message("No Prefix", "Select a prefix first.",
                                QMessageBox.Icon.Warning)
        return p

    def prefix_env(self, prefix):
        env = os.environ.copy()
        env["WINEPREFIX"] = str(prefix)
        wine = resolve_wine(self.window.config["default_wine"])
        if wine:
            env["WINE"] = wine
            env["PATH"] = str(Path(wine).parent) + os.pathsep + env.get("PATH", "")
        apply_gpu_env(self.window.config, env)
        return env

    def new_prefix(self):
        if self.wineboot and self.wineboot.poll() is None:
            self.window.message("Busy", "A prefix is already being created.",
                                QMessageBox.Icon.Warning)
            return
        name, ok = QInputDialog.getText(self, "New Prefix", "Prefix name:")
        if not ok or not name.strip():
            return
        wine = resolve_wine(self.window.config["default_wine"])
        if not wine:
            self.window.message("Wine Not Found", "Check the Wine path in Settings.",
                                QMessageBox.Icon.Critical)
            return
        prefix = PREFIX_BASE / safe_name(name.strip())
        if prefix.exists():
            self.window.message("Already Exists", "A prefix with that name already exists.",
                                QMessageBox.Icon.Warning)
            return
        prefix.mkdir(parents=True)
        env = build_env(self.window.config, prefix, True)
        wineboot = shutil.which("wineboot") or None
        cmd = [wineboot, "-u"] if wineboot else [wine, "wineboot", "-u"]
        try:
            self.wineboot = subprocess.Popen(
                cmd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError as e:
            self.window.message("Failed", str(e), QMessageBox.Icon.Critical)
            return
        try:
            (prefix / ".wine-citra-arch").write_text(
                self.window.config["default_arch"], encoding="utf-8")
        except OSError:
            pass
        self.prefix_status.setText(f"Creating {prefix.name}... this can take a minute.")
        self.progress.show()
        self.btn_new_prefix.setEnabled(False)
        self.boot_timer = QTimer(self)
        self.boot_timer.timeout.connect(self.check_wineboot)
        self.boot_timer.start(1000)

    def check_wineboot(self):
        if self.wineboot and self.wineboot.poll() is None:
            return
        self.boot_timer.stop()
        self.progress.hide()
        self.btn_new_prefix.setEnabled(True)
        self.prefix_status.setText("Prefix created.")
        self.window.refresh_prefixes()

    def delete_prefix(self):
        p = self.need_prefix()
        if not p:
            return
        if not is_inside_prefix_base(p):
            self.window.message(
                "Refused", "That folder is not inside the prefix directory.",
                QMessageBox.Icon.Critical)
            return
        if self.window.running:
            self.window.message(
                "Programs Running",
                "Close running programs before deleting a prefix.",
                QMessageBox.Icon.Warning)
            return
        answer = QMessageBox.question(
            self, "Delete Prefix",
            f"Permanently delete '{p.name}'?\n\n"
            "Everything installed inside it will be lost.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(p)
        except Exception as e:
            log(traceback.format_exc())
            self.window.message("Delete Failed", str(e), QMessageBox.Icon.Critical)
            return
        self.window.refresh_prefixes()
        self.prefix_status.setText(f"Deleted {p.name}.")

    def run_winetricks(self):
        p = self.need_prefix()
        if not p:
            return
        tricks = shutil.which("winetricks")
        if not tricks:
            self.window.message("Missing", "Winetricks is not installed.",
                                QMessageBox.Icon.Critical)
            return
        try:
            subprocess.Popen([tricks], env=self.prefix_env(p), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as e:
            self.window.message("Failed", str(e), QMessageBox.Icon.Critical)

    def run_winecfg(self):
        p = self.need_prefix()
        if not p:
            return
        wine = resolve_wine(self.window.config["default_wine"])
        if not wine:
            self.window.message("Wine Not Found", "Check the Wine path in Settings.",
                                QMessageBox.Icon.Critical)
            return
        try:
            subprocess.Popen([wine, "winecfg"], env=self.prefix_env(p),
                             start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.window.message("Failed", str(e), QMessageBox.Icon.Critical)

    def install_dxvk(self):
        p = self.need_prefix()
        if not p:
            return
        if not prefix_exists(p):
            self.window.message(
                "Not Initialized",
                "This prefix has not been created by Wine yet. "
                "Launch a program in it first, or use New Prefix.",
                QMessageBox.Icon.Warning)
            return
        if self.worker and self.worker.isRunning():
            self.window.message("Busy", "DXVK is already being installed.",
                                QMessageBox.Icon.Warning)
            return

        self.btn_dxvk.setEnabled(False)
        self.progress.show()
        self.worker = DXVKWorker(p, resolve_wine(self.window.config["default_wine"]))
        self.worker.progress.connect(self.prefix_status.setText)
        self.worker.done.connect(self.on_dxvk_done)
        self.worker.finished.connect(lambda: self.btn_dxvk.setEnabled(True))
        self.worker.start()

    def on_dxvk_done(self, ok, msg):
        self.progress.hide()
        self.prefix_status.setText(msg)
        self.window.message(
            "DXVK" if ok else "DXVK Failed", msg,
            QMessageBox.Icon.Information if ok else QMessageBox.Icon.Critical)


# ============================================================
# MAIN WINDOW
# ============================================================

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.running = {}            # pid -> (Popen, name)

        self.build_ui()
        self.refresh_all()

    # --------------------------------------------------------
    # UI skeleton
    # --------------------------------------------------------

    def build_ui(self):
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(820, 640)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.launcher_tab = LauncherTab(self)
        self.settings_tab = SettingsTab(self)
        self.prefixes_tab = PrefixesTab(self)
        self.logs_tab = LogsTab(self)
        self.tabs.addTab(self.launcher_tab, "Launcher")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.prefixes_tab, "Prefixes")
        self.tabs.addTab(self.logs_tab, "Logs")
        layout.addWidget(self.tabs)

        self._selection = load_selection()
        try:
            self.tabs.setCurrentIndex(int(self._selection.get("tab", 0)))
        except (TypeError, ValueError):
            self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(self.remember_tab)

        self.toolbar = StatusToolbar()
        self.toolbar.refreshRequested.connect(self.refresh_all)
        self.toolbar.openPrefixesRequested.connect(
            lambda: open_path(self, PREFIX_BASE))
        self.toolbar.openLogsRequested.connect(
            lambda: open_path(self, LOG_DIR))
        layout.addWidget(self.toolbar)

        # Backwards-compatible alias for code that updates the label directly.
        self.status_label = self.toolbar.status_label

    # --------------------------------------------------------
    # Shared helpers
    # --------------------------------------------------------

    def message(self, title, text, icon=QMessageBox.Icon.Information):
        box = QMessageBox(self)
        box.setWindowTitle(f"{APP_NAME} - {title}")
        box.setIcon(icon)
        box.setText(text)
        box.exec()

    def set_status(self, text):
        self.toolbar.set_status(text)

    def on_settings_saved(self):
        self.launcher_tab.update_exe_info()

    def remember_tab(self, index):
        self._selection["tab"] = index
        save_selection(self._selection)

    # --------------------------------------------------------
    # Refresh
    # --------------------------------------------------------

    def refresh_prefixes(self):
        self.prefixes_tab.reload()
        self.launcher_tab.refresh_prefix_combo()

    def refresh_all(self):
        self.refresh_prefixes()
        self.logs_tab.refresh_logs()
        self.launcher_tab.refresh_running()

    def closeEvent(self, event):
        worker = self.prefixes_tab.worker
        if worker and worker.isRunning():
            answer = QMessageBox.question(
                self, "DXVK Running",
                "DXVK is still being installed. Close anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            worker.wait(5000)
        # Wine programs are started in their own session, so they
        # keep running after this window is closed.
        event.accept()


# Old name kept for compatibility with third-party launchers.
RunnerApp = MainWindow
