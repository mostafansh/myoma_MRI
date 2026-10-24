# MyomaReviewLite portable review package

This folder is self-contained except for the image dataset and 3D Slicer 5.10.
Mostafa's queue and review results are stored under `mostafa`.

The pilot controls and the workflow for all requested annotation and
segmentation features are documented in [PILOT_FEATURE_GUIDE.md](PILOT_FEATURE_GUIDE.md).

To run the review as a multi-reader browser studio on a local network, see
[slicer-studio/README.md](slicer-studio/README.md). This package is a studio
module: its manifest is [studio-module.json](studio-module.json) at the root of
this folder. Prepare each reader's queue with
`python reader_queue.py create --reader-id <id> --study-id <study> --reader-dir <deploy-dir>/users/<id>`
(add `--case-list <file>` for a subset), then `add-user` and `up` as the studio guide describes.

## Set up or move the package

1. Copy the entire `fibroma_pilot` folder. Do not copy only the `mostafa`
   subfolder.
2. Open `review-config.json` in a text editor.
3. Set `dataset_root` to the folder containing the `sub-*` subject folders.
   The value may be an absolute path or a path relative to this package folder.
4. Leave `slicer_path` empty to auto-detect 3D Slicer 5.10, or set it to the
   Slicer executable or shortcut. It may also be absolute or package-relative.
5. Save the configuration, then double-click `mostafa\Start Review.cmd`.

Backslashes inside JSON strings must be doubled. Do not edit `mostafa\queue.json`
to change computers; its source paths are deliberately dataset-relative.

## Configuration and command-line precedence

`review-config.json` is the only package file that contains computer-specific
paths. `dataset_root` is required. A blank `slicer_path` enables 5.10
auto-detection. Passing `-SlicerPath` directly to `Start-MyomaReviewLite.ps1`
overrides both the configured path and auto-detection.

The queue utility uses the same dataset configuration:

```text
python reader_queue.py create --reader-id READER --study-id STUDY --reader-dir READER_FOLDER [--case-list LIST] [--config CONFIG] [--data-root DATASET]
python reader_queue.py status --reader-dir READER_FOLDER [--config CONFIG] [--data-root DATASET] [--json]
python reader_queue.py migrate-paths --reader-dir READER_FOLDER [--config CONFIG] [--data-root DATASET]
```

An explicit `--data-root` overrides the configured dataset root.

## Recovery

- If startup reports a missing dataset, correct `dataset_root`; do not move or
  rename subject files individually. Each case must retain its four expected
  NIfTI filenames and matching geometry, and masks may contain only labels 0–4.
- If Slicer cannot be found, set `slicer_path` to the Slicer 5.10 executable or
  its shortcut. A command-line `-SlicerPath` can be used for a one-time launch.
- If configuration is malformed, restore the three keys shown in the supplied
  `review-config.json`, keep `schema_version` as `1.0`, and make both path values
  JSON strings.
- Review artifacts are independent of source paths. Keep the entire `mostafa`
  folder when recovering or moving the package, and never place reader output
  inside the source dataset.

AI masks must contain only labels 0–4, and the correction editor uses the same
fixed labels 0–4.
