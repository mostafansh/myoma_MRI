# Hello Studio: a template for a new studio module

Copy this folder, rename `HelloStudio`, and edit `studio-module.json`:

- `schema` is 1. `name` is lowercase letters, digits and `-`; `title` is shown in the portal.
- `module_paths` lists scripted-module folders, relative to this folder, that Slicer loads.
- `start_module` is the module Slicer opens and locks the user into.
- `uses_dataset` is true if the module reads a shared dataset, mounted at `/data`.
- `env` sets environment variables; `{workspace}` becomes `/workspace`, `{dataset}` becomes `/data`.
- `workspace_requires` (optional) lists files the user's workspace must contain.

Each user gets a private folder mounted at `/workspace`. Keep all of the user's
state there and write files atomically (temp file, then `os.replace`).

To show progress in the portal, write `/workspace/progress.json` with exactly four
non-negative integers: `{"total": 10, "done": 3, "in_progress": 0, "needs_attention": 0}`.

The studio sets `SLICER_STUDIO=1` inside the container. A module can test for it to
turn on studio-only behaviour (locked-down UI, fixed paths) and stay a normal
module when run in plain Slicer.

Run it by pointing a deployment's `studio.json` module setting
at this folder (see the studio CLI).
