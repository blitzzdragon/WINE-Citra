#!/usr/bin/env python3
"""Build a Debian package for WINE Citra.

The application is installed as a private Python tree in
/usr/lib/wine-citra, with a small launcher in /usr/bin. This keeps the
project's Core/ and UI/ packages out of the system Python namespace while
still letting them be imported normally.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

PACKAGER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGER_DIR.parent
DEFAULT_CONFIG = PACKAGER_DIR / "Configuration" / "winecitra_config.json"

DEFAULT_DEPENDS = "python3, python3-pyqt6, wine"
DEFAULT_RECOMMENDS = "winetricks, gamemode, mangohud"
DEFAULT_INCLUDE = ["main.py", "Core", "UI", "SettingsMemory"]

COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".git", ".gitignore",
    ".DS_Store", "*~", "*.deb",
)


def fail(message):
    print(f"\n[ERROR] {message}")
    sys.exit(1)


def load_config(config_file):
    if not config_file.is_file():
        fail(f"Configuration file is missing: {config_file}")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as error:
        fail(f"Could not read {config_file}: {error}")

    if not isinstance(cfg, dict):
        fail("Configuration must be a JSON object.")

    required = [
        "package_name",
        "version",
        "section",
        "priority",
        "architecture",
        "maintainer",
        "description",
    ]

    for key in required:
        if not cfg.get(key):
            fail(f"Missing configuration value: {key}")

    package = cfg["package_name"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
        fail("Invalid Debian package name.")

    if not re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]*", cfg["version"]):
        fail("Invalid package version.")

    if not re.fullmatch(r"(all|[a-z0-9][a-z0-9-]*)", cfg["architecture"]):
        fail("Invalid package architecture.")

    return cfg


def resolve_source_dir(cfg):
    raw = cfg.get("source_dir") or cfg.get("script_source") or ".."
    source = (PACKAGER_DIR / raw).resolve()

    # Backwards compatibility: script_source may point at a file.
    if source.is_file():
        source = source.parent

    entry_point = cfg.get("entry_point", "main.py")
    if not (source / entry_point).is_file():
        fail(f"Application entry point not found: {source / entry_point}")

    return source, entry_point


def resolve_output_dir(cfg, override):
    raw = override or cfg.get("output_dir") or "../dist"
    output = Path(raw).expanduser()
    if not output.is_absolute():
        output = (PACKAGER_DIR / output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    return output


def supports_root_owner_group():
    try:
        result = subprocess.run(
            ["dpkg-deb", "--help"],
            capture_output=True, text=True, check=False)
    except OSError:
        return False
    return "--root-owner-group" in result.stdout


def copy_application(source_dir, app_root, include, entry_point):
    app_root.mkdir(parents=True, exist_ok=True)

    for item in include:
        source = source_dir / item
        target = app_root / item

        if source.is_dir():
            shutil.copytree(source, target, ignore=COPY_IGNORE)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            fail(f"Included path not found: {source}")

    # Normalise permissions: readable data files, executable entry point.
    for root, _dirs, files in os.walk(app_root):
        os.chmod(root, 0o755)
        for filename in files:
            os.chmod(Path(root) / filename, 0o644)

    entry = app_root / entry_point
    if entry.exists():
        entry.chmod(0o755)


def write_launcher(path, install_dir):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env python3\n"
        '"""Launcher for WINE Citra."""\n'
        "\n"
        "import sys\n"
        "\n"
        f"INSTALL_DIR = {str(install_dir)!r}\n"
        "if INSTALL_DIR not in sys.path:\n"
        "    sys.path.insert(0, INSTALL_DIR)\n"
        "\n"
        "from Core.Runtime.application_runtime import run\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(run())\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_desktop_file(path, package):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=WINE Citra\n"
        "GenericName=Windows Application Manager\n"
        "Comment=Run Windows applications using isolated Wine prefixes\n"
        f"Exec=/usr/bin/{package} %f\n"
        f"TryExec=/usr/bin/{package}\n"
        "Icon=wine\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Keywords=wine;windows;prefix;launcher;\n"
        "MimeType=application/x-ms-dos-executable;application/x-msdownload;"
        "application/vnd.microsoft.portable-executable;application/x-msi;\n"
        "StartupNotify=true\n",
        encoding="utf-8",
    )
    path.chmod(0o644)


def write_maintainer_scripts(debian_dir):
    postinst = (
        "#!/bin/sh\n"
        "set -e\n"
        "\n"
        "if command -v update-desktop-database >/dev/null 2>&1; then\n"
        "    update-desktop-database -q /usr/share/applications || true\n"
        "fi\n"
        "\n"
        "exit 0\n"
    )

    for name in ("postinst", "postrm"):
        script = debian_dir / name
        script.write_text(postinst, encoding="utf-8")
        script.chmod(0o755)


def normalise_permissions(build_dir):
    """Keep the package tree world-readable and the root entry at 0755.

    tempfile.mkdtemp() creates the build directory as 0700. If that mode is
    packaged for "./", installing the .deb would change the permissions of
    the filesystem root, so normalise every directory and file explicitly.
    """
    os.chmod(build_dir, 0o755)
    for root, _dirs, files in os.walk(build_dir):
        os.chmod(root, 0o755)
        for filename in files:
            path = Path(root) / filename
            if path.stat().st_mode & 0o111:
                path.chmod(0o755)
            else:
                path.chmod(0o644)


def control_description(description):
    lines = str(description).strip().splitlines() or [""]
    result = lines[0]
    for line in lines[1:]:
        result += "\n " + (line if line else ".")
    return result


def build_package(config_file, output_override=None):
    cfg = load_config(config_file)
    package = cfg["package_name"]
    version = cfg["version"]
    architecture = cfg["architecture"]

    source_dir, entry_point = resolve_source_dir(cfg)
    include = cfg.get("include") or DEFAULT_INCLUDE
    install_dir = Path(cfg.get("install_dir", f"/usr/lib/{package}"))

    output_dir = resolve_output_dir(cfg, output_override)
    deb_file = output_dir / f"{package}_{version}_{architecture}.deb"

    if not shutil.which("dpkg-deb"):
        fail("dpkg-deb is not installed (install the dpkg package).")

    print("=" * 55)
    print("             WINE CITRA PACKAGE BUILDER")
    print("=" * 55)
    print(f"\nPackage:    {package}")
    print(f"Version:    {version}")
    print(f"Source:     {source_dir}")
    print(f"Install to: {install_dir}")
    print(f"Output:     {deb_file}")

    build_dir = Path(tempfile.mkdtemp(prefix=f"{package}-build-"))
    try:
        debian_dir = build_dir / "DEBIAN"
        debian_dir.mkdir(parents=True)

        app_root = build_dir / install_dir.relative_to("/")
        copy_application(source_dir, app_root, include, entry_point)

        write_launcher(build_dir / "usr/bin" / package, install_dir)

        if cfg.get("desktop_entry", True):
            write_desktop_file(
                build_dir / "usr/share/applications" / f"{package}.desktop",
                package)

        write_maintainer_scripts(debian_dir)
        normalise_permissions(build_dir)

        total_bytes = sum(
            f.stat().st_size for f in build_dir.rglob("*") if f.is_file()
        )
        installed_kb = max(1, (total_bytes + 1023) // 1024)

        depends = cfg.get("depends") or DEFAULT_DEPENDS
        recommends = cfg.get("recommends") or DEFAULT_RECOMMENDS

        control = (
            f"Package: {package}\n"
            f"Version: {version}\n"
            f"Section: {cfg['section']}\n"
            f"Priority: {cfg['priority']}\n"
            f"Architecture: {architecture}\n"
            f"Installed-Size: {installed_kb}\n"
            f"Depends: {depends}\n"
        )
        if recommends:
            control += f"Recommends: {recommends}\n"
        control += (
            f"Maintainer: {cfg['maintainer']}\n"
            f"Description: {control_description(cfg['description'])}\n"
        )

        (debian_dir / "control").write_text(control, encoding="utf-8")
        (debian_dir / "control").chmod(0o644)

        if deb_file.exists():
            deb_file.unlink()

        print("\nBuilding Debian package...\n")
        sys.stdout.flush()

        command = ["dpkg-deb", "--build"]
        if supports_root_owner_group():
            command.append("--root-owner-group")
        command += [str(build_dir), str(deb_file)]

        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError:
            fail("dpkg-deb failed to build the package.")
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    if not deb_file.is_file():
        fail("Package was not created.")

    print("\n" + "=" * 55)
    print("             BUILD SUCCESSFUL")
    print("=" * 55)
    print(f"\nPackage created:\n{deb_file}")
    print("\nInstall using:")
    print(f"sudo apt install '{deb_file}'")
    print("\nRemove using:")
    print(f"sudo apt remove {package}")
    print("\nPackage information:\n")
    sys.stdout.flush()

    subprocess.run(["dpkg-deb", "--info", str(deb_file)], check=False)

    print("\nWINE Citra packaging complete!")


def main():
    parser = argparse.ArgumentParser(description="Build the WINE Citra .deb package.")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help=f"Path to the packaging configuration (default: {DEFAULT_CONFIG})")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Directory for the generated .deb (overrides the config)")
    args = parser.parse_args()

    config_file = args.config.expanduser().resolve()
    build_package(config_file, args.output)


if __name__ == "__main__":
    main()
