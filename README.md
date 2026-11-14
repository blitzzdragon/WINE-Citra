# WINE Citra

WINE Citra is a desktop launcher and prefix manager for running Windows
programs with Wine on Linux. It provides a simple executable picker, per-app
logs, graphics and audio controls, and tools for managing Wine prefixes.

## Requirements

- Linux with a graphical desktop session
- Python 3 and PyQt6
- Wine
- python-xlib for fullscreen controls and cursor trapping

Winetricks, GameMode, PulseAudio/PipeWire utilities, and Vulkan drivers are
optional and are used when available.

## Run from source

```sh
cd Application
python3 main.py
```

In the **Launcher** tab, choose or drop a Windows `.exe` and select **Launch**.
Less common arguments and prefix overrides are grouped under **Advanced launch
options**. The Settings tab includes Wine, graphics, audio, display, launcher,
and accessibility preferences. Running programs can be stopped or their logs
opened from the launcher.

The launcher supports keyboard use: **Ctrl+O** opens the executable picker,
**Ctrl+Enter** launches the selected program, and **F5** refreshes the app's
lists. The interface text size can be increased in Settings.

The Updates tab lists stable releases (and optional beta versions) in a
version selector, with release notes and package links shown on a card.
WINE Citra uses `Application/Assets/windows_icon.svg` for its app and desktop
icons; the packager installs it into the system icon theme.

## Data locations

- Settings: `~/.config/wine-citra/settings.json`
- Prefixes and logs: `~/.local/share/wine-citra/`

WINE Citra does not bundle Wine or Windows programs.

## Support

https://ko-fi.com/blitzzdragon
