"""Play the short UI sound effects shipped with WINE Citra."""

import shutil
from pathlib import Path

from PyQt6.QtCore import QProcess

from Core.Runtime.application_runtime import PROJECT_ROOT, load_config, log

SOUND_FILES = {
    "error": "error.ogg",
    "notification_in": "notification_in.ogg",
    "notification_out": "notification_out.ogg",
}


def play_sound(name, config=None):
    filename = SOUND_FILES.get(name)
    if filename is None:
        raise ValueError(f"Unknown WINE Citra sound: {name}")

    config = config if config is not None else load_config()
    if name == "error":
        if not config.get("error_sounds_enabled", True):
            return False
        volume = config.get("error_sound_volume", 70)
    else:
        if not config.get("screenshot_notification_sounds", True):
            return False
        volume = config.get("screenshot_notification_volume", 50)
    try:
        volume = max(0, min(100, int(volume)))
    except (TypeError, ValueError, OverflowError):
        volume = 70 if name == "error" else 50
    if volume == 0:
        return False

    sound_path = PROJECT_ROOT / "Assets" / "Audio" / filename
    if not sound_path.is_file():
        log(f"Sound effect is missing: {sound_path}")
        return False

    players = []
    paplay = shutil.which("paplay")
    if paplay:
        paplay_volume = round(volume * 655.36)
        players.append((paplay, [
            f"--volume={paplay_volume}", str(sound_path),
        ]))
    ffplay = shutil.which("ffplay")
    if ffplay:
        players.append((ffplay, [
            "-nodisp", "-autoexit", "-loglevel", "quiet", "-nostats",
            "-volume", str(volume),
            str(sound_path),
        ]))

    for program, arguments in players:
        try:
            started, _pid = QProcess.startDetached(
                program, arguments, str(Path.home()))
        except (OSError, RuntimeError, TypeError) as error:
            log(f"Could not play sound effect {filename}: {error}")
            continue
        if started:
            return True

    if not players:
        log("No supported audio player (paplay or ffplay) is installed.")
    else:
        log(f"Could not start an audio player for {filename}.")
    return False
