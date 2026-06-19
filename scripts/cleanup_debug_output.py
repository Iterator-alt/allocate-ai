#!/usr/bin/env python
"""Remove debug output files older than 7 days.

Usage:
    python scripts/cleanup_debug_output.py
    python scripts/cleanup_debug_output.py --dry-run
    python scripts/cleanup_debug_output.py --max-age-days 3
"""
import argparse
import os
import shutil
import time
from pathlib import Path


def cleanup(debug_dir: Path, max_age_days: int = 7, dry_run: bool = False) -> int:
    """Remove files/directories older than max_age_days.

    Args:
        debug_dir: Path to debug_output directory
        max_age_days: Maximum age in days before deletion
        dry_run: If True, only print what would be deleted

    Returns:
        Number of items removed
    """
    if not debug_dir.exists():
        print(f"Directory does not exist: {debug_dir}")
        return 0

    max_age_seconds = max_age_days * 24 * 60 * 60
    now = time.time()
    removed = 0

    for item in debug_dir.iterdir():
        # Skip hidden files
        if item.name.startswith('.'):
            continue

        item_age = now - item.stat().st_mtime

        if item_age > max_age_seconds:
            age_days = item_age / (24 * 60 * 60)

            if dry_run:
                print(f"[DRY RUN] Would remove: {item.name} ({age_days:.1f} days old)")
            else:
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    print(f"Removed: {item.name} ({age_days:.1f} days old)")
                    removed += 1
                except Exception as e:
                    print(f"Failed to remove {item.name}: {e}")

    return removed


def main():
    parser = argparse.ArgumentParser(description="Clean up old debug output files")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=7,
        help="Maximum age in days before deletion (default: 7)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be deleted, don't actually delete"
    )
    args = parser.parse_args()

    # Find debug_output directory relative to script
    script_dir = Path(__file__).parent
    debug_dir = script_dir.parent / "debug_output"

    print(f"Cleaning up: {debug_dir}")
    print(f"Max age: {args.max_age_days} days")
    if args.dry_run:
        print("DRY RUN - no files will be deleted")
    print()

    removed = cleanup(debug_dir, args.max_age_days, args.dry_run)

    print()
    if args.dry_run:
        print(f"Would remove {removed} item(s)")
    else:
        print(f"Removed {removed} item(s)")


if __name__ == "__main__":
    main()
