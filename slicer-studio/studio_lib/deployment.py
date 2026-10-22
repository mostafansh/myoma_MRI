"""Strict loader for a deployment folder's studio.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .manifest import ConfigError, ModuleManifest, load_manifest

KNOWN_KEYS = {"schema", "module", "dataset", "server_address"}
_ADDRESS_RE = re.compile(r"[A-Za-z0-9.:\-\[\]]+")


def check_address(text: str) -> None:
    # fullmatch, not match+$: "$" would accept a trailing newline.
    if not isinstance(text, str) or not _ADDRESS_RE.fullmatch(text):
        raise ConfigError(f"key 'server_address': invalid server address {text!r}")


@dataclass(frozen=True)
class Deployment:
    root: Path
    manifest: ModuleManifest
    dataset: Path | None
    server_address: str

    @property
    def users_dir(self) -> Path:
        return self.root / "users"

    @property
    def portal_dir(self) -> Path:
        return self.root / "portal"

    @property
    def generated_dir(self) -> Path:
        return self.root / "generated"

    @property
    def config_path(self) -> Path:
        return self.root / "studio.json"


def _fail(path: Path, key: str, problem: str) -> ConfigError:
    return ConfigError(f"{path}: key '{key}': {problem}")


def load_deployment(root: Path) -> Deployment:
    root = Path(root)
    path = root / "studio.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    for key in data:
        if key not in KNOWN_KEYS:
            raise _fail(path, key, "unknown key")
    for key in ("schema", "module", "server_address"):
        if key not in data:
            raise _fail(path, key, "required key is missing")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise _fail(path, "schema", "must be the integer 1")

    def resolve(key: str) -> Path:
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise _fail(path, key, "must be a non-empty path string")
        p = Path(value)
        return p if p.is_absolute() else root / p

    manifest = load_manifest(resolve("module"))

    dataset = None
    if manifest.uses_dataset:
        if "dataset" not in data:
            raise _fail(path, "dataset", "required because the module uses a dataset")
        dataset = resolve("dataset").resolve()
        if not dataset.is_dir():
            raise _fail(path, "dataset", f"{str(dataset)!r} is not an existing folder")
    elif "dataset" in data:
        raise _fail(path, "dataset", "not allowed because the module does not use a dataset")

    address = data["server_address"]
    try:
        check_address(address)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return Deployment(root=root, manifest=manifest, dataset=dataset, server_address=address)


def project_name(root: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", Path(root).name.lower()).strip("-")
    if not slug:
        return "studio"
    return ("studio-" + slug)[:63].rstrip("-")


def missing_workspace_files(deployment: Deployment, user_id: str) -> list[str]:
    workspace = deployment.users_dir / user_id
    return [n for n in deployment.manifest.workspace_requires if not (workspace / n).is_file()]
