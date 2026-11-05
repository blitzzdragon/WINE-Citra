"""Best-effort checks for external tools used by WINE Citra.

Only the standard library is used here, so this module can be imported
before Qt is initialized (and in headless/desktop-entry mode).
"""

import shutil

REQUIRED_TOOLS = {
    "wine": "Wine (or wine64)",
}

OPTIONAL_TOOLS = {
    "wineboot": "Wineboot",
    "winetricks": "Winetricks",
    "gamemoderun": "GameMode",
    "mangohud": "MangoHud",
    "lspci": "PCI utilities (GPU names)",
}


def find_tool(name):
    """Return the path to *name*, treating wine64 as a Wine fallback."""
    if name == "wine":
        return shutil.which("wine") or shutil.which("wine64")
    return shutil.which(name)


def check_dependencies():
    """Return a report describing which external tools are unavailable."""
    return {
        "required_missing": [
            label for tool, label in REQUIRED_TOOLS.items()
            if not find_tool(tool)
        ],
        "optional_missing": [
            label for tool, label in OPTIONAL_TOOLS.items()
            if not find_tool(tool)
        ],
    }


def describe_dependencies(report=None):
    """Format a dependency report as a short, log-friendly string."""
    report = check_dependencies() if report is None else report
    lines = []

    if report["required_missing"]:
        lines.append("Missing required tools: " + ", ".join(report["required_missing"]))
    if report["optional_missing"]:
        lines.append("Optional tools not found: " + ", ".join(report["optional_missing"]))

    return "\n".join(lines)
