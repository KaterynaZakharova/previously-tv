"""One-time cleanup after extracting an update over an older project.

Use --apply to remove the retired top-level input directory and old provider
schema fixtures. Active media under assets/, the catalog and runtime are kept.
"""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def retired_paths(root):
    root = Path(root)
    legacy = root / 'movies'
    paths = [legacy] if legacy.exists() or legacy.is_symlink() else []
    paths.extend(sorted((root / 'tests/fixtures').glob('*_schema_failure.json')))
    return paths


def cleanup(root=ROOT, apply=False):
    root = Path(root)
    paths = retired_paths(root)
    for path in paths:
        print(('Removing: ' if apply else 'Would remove: ') + str(path.relative_to(root)))
        if not apply:
            continue
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    return paths


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    paths = cleanup(apply=args.apply)
    if not paths:
        print('No retired paths found.')
