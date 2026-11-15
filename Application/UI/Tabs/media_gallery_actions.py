"""Shared rename and delete actions for screenshot and clip galleries."""

from pathlib import Path

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from Core.Runtime.application_runtime import APP_NAME


def rename_media(parent, path, kind):
    path = Path(path)
    name, accepted = QInputDialog.getText(
        parent, f"Rename {kind}",
        f"Enter a new name; the {path.suffix} extension is kept:",
        text=path.stem)
    if not accepted:
        return None

    name = name.strip()
    if name.lower().endswith(path.suffix.lower()):
        name = name[:-len(path.suffix)].rstrip()
    if (not name or name in (".", "..") or "/" in name or "\\" in name):
        QMessageBox.warning(
            parent, f"{APP_NAME} - Rename {kind}",
            "Enter a valid file name without folder separators.")
        return None

    target = path.with_name(name + path.suffix)
    if target == path:
        return path
    if target.exists():
        QMessageBox.warning(
            parent, f"{APP_NAME} - Rename {kind}",
            f"A file named '{target.name}' already exists.")
        return None
    try:
        path.rename(target)
    except OSError as error:
        QMessageBox.warning(
            parent, f"{APP_NAME} - Rename {kind}",
            f"Could not rename '{path.name}':\n{error}")
        return None
    return target


def delete_media(parent, path, kind):
    return delete_media_items(parent, [path], kind)


def delete_media_items(parent, paths, kind):
    paths = [Path(path) for path in paths]
    if not paths:
        return []
    names = "\n".join(f"• {path.name}" for path in paths[:8])
    if len(paths) > 8:
        names += f"\n…and {len(paths) - 8} more"
    answer = QMessageBox.question(
        parent, f"Delete {kind}",
        f"Permanently delete {len(paths)} selected {kind.lower()}"
        f"{'s' if len(paths) != 1 else ''}?\n\n{names}",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No)
    if answer != QMessageBox.StandardButton.Yes:
        return []

    deleted = []
    failures = []
    for path in paths:
        try:
            path.unlink()
            deleted.append(path)
        except OSError as error:
            failures.append(f"{path.name}: {error}")
    if failures:
        QMessageBox.warning(
            parent, f"{APP_NAME} - Delete {kind}",
            "Some selected files could not be deleted:\n\n"
            + "\n".join(failures))
    return deleted
