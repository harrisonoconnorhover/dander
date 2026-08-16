"""Credential-free probe for the immutable image qualification entrypoint."""

from __future__ import annotations

import json
import sys

print(
    json.dumps(
        {
            "arguments": sys.argv[1:],
            "schema": "io.dander.qualification.entrypoint-probe/v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
