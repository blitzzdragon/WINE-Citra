"""Browse and open saved screenshots."""

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QUrl, QSize
from PyQt6.QtGui import QDesktopServices, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import DATA_DIR
from UI.Tabs.media_gallery_actions import (
    delete_media_items, rename_media,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class ScreenshotsTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._gallery_directory = None
        self._gallery_snapshot = None
        self.build_ui()
        self.refresh_gallery()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(3000)
        self._refresh_timer.timeout.connect(self.refresh_gallery)
        self._refresh_timer.start()

    def build_ui(self):
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.status = QLabel("Saved screenshots")
        toolbar.addWidget(self.status, 1)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(
            lambda: self.refresh_gallery(force=True))
        toolbar.addWidget(self.refresh_button)

        self.rename_button = QPushButton("Rename")
        self.rename_button.setEnabled(False)
        self.rename_button.clicked.connect(self.rename_screenshot)
        toolbar.addWidget(self.rename_button)

        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_screenshot)
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
        self.gallery.setAccessibleName("Screenshot gallery")
        self.gallery.setAccessibleDescription(
            "Double-click a screenshot to open it.")
        self.gallery.itemDoubleClicked.connect(self.open_screenshot)
        self.gallery.itemSelectionChanged.connect(
            self._update_selection_actions)
        layout.addWidget(self.gallery, 1)

        self.empty_label = QLabel("No screenshots found.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

    def screenshot_directory(self):
        value = self.window.config.get("screenshot_directory", "")
        return Path(value).expanduser() if value else DATA_DIR / "screenshots"

    def refresh_gallery(self, force=False):
        directory = self.screenshot_directory()
        try:
            records = []
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                    stat = path.stat()
                    records.append((path, stat.st_mtime_ns, stat.st_size))
            records.sort(key=lambda item: item[1], reverse=True)
        except FileNotFoundError:
            records = []
        except OSError as error:
            self.status.setText(f"Could not read screenshot folder: {error}")
            self.empty_label.setText("The screenshot folder could not be read.")
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

        for path, _modified, _size in records:
            thumbnail = QPixmap(str(path))
            if thumbnail.isNull():
                continue
            item = QListWidgetItem(
                QIcon(thumbnail.scaled(
                    self.gallery.iconSize(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)),
                path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.gallery.addItem(item)

        count = self.gallery.count()
        self.status.setText(
            f"{count} screenshot{'s' if count != 1 else ''} in {directory}")
        self.empty_label.setText("No screenshots found in this folder.")
        self.empty_label.setVisible(count == 0)
        self._update_selection_actions()

    def _selected_paths(self):
        return [
            Path(item.data(Qt.ItemDataRole.UserRole))
            for item in self.gallery.selectedItems()
        ]

    def _update_selection_actions(self):
        paths = self._selected_paths()
        self.rename_button.setEnabled(len(paths) == 1)
        self.delete_button.setEnabled(bool(paths))
        count = self.gallery.count()
        selection = f" · {len(paths)} selected" if paths else ""
        self.status.setText(
            f"{count} screenshot{'s' if count != 1 else ''} in "
            f"{self._gallery_directory or self.screenshot_directory()}"
            f"{selection}")

    def rename_screenshot(self):
        paths = self._selected_paths()
        if len(paths) != 1:
            return
        renamed = rename_media(self, paths[0], "Screenshot")
        if renamed is not None:
            self.refresh_gallery(force=True)
            for index in range(self.gallery.count()):
                item = self.gallery.item(index)
                if Path(item.data(Qt.ItemDataRole.UserRole)) == renamed:
                    self.gallery.setCurrentItem(item)
                    break

    def delete_screenshot(self):
        paths = self._selected_paths()
        if paths and delete_media_items(self, paths, "Screenshot"):
            self.refresh_gallery(force=True)

    def open_screenshot(self, item=None):
        item = item or self.gallery.currentItem()
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.window.message(
                "Could Not Open Screenshot",
                f"The desktop could not open:\n{path}",
                QMessageBox.Icon.Warning)

    def open_folder(self):
        directory = self.screenshot_directory()
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
