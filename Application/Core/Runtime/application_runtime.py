#!/usr/bin/env python3
"""Runtime services for WINE Citra.

Everything in this module is independent of the graphical interface: paths,
configuration, logging, GPU selection, prefix management, Wine process
launching, desktop-entry handling and the application bootstrap.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import traceback

from datetime import datetime
from pathlib import Path

from Core.System.dependancy_check import describe_dependencies

# ============================================================
# INFO AND PATHS
# ============================================================

APP_NAME = "WINE Citra"
APP_VERSION = "0.2.11"

HOME = Path.home()
DATA_DIR = HOME / ".local/share/wine-citra"
PREFIX_BASE = DATA_DIR / "prefixes"
LOG_DIR = DATA_DIR / "logs"
CONFIG_DIR = HOME / ".config/wine-citra"
CONFIG_FILE = CONFIG_DIR / "settings.json"
GLOBAL_LOG = LOG_DIR / "wine-citra.log"
SHARED_PREFIX = PREFIX_BASE / "default"

PROJECT_ROOT = Path(__file__).resolve().parents[2]

for _d in (DATA_DIR, PREFIX_BASE, LOG_DIR, CONFIG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "default_wine": "wine",
    "default_arch": "win64",
    "prefix_mode": "shared",       # "shared" or "per_app"
    "gpu_choice": "auto",          # auto / dedicated / integrated / default / pci:<address>
    "gpu_driver": "auto",          # auto / mesa / nvidia (advanced override)
    "use_gamemode": False,
    "use_mangohud": False,
    "enable_esync": True,
    "enable_fsync": False,
    "dxvk_hud": False,
    "fps_limit": 0,
    "virtual_desktop": False,
    "desktop_res": "1920x1080",
    "wine_debug": False,
    "last_executable": "",
}

RES_PATTERN = re.compile(r"\d{3,5}x\d{3,5}")


# ============================================================
# LOGGING
# ============================================================

def log(message):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(GLOBAL_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{message}\n")
    except OSError:
        pass


def application_log(executable):
    path = Path(executable).resolve()
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:8]
    return LOG_DIR / f"{path.stem}_{digest}.log"


# ============================================================
# CONFIG
# ============================================================

def load_config():
    config = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in DEFAULT_CONFIG:
                    if key in loaded:
                        config[key] = loaded[key]
        except Exception:
            log("Could not load configuration:\n" + traceback.format_exc())

    if config["default_arch"] not in ("win64", "win32"):
        config["default_arch"] = "win64"
    if config["prefix_mode"] not in ("shared", "per_app"):
        config["prefix_mode"] = "shared"
    if not (config["gpu_choice"] in GPU_CHOICES
            or (isinstance(config["gpu_choice"], str)
                and PCI_CHOICE.fullmatch(config["gpu_choice"]))):
        config["gpu_choice"] = "auto"
    if config["gpu_driver"] not in GPU_DRIVERS:
        config["gpu_driver"] = "auto"
    if not isinstance(config["desktop_res"], str) or not RES_PATTERN.fullmatch(config["desktop_res"]):
        config["desktop_res"] = "1920x1080"
    try:
        config["fps_limit"] = max(0, min(1000, int(config["fps_limit"])))
    except (ValueError, TypeError):
        config["fps_limit"] = 0
    for key in ("use_gamemode", "use_mangohud", "enable_esync",
                "enable_fsync", "dxvk_hud", "virtual_desktop", "wine_debug"):
        config[key] = bool(config[key])
    return config


def save_config(config):
    tmp = CONFIG_FILE.with_suffix(".tmp")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_FILE)
        return True
    except Exception:
        log("Could not save configuration:\n" + traceback.format_exc())
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ============================================================
# GPU DETECTION / SELECTION
# ============================================================
#
# "Integrated" = graphics built into the CPU/APU (shares system RAM).
# "Dedicated"  = a separate graphics card with its own VRAM.
# Detection uses sysfs only (fast, no extra tools), so it is a best
# effort. Anything misdetected can be fixed by picking the exact GPU
# in Settings.

GPU_VENDOR_IDS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}

GPU_CHOICES = ("auto", "dedicated", "integrated", "default")
GPU_DRIVERS = ("auto", "mesa", "nvidia")
PCI_ADDR = r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]"
PCI_CHOICE = re.compile(r"pci:" + PCI_ADDR)
KIND_LABEL = {"integrated": "integrated (CPU graphics)", "discrete": "dedicated"}

# Variables this app manages. They are cleared first so a stale value
# inherited from the parent environment never leaks into Wine.
GPU_ENV_VARS = (
    "__NV_PRIME_RENDER_OFFLOAD",
    "__GLX_VENDOR_LIBRARY_NAME",
    "__VK_LAYER_NV_optimus",
    "DRI_PRIME",
)


class GPU:
    def __init__(self, pci, vendor, driver, vram):
        self.pci = pci                  # e.g. 0000:03:00.0
        self.vendor = vendor            # AMD / NVIDIA / Intel / Other
        self.driver = driver            # amdgpu / nvidia / nouveau / i915 / xe ...
        self.vram = vram                # bytes, or None if unknown
        self.kind = "discrete"          # "integrated" or "discrete"
        self.name = vendor

    @property
    def dri_tag(self):
        return "pci-" + self.pci.replace(":", "_").replace(".", "_")


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None


def _classify_gpus(gpus):
    amd = [g for g in gpus if g.vendor == "AMD"]
    max_vram = max((g.vram or 0 for g in amd), default=0)
    for g in gpus:
        if g.vendor == "Intel":
            # Intel iGPUs always sit at PCI 00:02.0; Arc cards do not.
            g.kind = "integrated" if g.pci.split(":", 1)[1] == "00:02.0" else "discrete"
        elif g.vendor == "AMD":
            if g.vram is None:
                g.kind = "discrete"
            elif len(amd) > 1:
                # APUs reserve only a slice of RAM; a real card has far more.
                g.kind = "discrete" if g.vram * 2 > max_vram else "integrated"
            else:
                g.kind = "discrete" if g.vram >= 2 * 1024 ** 3 else "integrated"
        else:
            g.kind = "discrete"         # NVIDIA and others


def _lookup_name(gpu):
    lspci = shutil.which("lspci")
    if lspci:
        try:
            out = subprocess.run([lspci, "-s", gpu.pci, "-mm"], capture_output=True,
                                 text=True, timeout=3).stdout.strip()
            parts = shlex.split(out)
            if len(parts) >= 4:
                return f"{parts[2]} {parts[3]}"
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return gpu.vendor


def list_gpus(with_names=False):
    gpus, seen = [], set()
    try:
        entries = sorted(Path("/sys/class/drm").iterdir())
    except OSError:
        return gpus
    for p in entries:
        if not re.fullmatch(r"card\d+", p.name):
            continue
        dev = p / "device"
        vid = _read(dev / "vendor")
        if not vid:
            continue
        try:
            pci = dev.resolve().name
        except OSError:
            continue
        if not re.fullmatch(PCI_ADDR, pci) or pci in seen:
            continue                    # skip non-PCI (platform) devices
        seen.add(pci)
        driver = ""
        if (dev / "driver").exists():
            try:
                driver = (dev / "driver").resolve().name
            except OSError:
                pass
        vram = _read(dev / "mem_info_vram_total")
        try:
            vram = int(vram) if vram else None
        except ValueError:
            vram = None
        gpus.append(GPU(pci, GPU_VENDOR_IDS.get(vid.lower(), "Other"), driver, vram))
    _classify_gpus(gpus)
    if with_names:
        for g in gpus:
            g.name = _lookup_name(g)
    return gpus


def _pick_dedicated(gpus):
    cards = [g for g in gpus if g.kind == "discrete"]
    cards.sort(key=lambda g: (g.vendor != "NVIDIA", -(g.vram or 0)))
    return cards[0] if cards else None


def _pick_integrated(gpus):
    for g in gpus:
        if g.kind == "integrated":
            return g
    return None


def _env_for_gpu(target, gpus, driver):
    """Environment variables that make OpenGL and Vulkan use `target`."""
    nvidia_proprietary = target.vendor == "NVIDIA" and (
        driver == "nvidia" or (driver == "auto" and target.driver == "nvidia"))
    if driver == "mesa":
        nvidia_proprietary = False

    if nvidia_proprietary:
        return {"__NV_PRIME_RENDER_OFFLOAD": "1",
                "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                "__VK_LAYER_NV_optimus": "NVIDIA_only"}

    env = {"DRI_PRIME": target.dri_tag}
    # If the NVIDIA proprietary driver owns OpenGL on this machine, tell
    # GLVND to use Mesa for the non-NVIDIA GPU we picked.
    if target.vendor != "NVIDIA" and any(
            g.vendor == "NVIDIA" and g.driver == "nvidia" for g in gpus):
        env["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"
    return env


def plan_gpu(choice, driver="auto", gpus=None):
    """Decide which GPU to use. Returns (env_vars_dict, human_description)."""
    gpus = list_gpus() if gpus is None else gpus
    note = ""

    if choice == "default":
        return {}, "System default - no GPU selection."
    if len(gpus) < 2:
        return {}, "Only one GPU detected - nothing to select."

    target = None
    if isinstance(choice, str) and choice.startswith("pci:"):
        target = next((g for g in gpus if g.pci == choice[4:]), None)
        if target is None:
            note = "Selected GPU not found, using Auto. "
            choice = "auto"
    if target is None:
        if choice == "integrated":
            target = _pick_integrated(gpus)
            if target is None:
                return {}, "No integrated GPU found - using system default."
        else:                           # auto / dedicated
            target = _pick_dedicated(gpus)
            if target is None:
                return {}, "No dedicated GPU found - using system default."

    env = _env_for_gpu(target, gpus, driver)
    kind = "integrated" if target.kind == "integrated" else "dedicated"
    return env, f"{note}Using {target.name} ({kind})."


def apply_gpu_env(config, env):
    for key in GPU_ENV_VARS:
        env.pop(key, None)
    plan, _ = plan_gpu(config.get("gpu_choice", "auto"), config.get("gpu_driver", "auto"))
    env.update(plan)


# ============================================================
# HELPERS
# ============================================================

def resolve_wine(binary):
    binary = str(binary).strip()
    if not binary:
        return shutil.which("wine") or shutil.which("wine64")
    if os.path.sep in binary:
        p = Path(binary).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        return None
    return shutil.which(binary)


def safe_name(name):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:60] or "application"


def per_app_prefix(executable):
    executable = str(Path(executable).expanduser().resolve())
    digest = hashlib.sha256(executable.encode()).hexdigest()[:10]
    return PREFIX_BASE / f"{safe_name(Path(executable).stem)}_{digest}"


def choose_prefix(config, executable, override=None):
    if override:
        return Path(override)
    if config.get("prefix_mode") == "per_app":
        return per_app_prefix(executable)
    return SHARED_PREFIX


def list_prefixes():
    try:
        return sorted((p for p in PREFIX_BASE.iterdir() if p.is_dir()),
                      key=lambda p: p.name.lower())
    except OSError:
        return []


def prefix_exists(prefix):
    return (Path(prefix) / "drive_c").is_dir()


def get_prefix_arch(prefix):
    reg = Path(prefix) / "system.reg"
    if reg.exists():
        try:
            head = reg.read_text(encoding="utf-8", errors="ignore")[:2000]
            if "#arch=win64" in head:
                return "win64"
            if "#arch=win32" in head:
                return "win32"
        except OSError:
            pass
    marker = Path(prefix) / ".wine-citra-arch"
    if marker.exists():
        try:
            v = marker.read_text(encoding="utf-8").strip()
            if v in ("win32", "win64"):
                return v
        except OSError:
            pass
    return "Unknown"


def is_inside_prefix_base(path):
    try:
        Path(path).resolve().relative_to(PREFIX_BASE.resolve())
        return Path(path).resolve() != PREFIX_BASE.resolve()
    except ValueError:
        return False


def to_windows_path(unix_path):
    """Map a Linux path to the Z: drive that Wine creates by default."""
    return "Z:" + str(unix_path).replace("/", "\\")


# ============================================================
# WINE ENVIRONMENT / COMMAND
# ============================================================

def build_env(config, prefix, is_new_prefix):
    env = os.environ.copy()
    env["WINEPREFIX"] = str(prefix)
    env["WINEDEBUG"] = "warn+all" if config["wine_debug"] else "-all"

    if is_new_prefix:
        env["WINEARCH"] = config["default_arch"]
    else:
        env.pop("WINEARCH", None)

    for key, on in (("WINEESYNC", config["enable_esync"]),
                    ("WINEFSYNC", config["enable_fsync"])):
        if on:
            env[key] = "1"
        else:
            env.pop(key, None)

    if config["dxvk_hud"]:
        env["DXVK_HUD"] = "fps,frametimes"
    else:
        env.pop("DXVK_HUD", None)

    if config["fps_limit"] > 0:
        env["DXVK_FRAME_RATE"] = str(config["fps_limit"])
    else:
        env.pop("DXVK_FRAME_RATE", None)

    apply_gpu_env(config, env)
    return env


def build_command(config, wine, executable, arguments):
    cmd = []
    if config["use_gamemode"]:
        gm = shutil.which("gamemoderun")
        if gm:
            cmd.append(gm)
    if config["use_mangohud"]:
        mh = shutil.which("mangohud")
        if mh:
            cmd.append(mh)

    cmd.append(wine)

    if config["virtual_desktop"]:
        if not RES_PATTERN.fullmatch(config["desktop_res"]):
            raise ValueError("Invalid virtual desktop resolution.")
        cmd += ["explorer", f"/desktop=WineCitra,{config['desktop_res']}",
                to_windows_path(executable)]
    else:
        cmd.append(executable)

    cmd += list(arguments)
    return cmd


def start_wine(config, executable, arguments=None, prefix_override=None):
    """Start a Windows program detached from this app.

    Returns (Popen, prefix, command). Raises RuntimeError on failure.
    """
    arguments = arguments or []
    executable = str(Path(executable).expanduser().resolve())

    if not Path(executable).is_file():
        raise RuntimeError(f"Executable not found:\n{executable}")
    if Path(executable).suffix.lower() != ".exe":
        raise RuntimeError("Please select a Windows .exe file.")

    wine = resolve_wine(config["default_wine"])
    if not wine:
        raise RuntimeError("Could not find the configured Wine executable.\n"
                           "Check the path in Settings.")

    prefix = choose_prefix(config, executable, prefix_override)
    prefix.mkdir(parents=True, exist_ok=True)
    is_new = not prefix_exists(prefix)

    env = build_env(config, prefix, is_new)
    try:
        cmd = build_command(config, wine, executable, arguments)
    except ValueError as e:
        raise RuntimeError(str(e))

    gpu_vars = {k: env[k] for k in GPU_ENV_VARS if k in env}
    gpu_note = plan_gpu(config["gpu_choice"], config["gpu_driver"])[1]

    logfile = application_log(executable)
    with open(logfile, "a", encoding="utf-8") as out:
        out.write(f"\n\n{'=' * 60}\n"
                  f"Launch: {datetime.now().isoformat(timespec='seconds')}\n"
                  f"Executable: {executable}\n"
                  f"Prefix: {prefix}\n"
                  f"GPU: {gpu_note}\n"
                  f"GPU env: {gpu_vars or 'none'}\n"
                  f"Command: {shlex.join(cmd)}\n\n")
        out.flush()
        try:
            proc = subprocess.Popen(
                cmd, env=env, cwd=str(Path(executable).parent),
                stdin=subprocess.DEVNULL, stdout=out,
                stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            raise RuntimeError(f"Could not start Wine:\n{e}")

    if is_new:
        try:
            (prefix / ".wine-citra-arch").write_text(config["default_arch"], encoding="utf-8")
        except OSError:
            pass

    log(f"Launched {executable}\nPrefix: {prefix}\nGPU: {gpu_note}\nGPU env: {gpu_vars or 'none'}\n"
        f"Command: {shlex.join(cmd)}")
    return proc, prefix, cmd


# ============================================================
# HEADLESS MODE (desktop entry / file association)
# ============================================================

def _desktop_exec():
    """Return the command a .desktop file should use to start the app."""
    launcher = shutil.which("wine-citra")
    if launcher:
        return shlex.quote(launcher)
    main_script = PROJECT_ROOT / "main.py"
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(main_script))}"


def launch_from_desktop(executable, arguments):
    """Run a program with no GUI. Used for 'Open with' and the .desktop entry."""
    config = load_config()
    try:
        start_wine(config, executable, arguments)
    except RuntimeError as e:
        log(f"Desktop launch failed: {e}")
        print(f"{APP_NAME}: {e}", file=sys.stderr)
        return 1
    config["last_executable"] = str(Path(executable).expanduser().resolve())
    save_config(config)
    return 0


def install_desktop_entry():
    apps = HOME / ".local/share/applications"
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / "wine-citra.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Run Windows programs with Wine\n"
        f"Exec={_desktop_exec()} %f\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "MimeType=application/x-ms-dos-executable;application/x-msdownload;"
        "application/vnd.microsoft.portable-executable;\n",
        encoding="utf-8")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return entry


# ============================================================
# ENTRY POINT
# ============================================================

def run(argv=None):
    """Start WINE Citra, or launch a file directly in headless mode."""
    argv = list(sys.argv if argv is None else argv)

    # Headless: a file argument (right-click > Open With, or the .desktop
    # entry) launches the program and exits without ever creating a window.
    if len(argv) > 1:
        return launch_from_desktop(argv[1], argv[2:])

    from PyQt6.QtWidgets import QApplication

    from Core.CrashHandler.crash_service_runtime import install_exception_hook
    from UI.MainWindow.window import MainWindow

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")

    install_exception_hook()

    report = describe_dependencies()
    if report:
        log("Dependency check:\n" + report)

    window = MainWindow()
    window.show()
    return app.exec()


# Kept as a convenience alias for callers that prefer the old name.
main = run
