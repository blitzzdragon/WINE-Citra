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
import select
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback

from datetime import datetime
from pathlib import Path

from Core.System.dependancy_check import describe_dependencies

# Optional X11 support for the F11 / Ctrl+Enter interceptor.  The app stays
# fully usable when python-xlib is not installed; only the key grabbing is
# disabled in that case.
try:
    from Xlib import X as _X
    from Xlib import XK as _XK
    from Xlib import display as _xdisplay
    from Xlib import protocol as _xprotocol
    _XLIB_AVAILABLE = True
except Exception:       # pragma: no cover - depends on the host system
    _X = _XK = _xdisplay = _xprotocol = None
    _XLIB_AVAILABLE = False

# ============================================================
# INFO AND PATHS
# ============================================================

APP_NAME = "WINE Citra"
APP_VERSION = "0.2.21"

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
    "intercept_fullscreen_keys": True,
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
                "enable_fsync", "dxvk_hud", "virtual_desktop", "wine_debug",
                "intercept_fullscreen_keys"):
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
        process, _prefix, _cmd = start_wine(config, executable, arguments)
    except RuntimeError as e:
        log(f"Desktop launch failed: {e}")
        print(f"{APP_NAME}: {e}", file=sys.stderr)
        return 1
    config["last_executable"] = str(Path(executable).expanduser().resolve())
    save_config(config)

    # Keep this small process alive while the game runs, otherwise the
    # interceptor would exit together with the launcher and F11 would go
    # back to Wine's built-in fullscreen handling.
    if config.get("intercept_fullscreen_keys", True) and start_fullscreen_hotkeys(config):
        try:
            _wait_for_wine_windows(process)
        except KeyboardInterrupt:
            pass
        finally:
            stop_fullscreen_hotkeys()
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
# FULLSCREEN HOTKEYS (X11)
# ============================================================
#
# Wine programs normally receive F11 and Ctrl+Enter themselves, so they
# switch to their own fullscreen mode (which may still have decorations).
# Citra grabs those two keys on Wine windows instead:
#
#   F11         -> toggle EWMH borderless fullscreen on the Wine window
#   Ctrl+Enter  -> consume the event and do nothing
#
# The grabs are installed on the Wine client windows only, never on the
# root window. That means F11 and Ctrl+Enter keep working normally in every
# other application, even while a Wine program is running.

def _is_wine_process(pid):
    """Return True if *pid* looks like a Wine-hosted process."""
    try:
        executable = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        executable = ""

    if "wine" in os.path.basename(executable).lower():
        return True

    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            cmdline = handle.read().replace(b"\0", b" ").decode(
                "utf-8", errors="replace")
    except OSError:
        return False

    lowered = cmdline.lower()
    return ("wine" in lowered
            or "c:\\" in lowered or "z:\\" in lowered)


