from __future__ import annotations

import os
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DATA_DIR_ENV_NAMES = ("PERSONAL_BRAND_OS_DATA_DIR", "DATA_DIR")
_BOOTSTRAPPED_ROOTS: set[Path] = set()


def data_root() -> Path:
    configured = _configured_data_dir()
    root = configured or DEFAULT_DATA_ROOT
    if configured:
        _bootstrap_external_data_root(root)
    return root


def data_path(*parts: str) -> Path:
    return data_root().joinpath(*parts)


def resolve_data_reference(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        return data_root().joinpath(*path.parts[1:])
    return PROJECT_ROOT / path


def _configured_data_dir() -> Path | None:
    for name in DATA_DIR_ENV_NAMES:
        raw = os.environ.get(name, "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def _bootstrap_external_data_root(root: Path) -> None:
    resolved = root.resolve()
    if resolved in _BOOTSTRAPPED_ROOTS:
        return
    root.mkdir(parents=True, exist_ok=True)
    if DEFAULT_DATA_ROOT.exists():
        shutil.copytree(DEFAULT_DATA_ROOT, root, dirs_exist_ok=True)
    _BOOTSTRAPPED_ROOTS.add(resolved)
