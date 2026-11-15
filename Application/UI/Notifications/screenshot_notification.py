"""Animated, non-activating notification for saved screenshots."""

from PyQt6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, Qt,
    QSize, QTimer, pyqtSignal,
)
from pathlib import Path

from PyQt6.QtGui import QMovie, QPalette
from PyQt6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QLabel, QVBoxLayout, QWidget,
)

from Core.Runtime.application_runtime import log
from UI.Audio.sounds import play_sound


class ScreenshotNotification(QWidget):
    dismissed = pyqtSignal()

    def __init__(self, image, screen, config, title="Screenshot saved",
                 preview_path=None):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(title)
        self.setAccessibleName(title)

        area = screen.availableGeometry()
        width = min(int(config.get("screenshot_notification_width", 360)),
                    max(260, area.width() - 36))
        height = min(int(config.get("screenshot_notification_height", 240)),
                     max(160, area.height() - 36))
        self.setGeometry(area)
        self._position = config.get("screenshot_position", "bottom-right")
        self._animation_duration = int(
            config.get("screenshot_animation_duration", 600))
        self._duration = int(config.get("screenshot_duration", 3))
        self._play_sounds = bool(
            config.get("screenshot_notification_sounds", True))
        self._sound_config = config
        self._preview_path = None
        self._movie = None
        self._closing = False
        self._closed = False
        self._opened = False
        self._animation = None

        self._card = QFrame(self)
        self._card.setObjectName("ScreenshotNotificationCard")
        self._card.setFixedSize(width, height)
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        palette = self.palette()
        background = palette.color(QPalette.ColorRole.Window)
        foreground = palette.color(QPalette.ColorRole.WindowText)
        border = palette.color(QPalette.ColorRole.Mid)
        self._card.setPalette(palette)
        self._card.setAutoFillBackground(True)
        self._card.setStyleSheet(
            "QFrame#ScreenshotNotificationCard {"
            f"background-color: {background.name()}; "
            f"color: {foreground.name()}; "
            f"border: 1px solid {border.name()}; "
            "border-radius: 9px; }"
            "QLabel { background-color: transparent; "
            f"color: {foreground.name()}; border: none; }}")
        layout = QVBoxLayout(self._card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(title_label)
        self._thumbnail = QLabel()
        self._thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_size = QSize(max(1, width - 28), max(1, height - 58))
        self._thumbnail.setPixmap(image.scaled(
            self._preview_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(self._thumbnail, 1)
        if preview_path:
            self.set_animated_preview(preview_path)

        margin = 18
        x = area.x() + margin if self._position.endswith("left") \
            else area.x() + area.width() - width - margin
        y = area.y() + margin if self._position.startswith("top") \
            else area.y() + area.height() - height - margin
        self._target_pos = QPoint(x - area.x(), y - area.y())
        self._offscreen_pos = QPoint(area.width() + 12, self._target_pos.y())
        self._card.move(self._offscreen_pos)
        self._opacity_effect = QGraphicsOpacityEffect(self._card)
        self._opacity_effect.setOpacity(0.0)
        self._card.setGraphicsEffect(self._opacity_effect)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

    def set_animated_preview(self, preview_path):
        path = Path(preview_path)
        if self._closing or self._closed:
            self._remove_preview_file(path)
            return False
        movie = QMovie(str(path))
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        movie.setScaledSize(self._preview_size)
        if not path.is_file() or not movie.isValid():
            log(f"Animated clip preview is invalid: {path}")
            self._remove_preview_file(path)
            return False
        if self._movie is not None:
            self._movie.stop()
        self._preview_path = path
        self._movie = movie
        self._thumbnail.setMovie(movie)
        movie.start()
        return True

    @staticmethod
    def _remove_preview_file(path):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            log(f"Could not remove temporary clip preview: {error}")

    def showEvent(self, event):
        super().showEvent(event)
        if self._opened:
            return
        self._opened = True
        if self._play_sounds:
            play_sound("notification_in", self._sound_config)
        QTimer.singleShot(0, self._start_enter_animation)

    def _start_enter_animation(self):
        if self._closing or not self.isVisible():
            return
        self._card.move(self._offscreen_pos)
        self._opacity_effect.setOpacity(0.0)
        animation = QParallelAnimationGroup(self)
        slide = QPropertyAnimation(self._card, b"pos", animation)
        slide.setDuration(self._animation_duration)
        slide.setStartValue(self._offscreen_pos)
        slide.setEndValue(self._target_pos)
        slide.setEasingCurve(QEasingCurve.Type.InOutSine)
        fade = QPropertyAnimation(self._opacity_effect, b"opacity", animation)
        fade.setDuration(self._animation_duration)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.InOutSine)
        animation.addAnimation(slide)
        animation.addAnimation(fade)
        self._animation = animation
        animation.finished.connect(
            lambda: self._dismiss_timer.start(max(1, self._duration) * 1000))
        animation.start()

    def dismiss(self):
        if self._closing or not self.isVisible():
            return
        self._closing = True
        self._dismiss_timer.stop()
        if self._play_sounds:
            play_sound("notification_out", self._sound_config)
        if (self._animation is not None
                and self._animation.state()
                == QParallelAnimationGroup.State.Running):
            self._animation.stop()

        animation = QParallelAnimationGroup(self)
        slide = QPropertyAnimation(self._card, b"pos", animation)
        slide.setDuration(self._animation_duration)
        slide.setStartValue(self._card.pos())
        slide.setEndValue(self._offscreen_pos)
        slide.setEasingCurve(QEasingCurve.Type.InOutSine)
        fade = QPropertyAnimation(self._opacity_effect, b"opacity", animation)
        fade.setDuration(self._animation_duration)
        fade.setStartValue(self._opacity_effect.opacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InOutSine)
        animation.addAnimation(slide)
        animation.addAnimation(fade)
        animation.finished.connect(self._finish_dismissal)
        self._animation = animation
        animation.start()

    def _finish_dismissal(self):
        self.close()
        self.dismissed.emit()

    def closeEvent(self, event):
        self._closed = True
        if self._movie is not None:
            self._movie.stop()
        if self._preview_path is not None:
            self._remove_preview_file(self._preview_path)
        super().closeEvent(event)