class FullscreenKeyInterceptor:
    """Grab F11 and Ctrl+Enter on Wine windows.

    F11 toggles EWMH fullscreen (borderless fullscreen) on the focused Wine
    window. Ctrl+Enter is swallowed so the Windows application never sees it.
    """

    POLL_INTERVAL = 0.5

    def __init__(self, config=None, poll_interval=POLL_INTERVAL):
        self.config = config if config is not None else {}
        self.poll_interval = poll_interval
        self._thread = None
        self._stop = threading.Event()
        self._grabbed = {}          # window id -> [(keycode, modifier mask)]
        self._active_windows = set()
        self._atoms = {}

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    def start(self):
        if not _XLIB_AVAILABLE:
            log("Fullscreen hotkeys disabled: python-xlib is not installed.")
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="wine-citra-fullscreen-keys", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        thread = self._thread
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=2.0)
        self._thread = None

    def _enabled(self):
        return bool(self.config.get("intercept_fullscreen_keys", True))

    def has_wine_windows(self):
        """Return True while at least one Wine window is visible."""
        return bool(self._active_windows)

    # --------------------------------------------------------
    # Worker
    # --------------------------------------------------------

    def _run(self):
        try:
            display = _xdisplay.Display()
        except Exception as exc:
            log(f"Fullscreen hotkeys unavailable (no X11 display): {exc}")
            return

        self._atoms.clear()
        root = display.screen().root
        f11 = display.keysym_to_keycode(_XK.string_to_keysym("F11"))
        ret = display.keysym_to_keycode(_XK.string_to_keysym("Return"))
        if not f11 or not ret:
            log("Fullscreen hotkeys disabled: F11/Return keycodes could not be resolved.")
            display.close()
            return

        key_specs = ((f11, 0), (ret, _X.ControlMask))
        modifiers = (0, _X.LockMask, _X.Mod2Mask, _X.LockMask | _X.Mod2Mask)

        try:
            while not self._stop.is_set():
                if self._enabled():
                    self._sync_grabs(display, root, key_specs, modifiers)
                else:
                    self._release_grabs(display)

                self._drain_events(display, root, f11, ret)
                readable, _, _ = select.select(
                    [display.fileno()], [], [], self.poll_interval)
                if readable:
                    self._drain_events(display, root, f11, ret)
        except Exception:
            log("Fullscreen hotkey thread stopped:\n" + traceback.format_exc())
        finally:
            try:
                self._release_grabs(display)
            except Exception:
                pass
            try:
                display.close()
            except Exception:
                pass
            self._thread = None

    def _drain_events(self, display, root, f11, ret):
        while display.pending_events():
            event = display.next_event()
            if event.type == _X.KeyPress:
                self._handle_key(display, root, event, f11, ret)

    # --------------------------------------------------------
    # Window discovery / grabs
    # --------------------------------------------------------

    def _atom(self, display, name):
        atom = self._atoms.get(name)
        if atom is None:
            atom = display.intern_atom(name)
            self._atoms[name] = atom
        return atom

    def _client_windows(self, display, root):
        try:
            prop = root.get_full_property(
                self._atom(display, "_NET_CLIENT_LIST"), _X.AnyPropertyType)
        except Exception:
            prop = None

        if prop and prop.value:
            for wid in prop.value:
                try:
                    yield display.create_resource_object("window", int(wid))
                except Exception:
                    continue
            return

        # Fallback for window managers without EWMH support.
        try:
            children = root.query_tree().children
        except Exception:
            return
        for child in children:
            try:
                subchildren = child.query_tree().children
            except Exception:
                subchildren = ()
            for window in list(subchildren) + [child]:
                yield window

    def _is_normal_window(self, display, window):
        try:
            prop = window.get_full_property(
                self._atom(display, "_NET_WM_WINDOW_TYPE"), _X.AnyPropertyType)
        except Exception:
            return True
        if not prop or not prop.value:
            return True
        normal = self._atom(display, "_NET_WM_WINDOW_TYPE_NORMAL")
        return normal in prop.value

    def _wine_windows(self, display, root):
        windows, seen = [], set()
        for window in self._client_windows(display, root):
            if window.id in seen:
                continue
            seen.add(window.id)
            try:
                prop = window.get_full_property(
                    self._atom(display, "_NET_WM_PID"), _X.AnyPropertyType)
            except Exception:
                continue
            if not prop or not prop.value:
                continue
            try:
                pid = int(prop.value[0])
            except (TypeError, ValueError):
                continue
            if not _is_wine_process(pid):
                continue
            if not self._is_normal_window(display, window):
                continue
            windows.append((window, pid))
        return windows

    def _sync_grabs(self, display, root, key_specs, modifiers):
        current = set()
        grabbed_any = False

        for window, pid in self._wine_windows(display, root):
            wid = window.id
            current.add(wid)
            if wid in self._grabbed:
                continue

            grabbed = []
            for keycode, base_mask in key_specs:
                for extra in modifiers:
                    mask = base_mask | extra
                    try:
                        window.grab_key(
                            keycode, mask, False,
                            _X.GrabModeAsync, _X.GrabModeAsync)
                        grabbed.append((keycode, mask))
                        grabbed_any = True
                    except Exception as exc:
                        log(f"Could not grab key {keycode} on window 0x{wid:x}: {exc}")

            self._grabbed[wid] = grabbed
            log(f"Intercepting F11 and Ctrl+Enter on Wine window 0x{wid:x} (pid {pid}).")

        self._active_windows = set(current)

        for wid in list(self._grabbed):
            if wid not in current:
                self._release_window(display, wid)

        if grabbed_any:
            display.flush()

    def _release_window(self, display, wid):
        keys = self._grabbed.pop(wid, [])
        if not keys:
            return
        try:
            window = display.create_resource_object("window", wid)
        except Exception:
            return
        for keycode, mask in keys:
            try:
                window.ungrab_key(keycode, mask)
            except Exception:
                pass

    def _release_grabs(self, display):
        for wid in list(self._grabbed):
            self._release_window(display, wid)
        try:
            display.flush()
        except Exception:
            pass

    # --------------------------------------------------------
    # Key handling
    # --------------------------------------------------------

    def _handle_key(self, display, root, event, f11, ret):
        # CapsLock (LockMask) and NumLock (Mod2Mask) must not change the
        # meaning of the shortcut.
        state = event.state & ~(_X.LockMask | _X.Mod2Mask)

        if event.detail == f11 and state == 0:
            self._toggle_fullscreen(display, root, event.window)
        elif event.detail == ret and state == _X.ControlMask:
            # Consume the event: Wine must never receive Ctrl+Enter.
            log("Blocked Ctrl+Enter on a Wine window.")

    def _toggle_fullscreen(self, display, root, window):
        state_atom = self._atom(display, "_NET_WM_STATE")
        fullscreen_atom = self._atom(display, "_NET_WM_STATE_FULLSCREEN")

        try:
            current = window.get_full_property(state_atom, _X.AnyPropertyType)
        except Exception:
            return

        fullscreen = bool(current and fullscreen_atom in current.value)
        action = 0 if fullscreen else 1

        message = _xprotocol.event.ClientMessage(
            window=window,
            client_type=state_atom,
            data=(32, [action, fullscreen_atom, 0, 1, 0]),
        )

        try:
            root.send_event(
                message,
                event_mask=(_X.SubstructureRedirectMask
                            | _X.SubstructureNotifyMask))
            display.flush()
        except Exception:
            log("Could not toggle borderless fullscreen:\n"
                + traceback.format_exc())
            return

        # The window manager resizes the top-level Wine window, but Wine may
        # keep its rendering child at the old work-area size. That leaves a
        # strip of desktop/taskbar visible along the bottom edge. Give the WM
        # a moment to settle, then make the main child fill the window again.
        self._fit_client_children(display, window)

        verb = "Disabled" if fullscreen else "Enabled"
        log(f"{verb} borderless fullscreen for Wine window 0x{window.id:x}.")

    def _fit_client_children(self, display, window, attempts=10, interval=0.12):
        """Make Wine's main rendering child fill its toplevel window.

        Games often create their rendering window at the size of the desktop
        work area, which excludes the panel/taskbar. When the toplevel window
        is made fullscreen the child can stay a little shorter, leaving an
        uncovered strip at the bottom. Only resize children that already
        occupy (almost) the whole window, so real overlay windows are left
        alone.
        """
        for _ in range(attempts):
            time.sleep(interval)
            try:
                geometry = window.get_geometry()
                children = window.query_tree().children
            except Exception:
                return
            resized = False
            for child in children:
                try:
                    child_geometry = child.get_geometry()
                    if (child_geometry.width < geometry.width * 0.9
                            or child_geometry.height < geometry.height * 0.9):
                        continue
                    if (child_geometry.width == geometry.width
                            and child_geometry.height == geometry.height):
                        continue
                    child.configure(x=0, y=0,
                                    width=geometry.width,
                                    height=geometry.height)
                    resized = True
                except Exception:
                    continue
            if resized:
                try:
                    display.flush()
                except Exception:
                    return


