"""Optional model-based memory candidate extraction.

Candidate extraction is P1 and deliberately isolated: it never runs inside
AgentLoop, never has tool access, only receives bounded user/final-assistant
text and existing memory summaries, and every proposal is persisted as a
``candidate`` that still requires human approval before retrieval.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from typing import Any, Dict, List, Optional, Sequence

from ..models import AssistantTurn
from .models import (
    MEMORY_MAX_ENTRY_CHARS,
    MEMORY_MAX_TITLE_CHARS,
    MemoryKind,
    MemoryScope,
)
from .policy import MemoryPolicy, MemoryPolicyError

_CANDIDATE_PROMPT = """\
You are a memory summarizer for a local coding agent. Read the user request and
the assistant's final answer below, then propose at most {limit} durable,
non-sensitive facts that would be useful in future conversations in the same
workspace.

Rules:
- Only propose things a human would explicitly want remembered: stable project
  facts, preferences, decisions, or recurring procedures.
- Do not propose secrets, API keys, passwords, private keys, environment
  values, full command output, or file contents.
- Do not propose reasoning, hidden chain-of-thought, or transient errors unless
  they represent a durable project decision.
- Return ONLY a JSON array. Each item must be:
  {{"kind": "fact"|"preference"|"decision"|"procedure", "content": "...", "title": "...", "scope": "workspace"|"global"|"conversation", "rationale": "..."}}
- Keep content concise (at most {max_chars} characters).
"""

_EXISTING_MEMORY_LIMIT = 8


class MemoryCandidateExtractor:
    """Bound candidate proposal using the same model client as AgentLoop.

    This is an optional enhancement. Failures are swallowed by the caller so
    they never change the main turn's terminal state.
    """

    def __init__(
        self,
        model_client: Any,
        *,
        policy: Optional[MemoryPolicy] = None,
        max_candidates: int = 3,
        max_user_chars: int = 8_000,
        max_assistant_chars: int = 8_000,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._model_client = model_client
        self._policy = policy or MemoryPolicy()
        self.max_candidates = max_candidates
        self.max_user_chars = max_user_chars
        self.max_assistant_chars = max_assistant_chars
        self.timeout_seconds = max(0.01, float(timeout_seconds))

    def extract(
        self,
        *,
        user_text: str,
        assistant_text: str,
        existing_memories: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Return validated proposal dicts; never raises for model/parse errors."""
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                result_queue.put(
                    (
                        True,
                        self._call_model(user_text, assistant_text, existing_memories),
                    )
                )
            except Exception as exc:  # isolated optional model call
                result_queue.put((False, exc))

        threading.Thread(
            target=invoke,
            name="memory-candidate-extractor",
            daemon=True,
        ).start()
        try:
            ok, value = result_queue.get(timeout=self.timeout_seconds)
        except queue.Empty:
            return []
        if not ok:
            return []
        raw = str(value or "")
        proposals = self._parse(raw)
        if not proposals:
            return []
        validated: List[Dict[str, Any]] = []
        for proposal in proposals[: self.max_candidates]:
            item = self._validate(proposal)
            if item is not None:
                validated.append(item)
        return validated

    def _call_model(
        self,
        user_text: str,
        assistant_text: str,
        existing_memories: Sequence[Dict[str, Any]],
    ) -> str:
        existing = existing_memories[:_EXISTING_MEMORY_LIMIT]
        memory_lines = [
            f"- {item.get('title') or item.get('content', '')[:100]}"
            f" ({item.get('scope_type', 'unknown')})"
            for item in existing
        ]
        memory_section = "\n".join(memory_lines) or "(no existing memory)"
        user_section = (user_text or "")[: self.max_user_chars]
        assistant_section = (assistant_text or "")[: self.max_assistant_chars]
        user_message = (
            "Existing memories:\n"
            f"{memory_section}\n\n"
            "User request:\n"
            f"{user_section}\n\n"
            "Final assistant answer:\n"
            f"{assistant_section}\n\n"
            "Return a JSON array of memory proposals."
        )
        system = _CANDIDATE_PROMPT.format(
            limit=self.max_candidates,
            max_chars=MEMORY_MAX_ENTRY_CHARS,
        )
        turn = self._model_client.request(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            [],
        )
        if not isinstance(turn, AssistantTurn):
            text = getattr(turn, "text", "")
        else:
            text = turn.text or ""
        return str(text or "")

    @staticmethod
    def _parse(raw: str) -> List[Dict[str, Any]]:
        if not raw or not raw.strip():
            return []
        text = raw.strip()
        # Accept a JSON code fence, then fall back to the first array slice.
        fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        if fenced:
            candidate = fenced.group(1)
        else:
            start = text.find("[")
            end = text.rfind("]")
            if start < 0 or end <= start:
                return []
            candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _validate(self, proposal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        content = str(proposal.get("content", "")).strip()
        if not content:
            return None
        if len(content) > MEMORY_MAX_ENTRY_CHARS:
            content = content[:MEMORY_MAX_ENTRY_CHARS]
        kind = str(proposal.get("kind", "fact")).strip().lower()
        try:
            kind_value = MemoryKind(kind).value
        except ValueError:
            kind_value = MemoryKind.FACT.value
        try:
            scope_value = MemoryScope(
                str(proposal.get("scope", "workspace")).strip().lower()
            ).value
        except ValueError:
            scope_value = MemoryScope.WORKSPACE.value
        title = str(proposal.get("title", "")).strip()[:MEMORY_MAX_TITLE_CHARS] or None
        rationale = str(proposal.get("rationale", "")).strip()[:300] or ""
        # The service re-runs the secret policy before persistence; rejecting
        # here avoids spending a DB write on obvious secrets as well.
        try:
            self._policy.check(content)
        except MemoryPolicyError:
            return None
        return {
            "kind": kind_value,
            "scope_type": scope_value,
            "content": content,
            "title": title,
            "rationale": rationale,
        }
