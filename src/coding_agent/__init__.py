"""Self-contained coding agent kernel.

The public surface is intentionally small: the CLI entry point and the core
modules. Application code should build on :mod:`coding_agent.agent` rather
than on vendor SDK objects, which never leave the model adapter boundary.
"""

__version__ = "0.1.0"