_fullscreen_hotkeys = None


def start_fullscreen_hotkeys(config=None):
    """Start the X11 F11 / Ctrl+Enter interceptor (idempotent)."""
    global _fullscreen_hotkeys
    if _fullscreen_hotkeys is None:
        _fullscreen_hotkeys = FullscreenKeyInterceptor(config)
    return _fullscreen_hotkeys.start()


def stop_fullscreen_hotkeys():
    """Stop the interceptor and release every key grab."""
    global _fullscreen_hotkeys
    if _fullscreen_hotkeys is not None:
        _fullscreen_hotkeys.stop()
        _fullscreen_hotkeys = None


def _wait_for_wine_windows(process=None, startup_timeout=120.0,
                           idle_timeout=5.0, exit_grace=30.0):
    """Keep a headless launcher alive until its Wine window disappears."""
    interceptor = _fullscreen_hotkeys
    if interceptor is None:
        return

    started = time.monotonic()
    seen_window = False
    idle_since = None

    while True:
        active = interceptor.has_wine_windows()
        if active:
            seen_window = True
            idle_since = None
        elif seen_window:
            if idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= idle_timeout:
                return
        else:
            elapsed = time.monotonic() - started
            if process is not None and process.poll() is not None:
                if elapsed >= exit_grace:
                    return
            elif elapsed >= startup_timeout:
                return
        time.sleep(0.5)


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

    # F11 toggles borderless fullscreen on Wine windows and Ctrl+Enter is
    # swallowed while the configuration option is enabled.
    start_fullscreen_hotkeys(window.config)
    app.aboutToQuit.connect(stop_fullscreen_hotkeys)

    return app.exec()


# Kept as a convenience alias for callers that prefer the old name.
main = run
