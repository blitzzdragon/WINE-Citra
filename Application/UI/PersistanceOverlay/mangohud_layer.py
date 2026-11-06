"""MangoHud overlay layer for WINE Citra.

MangoHud is an in-process Vulkan/OpenGL overlay: the ``mangohud`` launcher
preloads it into the Wine process.  Because it is part of that process it is
automatically drawn above the Wine window, follows that window between
monitors and desktops, and disappears when the game exits.  This module keeps
the overlay configuration in one place and scales it for the monitor the game
is launched on.
"""

from Core.System.Dependancy.dependancy_check import find_tool


def resolve_mangohud():
    """Return the MangoHud launcher path, or None when it is unavailable."""
    return find_tool("mangohud")


def overlay_available():
    return bool(resolve_mangohud())


def _font_size(monitor):
    """Pick a readable HUD size for the target monitor."""
    if not monitor:
        return 22
    shortest = min(int(monitor.get("width", 0)),
                   int(monitor.get("height", 0))) or 1080
    # 1080p -> 24, 1440p -> 32, 4K -> 46.  Clamp for very small panels.
    return max(14, min(48, round(shortest / 45)))


def build_mangohud_env(config, monitor=None):
    """Return environment variables for a native, monitor-scaled overlay.

    ``MANGOHUD_CONFIG`` is passed straight to MangoHud.  The values here are
    intentionally conservative: they look native, scale with the monitor and
    stay out of the way during gameplay.
    """
    position = config.get("mangohud_position", "top-left")
    font_size = _font_size(monitor)
    scale = config.get("mangohud_scale", 100)
    try:
        scale = max(50, min(200, int(scale)))
    except (TypeError, ValueError):
        scale = 100
    font_size = max(10, round(font_size * scale / 100))

    options = [
        "fps",
        "frametime",
        "frame_timing",
        "gpu_stats",
        "cpu_stats",
        "ram",
        "vram",
        f"font_size={font_size}",
        f"position={position}",
        "background_alpha=0.42",
        "round_corners=8",
        "text_outline",
        "alpha=1.0",
        "toggle_hud=Shift_R+F12",
        "toggle_fps_limit=Shift_L+F1",
    ]

    return {
        "MANGOHUD": "1",
        "MANGOHUD_CONFIG": ",".join(options),
    }


def summary(config, monitor=None):
    """Short description used by Settings and the launch log."""
    if not overlay_available():
        return "MangoHud is not installed."
    return (f"MangoHud overlay ready ({_font_size(monitor)}px, "
            f"{config.get('mangohud_position', 'top-left')}).")
