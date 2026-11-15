"""Browse, preview and play saved game clips."""

import shutil
import subprocess
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QStyle, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import DATA_DIR, log
from UI.Tabs.media_gallery_actions import (
    delete_media_items, rename_media,
)

CLIP_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
THUMBNAIL_CACHE_LIMIT = 128


class _ThumbnailSignals(QObject):
    completed = pyqtSignal(str, str, bytes, str)


class _ClipThumbnailWorker(QRunnable):
    def __init__(self, path, token, signals):
        super().__init__()
        self.path = Path(path)
        self.token = token
        self.signals = signals

    def run(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.signals.completed.emit(
                str(self.path), self.token, b"", "FFmpeg is not installed.")
            return
        try:
            result = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-ss", "2", "-i", str(self.path), "-an",
                    "-frames:v", "1", "-vf",
                    "scale=392:220:force_original_aspect_ratio=decrease:"
                    "force_divisible_by=2",
                    "-f", "image2pipe", "-vcodec", "png", "pipe:1",
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=12, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            self.signals.completed.emit(
                str(self.path), self.token, b"", str(error))
            return
        if result.returncode or not result.stdout:
            detail = result.stderr.decode("utf-8", "replace").strip()
            self.signals.completed.emit(
                str(self.path), self.token, b"",
                detail or "FFmpeg could not decode a thumbnail.")
            return
        self.signals.completed.emit(
            str(self.path), self.token, result.stdout, "")


class ClipsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._gallery_directory = None
        self._gallery_snapshot = None
        self._gallery_items = {}
        self._gallery_tokens = {}
        self._pending_tokens = set()
        self._thumbnail_cache = OrderedDict()
        self._thumbnail_pool = QThreadPool(self)
        self._thumbnail_pool.setMaxThreadCount(2)
        self._thumbnail_signals = _ThumbnailSignals(self)
        self._thumbnail_signals.completed.connect(self._thumbnail_ready)
        self.build_ui()
        self.refresh_gallery()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self.refresh_gallery)
        self._refresh_timer.start()

    def build_ui(self):
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.status = QLabel("Saved clips")
        toolbar.addWidget(self.status, 1)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(
            lambda: self.refresh_gallery(force=True))
        toolbar.addWidget(self.refresh_button)

        self.open_button = QPushButton("Play Selected")
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(self.open_clip)
        toolbar.addWidget(self.open_button)

        self.rename_button = QPushButton("Rename")
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self.rename_clip)
        toolbar.addWidget(self.rename_button)

        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_clip)
        toolbar.addWidget(self.delete_button)

        self.open_folder_button = QPushButton("Open Folder")
        self.open_folder_button.clicked.connect(self.open_folder)
        toolbar.addWidget(self.open_folder_button)
        layout.addLayout(toolbar)

        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setFlow(QListWidget.Flow.LeftToRight)
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setMovement(QListWidget.Movement.Static)
        self.gallery.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self.gallery.setWrapping(True)
        self.gallery.setWordWrap(True)
        self.gallery.setIconSize(QSize(196, 112))
        self.gallery.setGridSize(QSize(224, 152))
        self.gallery.setSpacing(8)
        self.gallery.setAccessibleName("Clip library")
        self.gallery.setAccessibleDescription(
            "Select a clip preview and activate Play Selected to open it.")
        self.gallery.itemDoubleClicked.connect(self.open_clip)
        self.gallery.itemSelectionChanged.connect(
            self._update_selection_actions)
        layout.addWidget(self.gallery, 1)

        self.empty_label = QLabel("No clips found.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

    def clip_directory(self):
        value = self.window.config.get("clip_directory", "")
        return Path(value).expanduser() if value else DATA_DIR / "clips"

    @staticmethod
    def _format_size(size):
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    def refresh_gallery(self, force=False):
        directory = self.clip_directory()
        try:
            records = []
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() in CLIP_SUFFIXES:
                    stat = path.stat()
                    records.append((path, stat.st_mtime_ns, stat.st_size))
            records.sort(key=lambda item: item[1], reverse=True)
        except FileNotFoundError:
            records = []
        except OSError as error:
            self.status.setText(f"Could not read clips folder: {error}")
            self.empty_label.setText("The clips folder could not be read.")
            self.empty_label.show()
            self._gallery_snapshot = None
            return

        snapshot = tuple((str(path), modified, size)
                         for path, modified, size in records)
        if (not force and directory == self._gallery_directory
                and snapshot == self._gallery_snapshot):
            return
        self._gallery_directory = directory
        self._gallery_snapshot = snapshot
        self.gallery.clear()
        self._gallery_items.clear()
        self._gallery_tokens.clear()

        play_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        for path, modified, size in records:
            token = f"{modified}:{size}"
            item = QListWidgetItem(play_icon, path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            when = datetime.fromtimestamp(modified / 1_000_000_000)
            item.setToolTip(
                f"{path}\n{when:%Y-%m-%d %H:%M:%S} · "
                f"{self._format_size(size)}")
            self.gallery.addItem(item)
            path_key = str(path)
            self._gallery_items[path_key] = item
            self._gallery_tokens[path_key] = token
            cached = self._thumbnail_cache.get((path_key, token))
            if cached is not None:
                self._thumbnail_cache.move_to_end((path_key, token))
                item.setIcon(QIcon(cached))
            elif (path_key, token) not in self._pending_tokens:
                self._pending_tokens.add((path_key, token))
                self._thumbnail_pool.start(
                    _ClipThumbnailWorker(path, token, self._thumbnail_signals))

        count = self.gallery.count()
        self.status.setText(
            f"{count} clip{'s' if count != 1 else ''} in {directory}")
        self.empty_label.setText("No clips found in this folder.")
        self.empty_label.setVisible(count == 0)
        self._update_selection_actions()

    def _selected_paths(self):
        return [
            Path(item.data(Qt.ItemDataRole.UserRole))
            for item in self.gallery.selectedItems()
        ]

    def _update_selection_actions(self):
        paths = self._selected_paths()
        enabled = len(paths) == 1
        self.open_button.setVisible(enabled)
        self.rename_button.setEnabled(enabled)
        self.delete_button.setEnabled(bool(paths))
        count = self.gallery.count()
        selection = f" · {len(paths)} selected" if paths else ""
        self.status.setText(
            f"{count} clip{'s' if count != 1 else ''} in "
            f"{self._gallery_directory or self.clip_directory()}"
            f"{selection}")

    def rename_clip(self):
        paths = self._selected_paths()
        if len(paths) != 1:
            return
        renamed = rename_media(self, paths[0], "Clip")
        if renamed is not None:
            self.refresh_gallery(force=True)
            for index in range(self.gallery.count()):
                item = self.gallery.item(index)
                if Path(item.data(Qt.ItemDataRole.UserRole)) == renamed:
                    self.gallery.setCurrentItem(item)
                    break

    def delete_clip(self):
        paths = self._selected_paths()
        if paths and delete_media_items(self, paths, "Clip"):
            self.refresh_gallery(force=True)

    def _thumbnail_ready(self, path, token, image_data, error):
        key = (path, token)
        self._pending_tokens.discard(key)
        if error:
            log(f"Could not create clip preview for {path}: {error}")
            item = self._gallery_items.get(path)
            if item is not None and self._gallery_tokens.get(path) == token:
                item.setToolTip(f"{path}\nPreview unavailable: {error}")
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(image_data, "PNG"):
            log(f"Could not load generated clip preview for {path}.")
            return
        self._thumbnail_cache[key] = pixmap
        self._thumbnail_cache.move_to_end(key)
        while len(self._thumbnail_cache) > THUMBNAIL_CACHE_LIMIT:
            self._thumbnail_cache.popitem(last=False)

        item = self._gallery_items.get(path)
        if item is not None and self._gallery_tokens.get(path) == token:
            item.setIcon(QIcon(pixmap.scaled(
                self.gallery.iconSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)))

    def open_clip(self, item=None):
        item = item or self.gallery.currentItem()
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.window.message(
                "Could Not Play Clip",
                f"The desktop could not open:\n{path}",
                QMessageBox.Icon.Warning)

    def open_folder(self):
        directory = self.clip_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.window.message("Could Not Open Clips Folder", str(error),
                                QMessageBox.Icon.Warning)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.window.message(
                "Could Not Open Clips Folder",
                f"The desktop could not open:\n{directory}",
                QMessageBox.Icon.Warning)
