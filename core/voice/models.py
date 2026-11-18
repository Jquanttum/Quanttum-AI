"""Finding Vosk speech models on disk."""

from pathlib import Path


def looks_like_vosk_model(path):
    path = Path(path)
    return path.is_dir() and (path / "am").is_dir() and (path / "conf").is_dir()


def find_models(folders, depth=2):
    """Every Vosk model inside the folders (they may be nested one level, as
    unzipping often creates)."""

    found, seen = [], set()

    def visit(folder, remaining):
        try:
            resolved = str(folder.resolve())
        except OSError:
            return

        if resolved in seen:
            return

        seen.add(resolved)

        if looks_like_vosk_model(folder):
            found.append(folder)
            return

        if remaining <= 0:
            return

        try:
            children = sorted(c for c in folder.iterdir() if c.is_dir())
        except OSError:
            return

        for child in children:
            visit(child, remaining - 1)

    for folder in folders:
        folder = Path(folder)

        if folder.is_dir():
            visit(folder, depth)

    return found
