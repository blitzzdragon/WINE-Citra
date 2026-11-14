"""Browse WINE Citra releases and their release notes."""

import json

from datetime import datetime

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import (
    QNetworkAccessManager, QNetworkReply, QNetworkRequest,
)
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import APP_VERSION, save_config

RELEASES_URL = "https://github.com/blitzzdragon/WINE-Citra/releases"
API_URL = "https://api.github.com/repos/blitzzdragon/WINE-Citra/releases"
API_PAGE_SIZE = 100


class UpdatesTab(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._raw_releases = []
        self._releases = []
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.current_version = QLabel(f"Installed version: {APP_VERSION}")
        self.current_version.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.current_version)

        controls = QHBoxLayout()
        self.release_combo = QComboBox()
        self.release_combo.setAccessibleName("Available versions")
        self.release_combo.setToolTip(
            "Choose a version to view its release notes and download.")
        self.release_combo.currentIndexChanged.connect(self.show_release)
        controls.addWidget(self.release_combo, 1)

        self.chk_prerelease = QCheckBox("Include beta versions")
        self.chk_prerelease.setChecked(
            bool(self.window.config.get("show_prerelease", False)))
        self.chk_prerelease.setToolTip(
            "Beta versions may be incomplete or less stable.")
        self.chk_prerelease.toggled.connect(self.prerelease_changed)
        controls.addWidget(self.chk_prerelease)
        layout.addLayout(controls)

        self.status = QLabel("Opening this tab checks for updates automatically.")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Update status")
        layout.addWidget(self.status)

        self.card = QFrame()
        self.card.setObjectName("releaseCard")
        self.card.setFrameShape(QFrame.Shape.StyledPanel)
        self.card.setStyleSheet(
            "QFrame#releaseCard { border: 1px solid palette(mid); "
            "border-radius: 8px; }")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        heading = QHBoxLayout()
        self.release_title = QLabel("Release notes")
        self.release_title.setWordWrap(True)
        self.release_title.setStyleSheet("font-size: 1.2em; font-weight: 600;")
        heading.addWidget(self.release_title, 1)
        self.release_type = QLabel("")
        self.release_type.setAlignment(Qt.AlignmentFlag.AlignRight)
        heading.addWidget(self.release_type)
        card_layout.addLayout(heading)

        self.release_date = QLabel("")
        self.release_date.setStyleSheet("color: palette(mid);")
        card_layout.addWidget(self.release_date)

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName("Selected version release notes")
        self.notes.setAccessibleDescription(
            "Release details and changes for the selected version.")
        self.notes.setMinimumHeight(180)
        self.notes.setMaximumHeight(300)
        card_layout.addWidget(self.notes, 1)
        layout.addWidget(self.card, 1)

        actions = QHBoxLayout()
        self.btn_check = QPushButton("Check for updates")
        self.btn_check.clicked.connect(self.check_updates)
        self.btn_check.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Fixed)
        self.btn_open = QPushButton("Open release page")
        self.btn_open.clicked.connect(self.open_release_page)
        self.btn_download = QPushButton("Download package")
        self.btn_download.clicked.connect(self.download_release)
        actions.addWidget(self.btn_check)
        actions.addWidget(self.btn_open)
        actions.addWidget(self.btn_download)
        layout.addLayout(actions)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._auto_check)

    def _auto_check(self):
        if self.isVisible():
            self.check_updates()

    def prerelease_changed(self, enabled):
        self.window.config["show_prerelease"] = bool(enabled)
        saved = save_config(self.window.config)
        self._populate_releases(self._raw_releases)
        if not saved:
            self.status.setText(
                "Could not save the beta-version preference; it will reset "
                "when WINE Citra closes.")

    def check_updates(self):
        if self._reply is not None:
            return
        self.status.setText("Checking for versions...")
        self.btn_check.setEnabled(False)
        request = QNetworkRequest(
            QUrl(f"{API_URL}?per_page={API_PAGE_SIZE}"))
        request.setRawHeader(b"User-Agent", b"WINE-Citra")
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(15000)
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda r=reply: self._on_reply(r))

    def _on_reply(self, reply):
        if reply is not self._reply:
            reply.deleteLater()
            return
        self._reply = None
        self.btn_check.setEnabled(True)

        if reply.error() != QNetworkReply.NetworkError.NoError:
            status = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            detail = reply.errorString()
            if status:
                detail = f"HTTP {status}: {detail}"
            reply.deleteLater()
            self.status.setText(f"Could not check releases: {detail}")
            return

        payload = bytes(reply.readAll())
        reply.deleteLater()
        try:
            releases = json.loads(payload.decode("utf-8"))
            if not isinstance(releases, list):
                raise ValueError("Unexpected response from GitHub.")
        except (UnicodeDecodeError, ValueError) as error:
            self.status.setText(f"Could not read release information: {error}")
            return
        self._raw_releases = releases
        self._populate_releases(releases)

    def _populate_releases(self, releases):
        show_beta = self.chk_prerelease.isChecked()
        entries = []
        seen = set()
        for release in releases:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            prerelease = bool(release.get("prerelease"))
            if prerelease and not show_beta:
                continue
            version = str(release.get("name") or release.get("tag_name")
                          or "Unnamed version").strip()
            tag = str(release.get("tag_name") or version)
            if tag in seen:
                continue
            seen.add(tag)

            package_url = ""
            for asset in release.get("assets") or []:
                if (isinstance(asset, dict)
                        and str(asset.get("name", "")).lower().endswith(".deb")):
                    package_url = str(asset.get("browser_download_url") or "")
                    if package_url:
                        break
            entries.append({
                "version": version,
                "tag": tag,
                "type": "Beta" if prerelease else "Stable release",
                "published": str(release.get("published_at") or "")[:10],
                "body": str(release.get("body") or "").strip(),
                "page_url": str(release.get("html_url") or RELEASES_URL),
                "package_url": package_url,
            })

        previous_tag = self.release_combo.currentData()
        self.release_combo.blockSignals(True)
        self.release_combo.clear()
        for release in entries:
            label = release["version"]
            if release["type"] == "Beta" and "beta" not in label.lower():
                label += " (Beta)"
            installed = APP_VERSION.lstrip("v")
            published_version = release["tag"].lstrip("v")
            if (published_version == installed
                    or release["version"].lstrip("v") == installed):
                label += " (Installed)"
            self.release_combo.addItem(label, release["tag"])
        self._releases = entries
        restore_index = self.release_combo.findData(previous_tag)
        if restore_index < 0:
            restore_index = 0
        self.release_combo.setCurrentIndex(
            restore_index if entries else -1)
        self.release_combo.blockSignals(False)

        self.show_release(self.release_combo.currentIndex())
        checked = datetime.now().strftime("%H:%M:%S")
        if entries:
            self.status.setText(
                f"Found {len(entries)} version(s). Last checked at {checked}.")
        else:
            self.status.setText(
                f"No versions match this filter. Last checked at {checked}.")

    def show_release(self, index):
        release = self._releases[index] if 0 <= index < len(self._releases) else None
        if not release:
            self.release_title.setText("No version selected")
            self.release_type.clear()
            self.release_date.clear()
            self.notes.setPlainText(
                "Check for updates to load available versions and release notes.")
            self.btn_open.setEnabled(False)
            self.btn_download.setEnabled(False)
            return

        self.release_title.setText(release["version"])
        self.release_type.setText(release["type"])
        self.release_date.setText(
            f"Published {release['published']}"
            if release["published"] else "Publication date unavailable")
        if release["body"]:
            self.notes.setMarkdown(release["body"])
        else:
            self.notes.setPlainText("No release notes were provided.")
        self.btn_open.setEnabled(bool(release["page_url"]))
        self.btn_download.setEnabled(bool(release["package_url"]))
        self.btn_download.setToolTip(
            "Download the Debian package for this version."
            if release["package_url"]
            else "No Debian package is attached to this release.")

    def open_release_page(self):
        index = self.release_combo.currentIndex()
        if 0 <= index < len(self._releases):
            QDesktopServices.openUrl(QUrl(self._releases[index]["page_url"]))
        else:
            QDesktopServices.openUrl(QUrl(RELEASES_URL))

    def download_release(self):
        index = self.release_combo.currentIndex()
        if 0 <= index < len(self._releases):
            url = self._releases[index]["package_url"]
            if url:
                QDesktopServices.openUrl(QUrl(url))
