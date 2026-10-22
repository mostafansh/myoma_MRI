#!/usr/bin/env python3
"""The studio admin CLI: create a deployment, manage its users, start and stop it."""

from __future__ import annotations

import argparse
import getpass as getpass_module
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from studio_lib.deployment import (  # noqa: E402
    Deployment, load_deployment, missing_workspace_files)
from studio_lib.manifest import ConfigError  # noqa: E402
from studio_lib.render import render  # noqa: E402

import portal_core  # noqa: E402  (studio_lib puts slicer-studio/portal on sys.path)
from portal_core import PortalError, User  # noqa: E402

MIN_PASSWORD = 8
EXIT_OK, EXIT_DOCKER, EXIT_USER = 0, 1, 2


class CliError(Exception):
    """A user or config error: printed as 'error: ...', exit code 2."""


def _repo_root() -> Path | None:
    for folder in (HERE, *HERE.parents):
        if (folder / ".git").exists():  # a folder normally, a file in a git worktree
            return folder
    return None


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _write_secret(path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(secrets.token_bytes(32))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load(deploy: str) -> Deployment:
    try:
        return load_deployment(Path(deploy))
    except ConfigError as exc:
        raise CliError(str(exc)) from None


def _users_path(dep: Deployment) -> Path:
    return dep.portal_dir / "users.json"


def _load_users(dep: Deployment) -> dict[str, User]:
    try:
        return portal_core.load_users(_users_path(dep))
    except PortalError as exc:
        raise CliError(f"{exc} ({_users_path(dep)})") from None


# --- commands --------------------------------------------------------------

def cmd_init(args, ctx) -> int:
    root = Path(args.deploy).resolve()
    repo = _repo_root()
    if repo is not None and _inside(root, repo.resolve()):
        raise CliError(f"{root} is inside the repository {repo}; choose a folder outside it")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise CliError(f"{root} exists and is not empty")
    config = {"schema": 1, "module": args.module, "server_address": args.server_address}
    if args.dataset is not None:
        config["dataset"] = args.dataset
    created = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    try:
        (root / "studio.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        load_deployment(root)  # a typo fails here, not later
        for sub in ("users", "portal", "generated"):
            (root / sub).mkdir()
        _write_secret(root / "portal" / "secret.key")
        portal_core.save_users(root / "portal" / "users.json", {})
    except ConfigError as exc:
        _cleanup(root, created)
        raise CliError(str(exc)) from None
    except Exception:
        _cleanup(root, created)
        raise
    print(f"Created deployment {root}")
    print(f'Next: studio.py add-user "{args.deploy}" <id>, then studio.py up "{args.deploy}"')
    return EXIT_OK


def _cleanup(root: Path, created: bool) -> None:
    if created:
        shutil.rmtree(root, ignore_errors=True)
        return
    for name in ("studio.json", "users", "portal", "generated"):
        target = root / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()


def cmd_add_user(args, ctx) -> int:
    uid = args.id
    if not portal_core.USER_ID_RE.fullmatch(uid):
        raise CliError(f"invalid user id '{uid}' (lowercase letters, digits, - and _, starting with a letter)")
    if uid in portal_core.RESERVED_IDS:
        raise CliError(f"user id '{uid}' is reserved")
    dep = _load(args.deploy)
    users = _load_users(dep)
    existing = users.get(uid)
    admin = True if args.admin else (existing.admin if existing else False)
    if not admin:
        (dep.users_dir / uid).mkdir(parents=True, exist_ok=True)
        missing = missing_workspace_files(dep, uid)
        if missing:
            raise CliError(f"workspace {dep.users_dir / uid} is missing: {', '.join(missing)}")
    password = ctx.getpass("Password: ")
    if not password or password != ctx.getpass("Repeat password: "):
        raise CliError("passwords are empty or do not match")
    if len(password) < MIN_PASSWORD:
        raise CliError(f"password must be at least {MIN_PASSWORD} characters")
    name = args.name or (existing.display_name if existing else uid)
    users[uid] = User(uid, name, portal_core.hash_password(password), admin)
    portal_core.save_users(_users_path(dep), users)
    print(f"Saved user {uid}")
    return EXIT_OK


def cmd_remove_user(args, ctx) -> int:
    dep = _load(args.deploy)
    users = _load_users(dep)
    if args.id not in users:
        raise CliError(f"no such user '{args.id}'")
    del users[args.id]
    portal_core.save_users(_users_path(dep), users)
    print(f"Removed user {args.id}; their workspace in {dep.users_dir / args.id} was kept.")
    print(f'Run "studio.py up {args.deploy}" to stop their Slicer.')
    return EXIT_OK


def _render(dep: Deployment):
    try:
        return render(dep)
    except (ConfigError, PortalError) as exc:
        raise CliError(str(exc)) from None


def cmd_render(args, ctx) -> int:
    dep = _load(args.deploy)
    result = _render(dep)
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"Wrote {dep.generated_dir / 'docker-compose.yml'}")
    print(f"Wrote {dep.generated_dir / 'Caddyfile'}")
    return EXIT_OK


def _compose(dep: Deployment, ctx, *tail: str) -> int:
    argv = ["docker", "compose", "-f", str(dep.generated_dir / "docker-compose.yml"), *tail]
    try:
        done = ctx.run(argv)
    except OSError as exc:
        print(f"error: cannot run {' '.join(argv)}: {exc}", file=sys.stderr)
        return EXIT_DOCKER
    if done.returncode != 0:
        print(f"error: {' '.join(argv)} failed with exit code {done.returncode}", file=sys.stderr)
        return EXIT_DOCKER
    return EXIT_OK


def cmd_up(args, ctx) -> int:
    dep = _load(args.deploy)
    result = _render(dep)
    for warning in result.warnings:
        print(f"warning: {warning}")
    return _compose(dep, ctx, "up", "-d", "--remove-orphans")


def cmd_down(args, ctx) -> int:
    return _compose(_load(args.deploy), ctx, "down")  # never -v: workspaces must survive


def cmd_restart(args, ctx) -> int:
    return _compose(_load(args.deploy), ctx, "restart")


def cmd_status(args, ctx) -> int:
    dep = _load(args.deploy)
    users = _load_users(dep)
    code = _compose(dep, ctx, "ps")
    for uid in sorted(u.id for u in users.values() if not u.admin):
        progress = portal_core.read_progress(dep.users_dir / uid / portal_core.PROGRESS_FILE)
        if progress is None:
            print(f"{uid}  —")
        else:
            print(f"{uid}  {progress['done']}/{progress['total']} done, "
                  f"{progress['in_progress']} in progress, {progress['needs_attention']} need attention")
    return code


class _Context:
    def __init__(self, getpass, run) -> None:
        self.getpass = getpass
        self.run = run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio.py", description="Slicer Studio admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a new deployment folder")
    init.add_argument("deploy")
    init.add_argument("--module", required=True, help="path to the module's studio-module.json")
    init.add_argument("--server-address", required=True)
    init.add_argument("--dataset")
    init.set_defaults(func=cmd_init)
    add = sub.add_parser("add-user", help="add a user or reset their password")
    add.add_argument("deploy")
    add.add_argument("id")
    add.add_argument("--name")
    add.add_argument("--admin", action="store_true")
    add.set_defaults(func=cmd_add_user)
    rem = sub.add_parser("remove-user", help="remove a user, keeping their workspace")
    rem.add_argument("deploy")
    rem.add_argument("id")
    rem.set_defaults(func=cmd_remove_user)
    for name, func, text in (("render", cmd_render, "write the compose file and Caddyfile"),
                             ("up", cmd_up, "render, then start everything"),
                             ("down", cmd_down, "stop everything (keeps all data)"),
                             ("restart", cmd_restart, "restart the containers"),
                             ("status", cmd_status, "containers and per-user progress")):
        p = sub.add_parser(name, help=text)
        p.add_argument("deploy")
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None, *, getpass=getpass_module.getpass, run=subprocess.run) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code in (0, None) else EXIT_USER
    try:
        return args.func(args, _Context(getpass, run))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # the status dash on a cp1252 console
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
