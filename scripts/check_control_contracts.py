#!/usr/bin/env python3
"""Reject drift in the packaged Dander Control contract bundle."""

from dander.control.bundle import BUNDLE_ID, PACKAGED_BUNDLE_DIRECTORY, bundle_drift


def main() -> None:
    drift = bundle_drift()
    if drift:
        raise SystemExit(
            "Control contract bundle is stale; regenerate these files:\n- " + "\n- ".join(drift)
        )
    print(f"Validated {BUNDLE_ID} at {PACKAGED_BUNDLE_DIRECTORY}.")


if __name__ == "__main__":
    main()
