"""Tool catalog and provider schema materialization."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .base import ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool registration: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def names(self) -> Tuple[str, ...]:
        return tuple(self._specs.keys())

    def provider_tools(self) -> List[dict]:
        """Materialize OpenAI-compatible function tool definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.schema,
                },
            }
            for spec in self._specs.values()
        ]
