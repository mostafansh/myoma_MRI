"""Every named-volume target under /home/user must exist in the image, owned by the image user.

A new named volume mounted over a path that does not exist is created root-owned,
so the unprivileged Slicer user could not write to it.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_lib.render import render_compose  # noqa: E402
from test_render import _write_fixture  # noqa: E402

DOCKERFILE = Path(__file__).resolve().parents[1] / "slicer" / "Dockerfile"


def _created_dirs(text: str) -> set[str]:
    """Paths made by `mkdir -p` and given to the image user by `chown ... user` in one RUN."""
    created: set[str] = set()
    for run in re.findall(r"(?m)^RUN (.*(?:\\r?\n.*)*)", text):
        run = re.sub(r"\\r?\n", " ", run)
        made = {p for cmd in re.findall(r"mkdir\s+(?:-\S+\s+)*([^&;|]+)", run) for p in cmd.split()}
        owned = {p for cmd in re.findall(r"chown\s+(?:-\S+\s+)*user(?::\S+)?\s+([^&;|]+)", run)
                 for p in cmd.split()}
        created |= made & owned
    return created


class ImagePathTests(unittest.TestCase):
    def test_every_home_volume_target_is_created_and_owned_in_the_dockerfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dep, _ = _write_fixture(Path(tmp))
            compose = render_compose(dep, ["ali"])
        targets = set(re.findall(r':(/home/user(?:/[^:"\s]+)*)(?::\w+)?"?\s*$', compose, re.M))
        self.assertIn("/home/user/.config", targets)
        created = _created_dirs(DOCKERFILE.read_text(encoding="utf-8"))
        for target in sorted(targets):
            with self.subTest(target=target):
                self.assertIn(target, created, f"{target} is not created and chowned to user in the Dockerfile")

    def test_created_dirs_parser(self) -> None:
        self.assertEqual(_created_dirs("RUN mkdir -p /home/user/.config && chown user:user /home/user/.config\n"),
                         {"/home/user/.config"})
        self.assertEqual(_created_dirs("RUN mkdir -p /home/user/.config\n"), set())
        self.assertEqual(_created_dirs("RUN chown user /home/user/.config\n"), set())


if __name__ == "__main__":
    unittest.main()
