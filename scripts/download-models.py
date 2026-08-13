#!/usr/bin/env python3
"""Download only reviewed, immutable official model artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from a64pilot.models.download import download_models, write_manifest
from a64pilot.models.registry import default_registry, required_registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--minimum",
        action="store_true",
        help="Download KleidiAI-compatible weak/strong Q4_0 and strong Q8_0 (default).",
    )
    selection.add_argument("--all", action="store_true", help="Download every registry row.")
    parser.add_argument("--dry-run", action="store_true", help="Plan without network or writes.")
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--manifest", type=Path, default=Path("artifacts/model-manifest.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    specs = default_registry() if args.all else required_registry()
    manifest = download_models(specs, output_dir=args.models_dir, dry_run=args.dry_run)
    if not args.dry_run:
        write_manifest(manifest, args.manifest)
    print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
