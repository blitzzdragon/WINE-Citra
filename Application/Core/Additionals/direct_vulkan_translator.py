"""DirectX-to-Vulkan translation helpers for WINE Citra.

Wine itself can translate some Direct3D calls, but modern games expect DXVK
(Direct3D 9/10/11) and VKD3D-Proton (Direct3D 12).  This module does not
bundle either project; it detects the DLLs already installed in a prefix and
sets the environment variables that make the translation layers behave well
under Wine, including NVIDIA-specific NVAPI support.
"""

from pathlib import Path

SHADER_CACHE_DIR = Path.home() / ".cache/wine-citra/shaders"

# DLLs that indicate a translation layer is present in a Windows prefix.
DXVK_DLLS = ("d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll")
VKD3D_DLLS = ("d3d12.dll", "d3d12core.dll", "vkd3d-proton.cfg")


def _system32(prefix):
    return Path(prefix) / "drive_c" / "windows" / "system32"


def translation_layers(prefix):
    """Return the translation layers installed in *prefix*.

    The result is a dict such as ``{"dxvk": True, "vkd3d": False}``.
    A missing or new prefix simply reports no installed layers.
    """
    system32 = _system32(prefix) if prefix else None
    if not system32 or not system32.is_dir():
        return {"dxvk": False, "vkd3d": False}

    def present(names):
        return any((system32 / name).exists() for name in names)

    return {"dxvk": present(DXVK_DLLS), "vkd3d": present(VKD3D_DLLS)}


def describe_translation(prefix):
    """Return a short human-readable summary for Settings and logs."""
    layers = translation_layers(prefix)
    found = [name for name, on in (("DXVK", layers["dxvk"]),
                                   ("VKD3D-Proton", layers["vkd3d"])) if on]
    if not found:
        return "No DirectX translation DLLs found in this prefix yet."
    return "Detected: " + ", ".join(found) + "."


def apply_translation_env(env, config, prefix=None, nvidia=False):
    """Populate *env* with DXVK/VKD3D runtime settings.

    The shader cache keeps Vulkan pipeline compilation out of the first few
    minutes of gameplay.  NVAPI is enabled only when an NVIDIA GPU is being
    used, because some AMD/Intel drivers do not advertise the extensions it
    expects.
    """
    try:
        SHADER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        env["DXVK_STATE_CACHE"] = "1"
        env["DXVK_STATE_CACHE_PATH"] = str(SHADER_CACHE_DIR)
    except OSError:
        # A read-only home is unusual, but the game should still launch.
        env.pop("DXVK_STATE_CACHE_PATH", None)

    # DXVK logs can be noisy.  Keep them quiet unless Wine debugging is on.
    if config.get("wine_debug"):
        env["DXVK_LOG_LEVEL"] = "info"
    else:
        env["DXVK_LOG_LEVEL"] = "none"

    # NVAPI exposes NVIDIA-specific driver features through DXVK/VKD3D.
    if nvidia and config.get("enable_nvapi", True):
        env["PROTON_ENABLE_NVAPI"] = "1"
    else:
        env.pop("PROTON_ENABLE_NVAPI", None)

    # Ray tracing in VKD3D-Proton is opt-in.
    if config.get("vkd3d_raytracing"):
        env["VKD3D_CONFIG"] = "dxr"
    else:
        env.pop("VKD3D_CONFIG", None)

    return env
