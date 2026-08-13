#!/usr/bin/env python3
"""Regenerate the packaged Dander Control contract bundle."""

from dander.control.bundle import BUNDLE_ID, PACKAGED_BUNDLE_DIRECTORY, write_bundle


def main() -> None:
    write_bundle()
    print(f"Generated {BUNDLE_ID} at {PACKAGED_BUNDLE_DIRECTORY}.")


if __name__ == "__main__":
    main()
