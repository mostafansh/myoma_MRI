"""Strict loader for a module's studio-module.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RESERVED_ENV = frozenset({"SLICER_STUDIO", "STUDIO_START_MODULE", "STUDIO_MODULE_PATHS", "STUDIO_TITLE", "STUDIO_USER"})
KNOWN_KEYS = {"schema", "name", "title", "module_paths", "start_module",
              "uses_dataset", "env", "workspace_requires"}
REQUIRED_KEYS = ("schema", "name", "title", "module_paths", "start_module")
_TOKEN_RE = re.compile(r"\{\{|\}\}|\{([^{}]*)\}|[{}]")


class ConfigError(Exception):
    """A manifest or deployment file is invalid. The message names file and key."""


@dataclass(frozen=True)
class ModuleManifest:
    path: Path
    name: str
    title: str
    module_dirs: tuple[Path, ...]
    start_module: str
    uses_dataset: bool
    env: dict[str, str]
    workspace_requires: tuple[str, ...]


def _fail(path: Path, key: str, problem: str) -> ConfigError:
    return ConfigError(f"{path}: key '{key}': {problem}")


def _expand(text: str, uses_dataset: bool, path: Path, key: str) -> str:
    def repl(match: re.Match) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        field = match.group(1)
        if field == "workspace":
            return "/workspace"
        if field == "dataset":
            if not uses_dataset:
                raise _fail(path, key, "{dataset} is used but uses_dataset is false")
            return "/data"
        if field is None:
            raise _fail(path, key, f"unmatched brace in {text!r}")
        raise _fail(path, key, f"unknown placeholder {{{field}}}; only {{workspace}} and {{dataset}} are allowed")

    return _TOKEN_RE.sub(repl, text)


def expand_env(manifest: ModuleManifest) -> dict[str, str]:
    return {k: _expand(v, manifest.uses_dataset, manifest.path, f"env.{k}")
            for k, v in manifest.env.items()}


def _relative_name_ok(value: str) -> bool:
    if not value or value != value.strip() or "\\" in value or "\0" in value:
        return False
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or PureWindowsPath(value).is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def load_manifest(path: Path) -> ModuleManifest:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: manifest file not found") from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(f"{path}: cannot read manifest: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    for key in data:
        if key not in KNOWN_KEYS:
            raise _fail(path, key, "unknown key")
    for key in REQUIRED_KEYS:
        if key not in data:
            raise _fail(path, key, "required key is missing")

    if type(data["schema"]) is not int or data["schema"] != 1:
        raise _fail(path, "schema", "must be the integer 1")
    name = data["name"]
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise _fail(path, "name", "must match ^[a-z][a-z0-9-]{0,31}$")
    for key in ("title", "start_module"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise _fail(path, key, "must be a non-empty string")

    uses_dataset = data.get("uses_dataset", False)
    if not isinstance(uses_dataset, bool):
        raise _fail(path, "uses_dataset", "must be true or false")

    env = data.get("env", {})
    if not isinstance(env, dict):
        raise _fail(path, "env", "must be an object of strings")
    for k, v in env.items():
        if not isinstance(k, str) or not ENV_KEY_RE.fullmatch(k):
            raise _fail(path, f"env.{k}", "variable name must match [A-Za-z_][A-Za-z0-9_]*")
        if k in RESERVED_ENV:
            raise _fail(path, f"env.{k}", "reserved name, set by the studio")
        if not isinstance(v, str):
            raise _fail(path, f"env.{k}", "value must be a string")

    requires = data.get("workspace_requires", [])
    if not isinstance(requires, list):
        raise _fail(path, "workspace_requires", "must be a list of file names")
    for item in requires:
        if not isinstance(item, str) or not _relative_name_ok(item):
            raise _fail(path, "workspace_requires", f"{item!r} must be a relative file name inside the workspace")

    paths = data["module_paths"]
    if not isinstance(paths, list) or not paths:
        raise _fail(path, "module_paths", "must be a non-empty list of folders")
    base = path.resolve().parent
    dirs = []
    for item in paths:
        if not isinstance(item, str) or not item.strip():
            raise _fail(path, "module_paths", f"{item!r} must be a non-empty string")
        resolved = (base / item).resolve()
        if not resolved.is_relative_to(base):
            raise _fail(path, "module_paths", f"{item!r} resolves outside the manifest's folder")
        if resolved == base:
            raise _fail(path, "module_paths", f"{item!r} is the manifest's own folder; list a subfolder")
        if not resolved.is_dir():
            raise _fail(path, "module_paths", f"{item!r} is not an existing folder")
        dirs.append(resolved)

    manifest = ModuleManifest(
        path=path, name=name, title=data["title"], module_dirs=tuple(dirs),
        start_module=data["start_module"], uses_dataset=uses_dataset,
        env=dict(env), workspace_requires=tuple(requires),
    )
    expand_env(manifest)  # fail at load time on bad placeholders
    return manifest
