"""Dump the OpenAPI schema as JSON for frontend type generation.

Usage: ``python -m coding_agent.web.openapi_json > schema.json``
The schema is emitted from the real app object so the generated TypeScript
types always match the shipped API. No home directory or credentials are
touched — the controller is only constructed, never queried.
"""

from __future__ import annotations

import json
import sys

from .server import build_app


def main() -> int:
    app = build_app()
    schema = app.openapi()
    print(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
