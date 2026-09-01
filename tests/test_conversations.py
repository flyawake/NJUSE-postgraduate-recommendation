"""task_004 backend tests: repository, service, multi-turn, locks, change sets.

All tests are offline and use tmp_path for both agent home and workspace.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid

import pytest

import coding_agent.changes.collector as collector_module
from coding_agent.artifacts.store import ArtifactCorruptError, ArtifactStore
from coding_agent.changes.collector import ToolChangeCollector
from coding_agent.conversations.runtime import RuntimeRegistry
from coding_agent.conversations.service import (
    ConversationService,
    ConversationServiceError,
)
from coding_agent.conversations.store import (
    SCHEMA_VERSION,
    SQLiteConversationRepository,
)
from coding_agent.models import (
    AssistantMessage,
    AssistantTurn,
    SystemMessage,
    ToolCall,
    UserMessage,
)
from coding_agent.prompt import SYSTEM_PROMPT
from coding_agent.streaming import StreamCompleted, StreamStarted, TextDelta
from coding_agent.tools.base import PreparedCall, ToolEffect, ToolOutcome, ToolSpec


def make_call(name: str, args: dict, call_id: str | None = None) -> ToolCall:
    return ToolCall(
        id=call_id or f"call-{name}",
        name=name,
        arguments_raw=json.dumps(args, ensure_ascii=False, sort_keys=True),
    )


def wait_turn(service: ConversationService, conversation_id: str, turn_id: str):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        turn = service.get_turn(conversation_id, turn_id)
        if turn["state"] in ("success", "error", "interrupted", "rejected"):
            return turn
        time.sleep(0.05)
    raise AssertionError("turn did not reach a terminal state")


class FinalModel:
    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    def request(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        self.requests.append(messages)
        return AssistantTurn(text="done", tool_calls=())


class WriteThenFinalModel:
    def __init__(self) -> None:
        self.turn = 0
        self.requests: list[list[dict]] = []

    def request(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        self.turn += 1
        self.requests.append(messages)
        if self.turn == 1:
            return AssistantTurn(
                text="",
                tool_calls=(
                    make_call(
                        "write_file",
                        {"path": "new.txt", "content": "hello\n"},
                        "call-write",
                    ),
                ),
            )
        return AssistantTurn(text="done", tool_calls=())


class TextStreamingModel:
    def __init__(self) -> None:
        self.requests = 0

    def request(self, messages, tools):
        raise AssertionError("should stream")

    def stream(self, messages, tools, *, options=None, cancel=None):
        self.requests += 1
        yield StreamStarted()
        yield TextDelta(0, "hello ")
        yield TextDelta(0, "world")
        yield StreamCompleted(finish_reason="stop")


class BlockingModel:
    def __init__(self) -> None:
        self.release = True
        self.started = time.monotonic()

    def request(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        if self.release:
            time.sleep(0.2)
        else:
            time.sleep(1.0)
        return AssistantTurn(text="done", tool_calls=())


class DuplicateCallIdModel:
    def request(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        return AssistantTurn(
            text="",
            tool_calls=(
                make_call("glob", {"pattern": "*.py"}, "duplicate"),
                make_call("glob", {"pattern": "*.txt"}, "duplicate"),
            ),
        )


class PlanningModel:
    def __init__(self) -> None:
        self.request_count = 0

    def request(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        self.request_count += 1
        if self.request_count <= 2:
            complete = self.request_count == 2
            return AssistantTurn(
                text="",
                tool_calls=(
                    make_call(
                        "update_plan",
                        {
                            "explanation": "cross-layer work",
                            "plan": [
                                {
                                    "step": "Inspect architecture",
                                    "status": "completed"
                                    if complete
                                    else "in_progress",
                                },
                                {
                                    "step": "Verify integration",
                                    "status": "completed" if complete else "pending",
                                },
                            ],
                        },
                        f"plan-{self.request_count}",
                    ),
                ),
            )
        return AssistantTurn(text="planned work done", tool_calls=())


@pytest.fixture
def workspace_factory(tmp_path):
    def make():
        path = tmp_path / f"ws-{uuid.uuid4().hex}"
        path.mkdir()
        (path / "a.txt").write_text("base", encoding="utf-8")
        return path

    return make


def make_service(tmp_path, model, env=None):
    home = tmp_path / f"home-{time.time_ns()}"
    return ConversationService(
        home=home,
        env=env or {"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "fake-model"},
        client_factory=lambda _connection: model,
    )


class TestRepositoryBasics:
    def test_v17_migration_adds_revisioned_turn_plans(self, tmp_path):
        database = tmp_path / "state.db"
        baseline = SQLiteConversationRepository(database, create_backups=False)
        baseline.initialize()
        conn = baseline._connect()
        conn.execute("DROP TABLE turn_plan_revisions")
        conn.execute("DROP TABLE turn_plans")
        conn.execute("DELETE FROM schema_meta")
        conn.execute("INSERT INTO schema_meta(version, applied_at) VALUES (17, 'old')")
        conn.commit()
        baseline.close()

        migrated = SQLiteConversationRepository(database, create_backups=False)
        migrated.initialize()
        tables = {
            str(row["name"])
            for row in migrated._connect().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"turn_plans", "turn_plan_revisions"} <= tables
        assert (
            migrated._connect()
            .execute("SELECT version FROM schema_meta")
            .fetchone()["version"]
            == SCHEMA_VERSION
        )

    def test_v14_migration_adds_conversation_reasoning_preference(self, tmp_path):
        path = tmp_path / "v14.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE schema_meta(version INTEGER NOT NULL, applied_at TEXT NOT NULL);
            INSERT INTO schema_meta VALUES (14, 'old');
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                title_source TEXT NOT NULL,
                workspace_path TEXT NOT NULL,
                workspace_key TEXT NOT NULL,
                profile_id TEXT,
                state TEXT NOT NULL,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                archived_at TEXT
            );
            INSERT INTO conversations VALUES (
                'conversation', 'title', 'auto', 'E:/ws', 'ws', NULL,
                'active', 1, 'created', 'active-at', NULL
            );
            """
        )
        conn.commit()
        conn.close()

        repo = SQLiteConversationRepository(path, create_backups=False)
        repo.initialize()
        columns = {
            row["name"]
            for row in repo._connect().execute("PRAGMA table_info(conversations)")
        }
        assert "reasoning_effort" in columns
        assert "command_policy" in columns
        assert repo.get_conversation("conversation").reasoning_effort is None
        assert repo.get_conversation("conversation").command_policy == "ask"
        assert (
            repo._connect()
            .execute("SELECT version FROM schema_meta")
            .fetchone()["version"]
            == SCHEMA_VERSION
        )

    def test_v5_inbox_migration_is_idempotent_and_adds_state_guard(self, tmp_path):
        database = tmp_path / "state.db"
        baseline = SQLiteConversationRepository(database)
        baseline.initialize()
        conn = baseline._connect()
        conn.execute("DROP TRIGGER enforce_inbox_state_transition")
        conn.execute("DROP TABLE inbox_events")
        conn.execute("DROP TABLE inbox_items")
        conn.execute("DROP TABLE inbox_meta")
        conn.execute("DELETE FROM schema_meta")
        conn.execute("INSERT INTO schema_meta(version, applied_at) VALUES (5, 'old')")
        conn.commit()
        baseline.close()

        migrated = SQLiteConversationRepository(database)
        migrated.initialize()
        columns = {
            str(row["name"])
            for row in migrated._connect().execute("PRAGMA table_info(inbox_items)")
        }
        assert "reasoning_effort" in columns
        trigger = (
            migrated._connect()
            .execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name='enforce_inbox_state_transition'"
            )
            .fetchone()
        )
        assert trigger is not None
        # A second startup is a no-op, not a destructive re-migration.
        migrated.initialize()

    def test_inbox_state_transitions_are_enforced_by_sqlite(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        conversation = repo.create_conversation(
            workspace_path=str(workspace),
            workspace_key=str(workspace),
            profile_id=None,
            title="state trigger",
        )
        item = repo.enqueue_inbox_item(
            conversation.id, content="remove permanently", requested_mode="queue"
        )
        repo.remove_inbox_item(conversation.id, item["id"], item["version"])
        with pytest.raises(
            sqlite3.IntegrityError, match="invalid_inbox_state_transition"
        ):
            repo._connect().execute(
                "UPDATE inbox_items SET state='queued' WHERE id=?", (item["id"],)
            )

    def test_atomic_queue_claim_recovers_without_ghost_turn(self, tmp_path):
        """Crash after the claim transaction has one durable, non-running turn."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        database = tmp_path / "state.db"
        repo = SQLiteConversationRepository(database)
        repo.initialize()
        conversation = repo.create_conversation(
            workspace_path=str(workspace),
            workspace_key=str(workspace),
            profile_id=None,
            title="queue recovery",
        )
        item = repo.enqueue_inbox_item(
            conversation.id, content="recover queue", requested_mode="queue"
        )
        turn, created = repo.create_turn_with_initial_messages(
            conversation.id,
            user_text="recover queue",
            run_id="run-queue-recovery",
            idempotency_key=f"inbox:{item['id']}:{item['version']}",
            messages=(
                SystemMessage(SYSTEM_PROMPT),
                UserMessage("recover queue", source="user"),
            ),
            inbox_item_id=item["id"],
        )
        assert created
        repo.close()

        restarted = SQLiteConversationRepository(database)
        restarted.initialize()
        recovered = restarted.recover_active_turns()
        assert [record.id for record in recovered] == [turn.id]
        assert restarted.get_active_turn(conversation.id) is None
        snapshot = restarted.get_inbox_snapshot(conversation.id)
        assert snapshot["items"][0]["state"] == "delivered"
        assert snapshot["items"][0]["claimed_turn_id"] == turn.id

    def test_restart_demotes_claimed_steer_to_fifo_queue(self, tmp_path):
        """A steer claimed before its acknowledgement is never silently lost."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        conversation = repo.create_conversation(
            workspace_path=str(workspace),
            workspace_key=str(workspace),
            profile_id=None,
            title="steer recovery",
        )
        turn, _ = repo.create_turn_with_initial_messages(
            conversation.id,
            user_text="initial",
            run_id="run-steer-recovery",
            idempotency_key="initial",
            messages=(
                SystemMessage(SYSTEM_PROMPT),
                UserMessage("initial", source="user"),
            ),
        )
        item = repo.enqueue_inbox_item(
            conversation.id,
            content="keep this steer",
            requested_mode="steer",
            bound_turn_id=turn.id,
        )
        assert repo.mark_steer_claimed_for_delivery(
            conversation.id, turn.id, item["id"]
        )
        repo.recover_active_turns()
        assert repo.recover_claimed_steers() == [item["id"]]
        snapshot = repo.get_inbox_snapshot(conversation.id)
        assert snapshot["items"][0]["state"] == "queued"
        assert snapshot["items"][0]["bound_turn_id"] is None

    def test_v4_stream_checkpoint_migration_adds_event_cursor(self, tmp_path):
        path = tmp_path / "v4.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE schema_meta(version INTEGER NOT NULL, applied_at TEXT NOT NULL);
            INSERT INTO schema_meta VALUES (4, 'old');
            CREATE TABLE stream_checkpoints(
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                channel TEXT NOT NULL,
                text TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(turn_id, attempt, channel)
            );
            """
        )
        conn.commit()
        conn.close()
        repo = SQLiteConversationRepository(path, create_backups=False)
        repo.initialize()
        columns = {
            row["name"]
            for row in repo._connect().execute("PRAGMA table_info(stream_checkpoints)")
        }
        assert "event_seq" in columns

    def test_create_and_crud(self, workspace_factory, tmp_path):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws),
            workspace_key=str(ws),
            profile_id=None,
            title="新会话",
        )
        assert conv.state == "active"
        assert repo.get_conversation(conv.id) is not None

        preferred = repo.set_conversation_reasoning_effort(conv.id, "high")
        assert preferred.reasoning_effort == "high"
        assert preferred.version == 1
        repo.close()
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        assert repo.get_conversation(conv.id).reasoning_effort == "high"

        second = repo.create_conversation(
            workspace_path=str(ws),
            workspace_key=str(ws),
            profile_id=None,
            title="另一个会话",
        )
        repo.set_conversation_command_policy(conv.id, "allow")
        repo.set_conversation_command_policy(second.id, "deny")
        repo.close()
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        assert repo.get_conversation(conv.id).command_policy == "allow"
        assert repo.get_conversation(second.id).command_policy == "deny"

        renamed = repo.rename_conversation(conv.id, title="改名", expected_version=1)
        assert renamed.title == "改名" and renamed.version == 2
        with pytest.raises(ValueError):
            repo.rename_conversation(conv.id, title="旧", expected_version=1)

        archived = repo.set_conversation_state(
            conv.id, state="archived", expected_version=2
        )
        assert archived.state == "archived"
        active = repo.set_conversation_state(
            conv.id, state="active", expected_version=3
        )
        assert active.state == "active"
        assert repo.verify_conversation_projection(conv.id)

        repo._connect().execute(
            "UPDATE conversations SET title='tampered' WHERE id=?", (conv.id,)
        )
        repo._connect().commit()
        assert not repo.verify_conversation_projection(conv.id)
        repo._connect().execute(
            "UPDATE conversations SET title='改名' WHERE id=?", (conv.id,)
        )
        repo._connect().commit()

        repo.delete_conversation(conv.id, expected_version=4)
        assert repo.get_conversation(conv.id) is None

    def test_stream_checkpoint_roundtrip(self, workspace_factory, tmp_path):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        turn = repo.create_turn(conv.id, user_text="stream", run_id="run")
        repo.upsert_stream_checkpoints_batch(
            [
                {
                    "conversation_id": conv.id,
                    "turn_id": turn.id,
                    "run_id": "run",
                    "attempt": 1,
                    "channel": "text",
                    "text": "hello ",
                    "event_seq": 1,
                },
                {
                    "conversation_id": conv.id,
                    "turn_id": turn.id,
                    "run_id": "run",
                    "attempt": 1,
                    "channel": "reasoning",
                    "text": "thinking",
                    "event_seq": 2,
                },
            ]
        )
        tail = {
            "conversation_id": conv.id,
            "turn_id": turn.id,
            "run_id": "run",
            "attempt": 1,
            "channel": "text",
            "text": "world",
            "event_seq": 3,
        }
        repo.upsert_stream_checkpoints_batch([tail])
        # An ambiguous retry of the same committed batch is idempotent.
        repo.upsert_stream_checkpoints_batch([tail])
        rows = repo.get_stream_checkpoints(conv.id, turn.id)
        by_channel = {row["channel"]: row["text"] for row in rows}
        assert by_channel["text"] == "hello world"
        assert by_channel["reasoning"] == "thinking"
        assert {row["channel"]: row["event_seq"] for row in rows}["text"] == 3

    def test_cursor_pagination_stable(self, workspace_factory, tmp_path):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        for index in range(5):
            ws = workspace_factory()
            repo.create_conversation(
                workspace_path=str(ws),
                workspace_key=str(ws),
                profile_id=None,
                title=f"c{index}",
            )
        page, cursor = repo.list_conversations(limit=2)
        assert len(page) == 2 and cursor
        page2, cursor2 = repo.list_conversations(limit=2, cursor=cursor)
        assert len(page2) == 2 and cursor2
        page3, cursor3 = repo.list_conversations(limit=2, cursor=cursor2)
        assert len(page3) == 1 and cursor3 is None

    def test_201_item_cursor_pagination_has_no_gaps(self, workspace_factory, tmp_path):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        expected = set()
        for index in range(201):
            ws = workspace_factory()
            expected.add(
                repo.create_conversation(
                    workspace_path=str(ws),
                    workspace_key=str(ws),
                    profile_id=None,
                    title=f"c{index:03d}",
                ).id
            )
        found = []
        page, cursor = repo.list_conversations(limit=50)
        found.extend(item.id for item in page)
        inserted_ws = workspace_factory()
        inserted = repo.create_conversation(
            workspace_path=str(inserted_ws),
            workspace_key=str(inserted_ws),
            profile_id=None,
            title="inserted-after-first-page",
        )
        while True:
            if cursor is None:
                break
            page, cursor = repo.list_conversations(limit=50, cursor=cursor)
            found.extend(item.id for item in page)
        assert len(found) == len(set(found)) == 201
        assert set(found) == expected
        assert inserted.id not in found
        fresh_page, _ = repo.list_conversations(limit=50)
        assert fresh_page[0].id == inserted.id

    def test_201_turn_cursor_pagination_is_stable_during_insert(
        self, workspace_factory, tmp_path
    ):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        expected = set()
        for index in range(201):
            turn = repo.create_turn(
                conv.id, user_text=f"turn-{index:03d}", run_id=uuid.uuid4().hex
            )
            expected.add(turn.id)
            repo.update_turn_state(conv.id, turn.id, state="success")

        found = []
        page, cursor = repo.list_turns(conv.id, limit=50)
        found.extend(item.id for item in page)
        inserted = repo.create_turn(
            conv.id, user_text="inserted-after-first-page", run_id=uuid.uuid4().hex
        )
        repo.update_turn_state(conv.id, inserted.id, state="success")
        while cursor is not None:
            page, cursor = repo.list_turns(conv.id, limit=50, cursor=cursor)
            found.extend(item.id for item in page)
        assert len(found) == len(set(found)) == 201
        assert set(found) == expected
        assert inserted.id not in found
        fresh_page, _ = repo.list_turns(conv.id, limit=50)
        assert fresh_page[0].id == inserted.id

    def test_turn_and_initial_canonical_group_are_atomic(
        self, workspace_factory, tmp_path
    ):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        repo._connect().execute(
            """
            CREATE TRIGGER fail_initial_canonical BEFORE INSERT ON canonical_items
            BEGIN SELECT RAISE(ABORT, 'injected'); END
            """
        )
        with pytest.raises(Exception):
            repo.create_turn_with_initial_messages(
                conv.id,
                user_text="hello",
                run_id="run",
                idempotency_key="once",
                messages=[SystemMessage("system"), UserMessage("hello")],
            )
        assert repo.get_active_turn(conv.id) is None
        turns, _ = repo.list_turns(conv.id)
        assert turns == []

    def test_durable_idempotency_is_safe_under_concurrency(
        self, workspace_factory, tmp_path
    ):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        barrier = threading.Barrier(3)
        results = []

        def create() -> None:
            barrier.wait()
            results.append(
                repo.create_turn_with_initial_messages(
                    conv.id,
                    user_text="hello",
                    run_id=uuid.uuid4().hex,
                    idempotency_key="same-key",
                    messages=[SystemMessage("system"), UserMessage("hello")],
                )
            )

        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert len(results) == 2
        assert {item[0].id for item in results} == {results[0][0].id}
        assert sorted(item[1] for item in results) == [False, True]

    def test_failed_v2_migration_rolls_back_version_and_keeps_backup(
        self, workspace_factory, tmp_path
    ):
        db_path = tmp_path / "state.db"
        repo = SQLiteConversationRepository(db_path)
        repo.initialize()
        ws = workspace_factory()
        repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        repo.close()
        conn = sqlite3.connect(db_path)
        conn.execute("DROP TABLE conversation_events")
        conn.execute("CREATE TABLE conversation_events(id TEXT)")
        conn.execute("UPDATE schema_meta SET version=2")
        conn.commit()
        conn.close()

        broken = SQLiteConversationRepository(db_path)
        with pytest.raises(sqlite3.Error):
            broken.initialize()
        version = (
            broken._connect()
            .execute("SELECT version FROM schema_meta")
            .fetchone()["version"]
        )
        assert version == 2
        assert list((tmp_path / "backups").glob("state-v2-*.db"))

    def test_pending_tool_group_recovers_with_synthetic_result(
        self, workspace_factory, tmp_path
    ):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        turn = repo.create_turn(conv.id, user_text="hello", run_id="run")
        group_id, _ = repo.begin_canonical_group(conv.id, turn.id, kind="tool")
        call = make_call("write_file", {"path": "x.txt", "content": "x"}, "c1")
        repo.append_canonical_item(
            conv.id,
            turn.id,
            group_id,
            repo.next_canonical_seq(conv.id),
            AssistantMessage(text="", tool_calls=(call,)),
        )
        recovered = repo.recover_pending_groups_for_turn(conv.id, turn.id)
        assert recovered == 1
        history = repo.get_canonical_history(conv.id)
        assert len(history) == 2
        tool_messages = [m for m in history if getattr(m, "tool_call_id", None) == "c1"]
        assert len(tool_messages) == 1
        assert tool_messages[0].ok is False

    def test_recover_active_turns(self, workspace_factory, tmp_path):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        turn = repo.create_turn(conv.id, user_text="hello", run_id="run")
        repo.update_turn_state(conv.id, turn.id, state="running")
        repo.save_turn_plan(
            conv.id,
            turn.id,
            revision=1,
            state="active",
            explanation="complex task",
            steps=[
                {"step": "Inspect", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ],
            expected_revision=0,
        )
        recovered = repo.recover_active_turns()
        assert len(recovered) == 1
        turned = repo.get_turn(conv.id, turn.id)
        assert turned.state == "interrupted"
        assert turned.error_code == "PROCESS_RESTARTED"
        assert repo.get_turn_plan(conv.id, turn.id)["state"] == "interrupted"

    def test_turn_plan_revisions_are_atomic_and_auditable(
        self, workspace_factory, tmp_path
    ):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        turn = repo.create_turn(conv.id, user_text="complex", run_id="run")
        first_steps = [
            {"step": "Inspect", "status": "in_progress"},
            {"step": "Verify", "status": "pending"},
        ]
        repo.save_turn_plan(
            conv.id,
            turn.id,
            revision=1,
            state="active",
            explanation="initial",
            steps=first_steps,
            expected_revision=0,
        )
        second_steps = [
            {"step": "Inspect", "status": "completed"},
            {"step": "Verify", "status": "completed"},
        ]
        repo.save_turn_plan(
            conv.id,
            turn.id,
            revision=2,
            state="active",
            explanation="finished",
            steps=second_steps,
            expected_revision=1,
        )

        assert repo.get_turn_plan(conv.id, turn.id)["steps"] == second_steps
        assert [
            item["revision"] for item in repo.list_turn_plan_revisions(conv.id, turn.id)
        ] == [1, 2]
        with pytest.raises(ValueError, match="plan_revision_conflict"):
            repo.save_turn_plan(
                conv.id,
                turn.id,
                revision=2,
                state="active",
                explanation="stale",
                steps=first_steps,
                expected_revision=1,
            )
        repo.finish_turn_plan(conv.id, turn.id, state="completed")
        assert repo.get_turn_plan(conv.id, turn.id)["state"] == "completed"

    def test_restart_reconciles_plan_left_active_after_terminal_turn(
        self, workspace_factory, tmp_path
    ):
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        ws = workspace_factory()
        conv = repo.create_conversation(
            workspace_path=str(ws), workspace_key=str(ws), profile_id=None, title="t"
        )
        turn = repo.create_turn(conv.id, user_text="complex", run_id="run")
        repo.save_turn_plan(
            conv.id,
            turn.id,
            revision=1,
            state="active",
            explanation="done but terminal callback failed",
            steps=[
                {"step": "Inspect", "status": "completed"},
                {"step": "Verify", "status": "completed"},
            ],
            expected_revision=0,
        )
        repo.set_turn_terminal(conv.id, turn.id, state="success", result_json="{}")

        assert repo.recover_active_turns() == []
        assert repo.get_turn_plan(conv.id, turn.id)["state"] == "completed"


class TestConversationService:
    def test_plan_is_persisted_and_returned_with_terminal_turn(
        self, tmp_path, workspace_factory
    ):
        service = make_service(tmp_path, PlanningModel())
        try:
            workspace = workspace_factory()
            conversation = service.create_conversation(
                workspace_path=str(workspace), profile_id=None
            )
            started = service.start_turn(
                conversation["id"], user_text="refactor multiple layers"
            )
            finished = wait_turn(service, conversation["id"], started["id"])

            assert finished["state"] == "success"
            assert finished["plan"]["revision"] == 2
            assert finished["plan"]["state"] == "completed"
            assert (
                len(
                    service._repository.list_turn_plan_revisions(
                        conversation["id"], started["id"]
                    )
                )
                == 2
            )
            events = service.get_events(
                conversation["id"], started["id"], after_seq=0, limit=100
            )
            assert [event["kind"] for event in events].count("plan_updated") == 2
        finally:
            service.shutdown(timeout=5)

    def test_command_policy_survives_service_restart_per_conversation(
        self, tmp_path, workspace_factory
    ):
        home = tmp_path / "persistent-home"
        env = {"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "fake-model"}
        first_service = ConversationService(
            home=home,
            env=env,
            client_factory=lambda _connection: FinalModel(),
        )
        workspace = workspace_factory()
        first = first_service.create_conversation(
            workspace_path=str(workspace), profile_id=None
        )
        second = first_service.create_conversation(
            workspace_path=str(workspace), profile_id=None
        )
        first_service.set_conversation_command_policy(first["id"], "allow")
        first_service.set_conversation_command_policy(second["id"], "deny")
        first_service.shutdown()
        first_service._repository.close()

        restarted = ConversationService(
            home=home,
            env=env,
            client_factory=lambda _connection: FinalModel(),
        )
        try:
            assert restarted.get_conversation(first["id"])["command_policy"] == "allow"
            assert restarted.get_conversation(second["id"])["command_policy"] == "deny"
        finally:
            restarted.shutdown()

    def test_duplicate_tool_call_ids_never_commit_to_canonical_history(
        self, tmp_path, workspace_factory
    ):
        service = make_service(tmp_path, DuplicateCallIdModel())
        try:
            workspace = workspace_factory()
            conversation = service.create_conversation(
                workspace_path=str(workspace), profile_id=None
            )
            started = service.start_turn(conversation["id"], user_text="inspect")
            finished = wait_turn(service, conversation["id"], started["id"])
            assert finished["state"] == "error"
            history = service._repository.get_canonical_history(conversation["id"])
            assert not any(
                isinstance(message, AssistantMessage)
                and len(message.tool_calls) == 2
                and {call.id for call in message.tool_calls} == {"duplicate"}
                for message in history
            )
            states = (
                service._repository._connect()
                .execute(
                    "SELECT kind, state FROM canonical_groups WHERE turn_id=? ORDER BY group_seq",
                    (started["id"],),
                )
                .fetchall()
            )
            assert not any(row["kind"] == "tool" for row in states)
        finally:
            service.shutdown(timeout=5)

    def test_multi_turn_history_isolated_between_conversations(
        self, tmp_path, workspace_factory
    ):
        model = FinalModel()
        service = make_service(tmp_path, model)
        try:
            ws_a = workspace_factory()
            ws_b = workspace_factory()
            a = service.create_conversation(workspace_path=str(ws_a), profile_id=None)
            b = service.create_conversation(workspace_path=str(ws_b), profile_id=None)
            t1 = service.start_turn(a["id"], user_text="first task in A")
            wait_turn(service, a["id"], t1["id"])
            t2 = service.start_turn(b["id"], user_text="only task in B")
            wait_turn(service, b["id"], t2["id"])
            t3 = service.start_turn(a["id"], user_text="follow-up in A")
            wait_turn(service, a["id"], t3["id"])
            # Requests are recorded in start order: A1, B1, A2.
            assert len(model.requests) >= 3
            a2_messages = " ".join(str(message) for message in model.requests[2])
            assert "first task in A" in a2_messages
            assert "follow-up in A" in a2_messages
            # B never saw A's history.
            b_messages = " ".join(str(message) for message in model.requests[1])
            assert "first task in A" not in b_messages
            # Canonical history is stored and can be reloaded.
            history = service._repository.get_canonical_history(a["id"])
            assert any(
                getattr(message, "content", "") == "first task in A"
                for message in history
            )
        finally:
            service.shutdown(timeout=5)

    def test_inbox_snapshot_and_crud(self, tmp_path, workspace_factory):
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            snap = service.enqueue_inbox(
                conv["id"], content="first queued", mode="queue"
            )
            assert snap["queue_version"] == 2
            item = snap["items"][0]
            assert item["state"] == "queued"
            snap2 = service.edit_inbox(
                conv["id"],
                item["id"],
                content="edited queued",
                expected_version=item["version"],
            )
            assert snap2["items"][0]["content"] == "edited queued"
            snap3 = service.remove_inbox(
                conv["id"],
                item["id"],
                expected_version=snap2["items"][0]["version"],
            )
            assert snap3["items"] == []
        finally:
            service.shutdown(timeout=5)

    def test_queue_fifo_single_consumer(self, tmp_path, workspace_factory):
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            first = service.start_turn(conv["id"], user_text="initial")
            wait_turn(service, conv["id"], first["id"])
            for text in ("one", "two", "three"):
                service.enqueue_inbox(conv["id"], content=text, mode="queue")
            service._after_turn_finished(conv["id"])
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                turns = service.list_turns(conv["id"])["items"]
                inbox = service.get_inbox(conv["id"])
                if len(turns) == 4 and all(
                    item["state"] == "delivered" for item in inbox["items"]
                ):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("queue did not drain one turn at a time")
            turns = service.list_turns(conv["id"])["items"]
            texts = [
                turn["user_text"]
                for turn in sorted(turns, key=lambda turn: turn["ordinal"])
            ]
            assert texts == ["initial", "one", "two", "three"]
            inbox = service.get_inbox(conv["id"])
            assert all(item["state"] == "delivered" for item in inbox["items"])
        finally:
            service.shutdown(timeout=5)

    def test_concurrent_idempotent_enqueue_creates_one_item(
        self, tmp_path, workspace_factory
    ):
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            results: list[dict] = []

            def enqueue() -> None:
                results.append(
                    service.enqueue_inbox(
                        conv["id"],
                        content="same message",
                        mode="queue",
                        idempotency_key="same-key",
                    )
                )

            threads = [threading.Thread(target=enqueue) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            items = results[0]["items"]
            assert len(items) == 1
        finally:
            service.shutdown(timeout=5)

    def test_queue_claim_uses_transactional_current_content(
        self, tmp_path, workspace_factory, monkeypatch
    ):
        """An edit between the consumer's read and claim changes the opener.

        The Barrier fixes the interleaving: it is not a timing-dependent
        sleep.  The turn, canonical message, and delivered inbox row must all
        contain the version that committed before the claim transaction.
        """
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            item = service.enqueue_inbox(
                conv["id"], content="before edit", mode="queue"
            )["items"][0]
            barrier = threading.Barrier(2)
            start_turn = service.start_turn

            def paused_start(*args, **kwargs):
                barrier.wait(timeout=5)
                return start_turn(*args, **kwargs)

            monkeypatch.setattr(service, "start_turn", paused_start)
            consumer = threading.Thread(
                target=service._start_next_from_queue, args=(conv["id"],)
            )
            consumer.start()
            barrier.wait(timeout=5)
            service.edit_inbox(
                conv["id"],
                item["id"],
                content="after edit",
                expected_version=item["version"],
            )
            consumer.join(timeout=5)
            assert not consumer.is_alive()
            turns = service.list_turns(conv["id"])["items"]
            assert len(turns) == 1
            assert turns[0]["user_text"] == "after edit"
            history = service._repository.get_canonical_history(conv["id"])
            assert any(
                isinstance(message, UserMessage) and message.content == "after edit"
                for message in history
            )
            inbox = service.get_inbox(conv["id"])["items"]
            assert inbox[0]["state"] == "delivered"
            assert inbox[0]["claimed_turn_id"] == turns[0]["id"]
        finally:
            service.shutdown(timeout=5)

    def test_removed_queue_item_cannot_create_turn(
        self, tmp_path, workspace_factory, monkeypatch
    ):
        """A successful delete wins over a later claim of the stale head."""
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            item = service.enqueue_inbox(
                conv["id"], content="remove before claim", mode="queue"
            )["items"][0]
            barrier = threading.Barrier(2)
            start_turn = service.start_turn

            def paused_start(*args, **kwargs):
                barrier.wait(timeout=5)
                return start_turn(*args, **kwargs)

            monkeypatch.setattr(service, "start_turn", paused_start)
            consumer = threading.Thread(
                target=service._start_next_from_queue, args=(conv["id"],)
            )
            consumer.start()
            barrier.wait(timeout=5)
            service.remove_inbox(
                conv["id"], item["id"], expected_version=item["version"]
            )
            consumer.join(timeout=5)
            assert not consumer.is_alive()
            assert service.list_turns(conv["id"])["items"] == []
            assert service.get_inbox(conv["id"])["items"] == []
        finally:
            service.shutdown(timeout=5)

    def test_reentrant_terminal_callback_claims_only_one_item(
        self, tmp_path, workspace_factory, monkeypatch
    ):
        """The terminal callback has a per-conversation single-consumer lock."""
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            service.enqueue_inbox(conv["id"], content="only once", mode="queue")
            entered = threading.Event()
            release = threading.Event()
            start_turn = service.start_turn

            def paused_start(*args, **kwargs):
                entered.set()
                assert release.wait(timeout=5)
                return start_turn(*args, **kwargs)

            monkeypatch.setattr(service, "start_turn", paused_start)
            first = threading.Thread(
                target=service._after_turn_finished, args=(conv["id"],)
            )
            first.start()
            assert entered.wait(timeout=5)
            second = threading.Thread(
                target=service._after_turn_finished, args=(conv["id"],)
            )
            second.start()
            second.join(timeout=5)
            assert not second.is_alive()
            release.set()
            first.join(timeout=5)
            assert not first.is_alive()
            assert len(service.list_turns(conv["id"])["items"]) == 1
        finally:
            service.shutdown(timeout=5)

    def test_failed_queue_start_blocks_then_retry_consumes_item(
        self, tmp_path, workspace_factory, monkeypatch
    ):
        """A failure after atomic turn creation leaves a recoverable queue row."""
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            service.enqueue_inbox(conv["id"], content="recover me", mode="queue")
            build_loop = service._build_loop

            def fail_build(**_kwargs):
                raise RuntimeError("injected loop build failure")

            monkeypatch.setattr(service, "_build_loop", fail_build)
            service._start_next_from_queue(conv["id"])
            failed_item = service.get_inbox(conv["id"])["items"][0]
            assert failed_item["state"] == "blocked"
            failed_turn = service.list_turns(conv["id"])["items"]
            assert len(failed_turn) == 1
            assert failed_turn[0]["state"] == "rejected"

            monkeypatch.setattr(service, "_build_loop", build_loop)
            service.retry_inbox(
                conv["id"], failed_item["id"], expected_version=failed_item["version"]
            )
            inbox = service.get_inbox(conv["id"])["items"]
            assert inbox[0]["state"] == "delivered"
            turns = service.list_turns(conv["id"])["items"]
            assert len(turns) == 2
            assert turns[1]["user_text"] == "recover me"
        finally:
            service.shutdown(timeout=5)

    def test_large_queue_snapshot_bounded(self, tmp_path, workspace_factory):
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            for index in range(100):
                service.enqueue_inbox(conv["id"], content=f"item-{index}", mode="queue")
            snapshot = service.get_inbox(conv["id"])
            assert len(snapshot["items"]) == 100
            assert snapshot["queue_version"] == 101
        finally:
            service.shutdown(timeout=5)

    def test_steer_requires_active_turn(self, tmp_path, workspace_factory):
        service = make_service(tmp_path, FinalModel())
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            service.enqueue_inbox(conv["id"], content="steer me", mode="queue")
            snap = service.get_inbox(conv["id"])
            item = snap["items"][0]
            with pytest.raises(ConversationServiceError) as exc:
                service.steer_inbox(
                    conv["id"], item["id"], expected_version=item["version"]
                )
            assert exc.value.code == "turn_not_steerable"
        finally:
            service.shutdown(timeout=5)

    def test_stream_snapshot_recovers_full_text(self, tmp_path, workspace_factory):
        model = TextStreamingModel()
        service = make_service(tmp_path, model)
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            turn = service.start_turn(conv["id"], user_text="stream text")
            wait_turn(service, conv["id"], turn["id"])
            snapshot = service.get_stream_snapshot(conv["id"], turn["id"])
            text = next(
                item["text"]
                for item in snapshot
                if item["channel"] == "text" and item["attempt"] == 1
            )
            assert text == "hello world"
        finally:
            service.shutdown(timeout=5)

    def test_shutdown_timeout_marks_active_turn_interrupted(
        self, tmp_path, workspace_factory
    ):
        model = BlockingModel()
        model.release = False
        service = make_service(tmp_path, model)
        ws = workspace_factory()
        conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
        turn = service.start_turn(conv["id"], user_text="slow")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if service.get_turn(conv["id"], turn["id"])["state"] == "running":
                break
            time.sleep(0.01)
        started = time.monotonic()
        service.shutdown(timeout=0.01)
        assert time.monotonic() - started < 0.5
        recovered = service.get_turn(conv["id"], turn["id"])
        assert recovered["state"] == "interrupted"
        assert recovered["error_code"] in {"INTERRUPTED", "PROCESS_RESTARTED"}

    def test_different_workspaces_obey_global_worker_limit(self):
        registry = RuntimeRegistry(max_workers=1)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_finished = threading.Event()

        def first() -> None:
            first_started.set()
            release_first.wait(timeout=2)

        def second() -> None:
            second_started.set()
            second_finished.set()

        registry.submit("c1", "workspace-1", turn_id="t1", run_id="r1", target=first)
        assert first_started.wait(timeout=1)
        registry.submit("c2", "workspace-2", turn_id="t2", run_id="r2", target=second)
        assert not second_started.wait(timeout=0.1)
        release_first.set()
        assert second_finished.wait(timeout=1)
        registry.shutdown(timeout=2)


class TestArtifactsAndCommandProbe:
    @staticmethod
    def _command_call() -> PreparedCall:
        spec = ToolSpec(
            name="run_command",
            description="test",
            schema={},
            effect=ToolEffect.EXECUTE,
            validator=lambda value: value,
            handler=lambda _value: {},
        )
        return PreparedCall("call", "run_command", {}, "sig", spec=spec)

    @staticmethod
    def _write_call(path: str) -> PreparedCall:
        spec = ToolSpec(
            name="write_file",
            description="test",
            schema={},
            effect=ToolEffect.WRITE,
            validator=lambda value: value,
            handler=lambda _value: {},
        )
        return PreparedCall(
            "write-call", "write_file", {"path": path}, "write-sig", spec=spec
        )

    @staticmethod
    def _repo_turn(repo, workspace):
        conv = repo.create_conversation(
            workspace_path=str(workspace),
            workspace_key=str(workspace),
            profile_id=None,
            title="probe",
        )
        turn = repo.create_turn(conv.id, user_text="probe", run_id=uuid.uuid4().hex)
        return conv, turn

    def test_compressed_artifact_roundtrip_and_corruption_fail_closed(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        source = ("你好, artifact!\n" * 100).encode()
        blob_id = store.put(source)
        assert store.read(blob_id) == source
        store._path_for(blob_id).write_bytes(b"corrupt")
        with pytest.raises(ArtifactCorruptError):
            store.read(blob_id)

    def test_command_probe_uses_command_baseline_and_detects_side_effects(
        self, tmp_path
    ):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "dirty.txt").write_text("already dirty", encoding="utf-8")
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        conv, turn = self._repo_turn(repo, workspace)
        collector = ToolChangeCollector(
            workspace, ArtifactStore(tmp_path / "artifacts")
        )
        call = self._command_call()
        collector.before_execute(call)
        (workspace / "created.py").write_text("print('new')\n", encoding="utf-8")
        collector.after_execute(call, ToolOutcome("call", "run_command", True, data={}))
        change_set = collector.finalize(repo, conversation_id=conv.id, turn_id=turn.id)
        assert change_set["coverage"] == "complete"
        assert [item["relative_path"] for item in change_set["files"]] == ["created.py"]
        assert change_set["files"][0]["source"] == "command_detected"

    def test_command_probe_coalesces_unambiguous_rename(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "old.txt").write_text("same content\n", encoding="utf-8")
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        conv, turn = self._repo_turn(repo, workspace)
        collector = ToolChangeCollector(
            workspace, ArtifactStore(tmp_path / "artifacts")
        )
        call = self._command_call()
        collector.before_execute(call)
        (workspace / "old.txt").rename(workspace / "new.txt")
        collector.after_execute(call, ToolOutcome("call", "run_command", True, data={}))
        change_set = collector.finalize(repo, conversation_id=conv.id, turn_id=turn.id)
        assert change_set["file_count"] == 1
        assert change_set["files"][0]["change_type"] == "renamed"
        assert change_set["files"][0]["old_relative_path"] == "old.txt"
        assert change_set["files"][0]["relative_path"] == "new.txt"

    def test_command_probe_marks_git_head_change_incomplete(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        git_dir = workspace / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("a" * 40, encoding="utf-8")
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        conv, turn = self._repo_turn(repo, workspace)
        collector = ToolChangeCollector(
            workspace, ArtifactStore(tmp_path / "artifacts")
        )
        call = self._command_call()
        collector.before_execute(call)
        (workspace / "created.txt").write_text("created\n", encoding="utf-8")
        (git_dir / "HEAD").write_text("b" * 40, encoding="utf-8")
        collector.after_execute(call, ToolOutcome("call", "run_command", True, data={}))
        change_set = collector.finalize(repo, conversation_id=conv.id, turn_id=turn.id)
        assert change_set["coverage"] == "incomplete"
        assert [item["relative_path"] for item in change_set["files"]] == [
            "created.txt"
        ]

    def test_probe_budget_degrades_but_keeps_tool_confirmed_change(
        self, tmp_path, monkeypatch
    ):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "existing.txt").write_text("base\n", encoding="utf-8")
        repo = SQLiteConversationRepository(tmp_path / "state.db")
        repo.initialize()
        conv, turn = self._repo_turn(repo, workspace)
        collector = ToolChangeCollector(
            workspace, ArtifactStore(tmp_path / "artifacts")
        )
        monkeypatch.setattr(collector_module, "MAX_PROBE_FILES", 0)
        command = self._command_call()
        collector.before_execute(command)
        collector.after_execute(
            command, ToolOutcome("call", "run_command", True, data={})
        )
        write = self._write_call("confirmed.txt")
        collector.before_execute(write)
        (workspace / "confirmed.txt").write_text("confirmed\n", encoding="utf-8")
        collector.after_execute(
            write, ToolOutcome("write-call", "write_file", True, data={})
        )
        change_set = collector.finalize(repo, conversation_id=conv.id, turn_id=turn.id)
        assert change_set["coverage"] == "incomplete"
        assert change_set["file_count"] == 1
        assert change_set["files"][0]["relative_path"] == "confirmed.txt"
        assert change_set["files"][0]["source"] == "tool_confirmed"

    def test_shared_cas_blob_is_collected_only_after_last_conversation_delete(
        self, tmp_path
    ):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        service = make_service(tmp_path, FinalModel())
        blob = service._artifact_store.put_text("shared snapshot\n")
        try:
            conversations = []
            for index in range(2):
                conv = service.create_conversation(
                    workspace_path=str(workspace), profile_id=None, title=f"c{index}"
                )
                turn = service._repository.create_turn(
                    conv["id"], user_text="x", run_id=uuid.uuid4().hex
                )
                service._repository.save_change_set(
                    change_set_id=uuid.uuid4().hex,
                    conversation_id=conv["id"],
                    turn_id=turn.id,
                    status="final",
                    additions=1,
                    deletions=0,
                    file_count=1,
                    coverage="complete",
                    files=[
                        {
                            "relative_path": "shared.txt",
                            "change_type": "created",
                            "source": "tool_confirmed",
                            "after_blob_id": blob,
                            "after_sha": blob,
                            "after_byte_count": len("shared snapshot\n"),
                            "additions": 1,
                            "deletions": 0,
                        }
                    ],
                )
                conversations.append(service.get_conversation(conv["id"]))
            service.delete_conversation(
                conversations[0]["id"], expected_version=conversations[0]["version"]
            )
            assert service._artifact_store.exists(blob)
            service.delete_conversation(
                conversations[1]["id"], expected_version=conversations[1]["version"]
            )
            assert not service._artifact_store.exists(blob)
        finally:
            service.shutdown(timeout=5)

    def test_workspace_lock_rejects_second_conversation(
        self, tmp_path, workspace_factory
    ):
        model = BlockingModel()
        model.release = False
        service = make_service(tmp_path, model)
        try:
            ws = workspace_factory()
            a = service.create_conversation(workspace_path=str(ws), profile_id=None)
            b = service.create_conversation(workspace_path=str(ws), profile_id=None)
            ta = service.start_turn(a["id"], user_text="run A")
            # Give the worker a chance to take the lease.
            for _ in range(50):
                if service.runtime.workspace_owner(str(ws)) == a["id"]:
                    break
                time.sleep(0.02)
            with pytest.raises(ConversationServiceError) as exc:
                service.start_turn(b["id"], user_text="run B")
            assert exc.value.code == "workspace_busy"
            service.cancel_turn(a["id"], ta["id"])
        finally:
            model.release = True
            service.shutdown(timeout=5)

    def test_change_set_captures_created_file(self, tmp_path, workspace_factory):
        model = WriteThenFinalModel()
        service = make_service(tmp_path, model)
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            turn = service.start_turn(conv["id"], user_text="create new.txt")
            wait_turn(service, conv["id"], turn["id"])
            change_set = service.get_change_set(conv["id"], turn["id"])
            assert change_set is not None
            assert change_set["file_count"] == 1
            assert change_set["files"][0]["relative_path"] == "new.txt"
            assert change_set["files"][0]["change_type"] == "created"
            assert change_set["files"][0]["source"] == "tool_confirmed"
        finally:
            service.shutdown(timeout=5)

    def test_start_archived_conversation_rejected(self, tmp_path, workspace_factory):
        model = FinalModel()
        service = make_service(tmp_path, model)
        try:
            ws = workspace_factory()
            conv = service.create_conversation(workspace_path=str(ws), profile_id=None)
            service.archive_conversation(conv["id"], expected_version=1)
            with pytest.raises(ConversationServiceError) as exc:
                service.start_turn(conv["id"], user_text="should fail")
            assert exc.value.code == "conversation_archived"
        finally:
            service.shutdown(timeout=5)
