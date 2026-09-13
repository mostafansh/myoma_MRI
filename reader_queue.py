"""Project entry point for the bundled MyomaReviewLite queue utility."""

from __future__ import annotations

import sys
from pathlib import Path


def _module_root() -> Path:
    root = Path(__file__).resolve().with_name("MyomaReviewLite")
    if not (root / "MyomaReviewLiteLib" / "reader_queue.py").is_file():
        raise RuntimeError(f"bundled MyomaReviewLite queue utility was not found at {root}")
    return root


def main() -> int:
    try:
        root = _module_root()
        sys.path.insert(0, str(root))
        from MyomaReviewLiteLib.reader_queue import main as queue_main

        return queue_main(sys.argv[1:])
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
