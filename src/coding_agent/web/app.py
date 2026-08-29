"""FastAPI local app server: JSON API, SSE and static GUI hosting.

The whole app is one loopback process. FastAPI/Starlette provide routing,
dependency parsing and the ASGI interface; RunController owns all run
semantics; DTOs in :mod:`schemas` are the only types that cross the API
boundary. No AgentLoop semantics are duplicated here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from .. import __version__
from ..config import DEFAULT_CHAR_BUDGET, DEFAULT_MAX_STEPS
from ..conversations.service import ConversationService, ConversationServiceError
from ..credentials import CredentialError
from ..errors import ConfigError
from ..provider_config import (
    WIRE_APIS,
    ProfileError,
    ProfileStore,
    ProviderCatalog,
    ProviderProfile,
    validate_profile,
)
from ..storage import StorageError
from .controller import RunController, RunControllerError
from .legacy_adapter import ConversationRunAdapter
from .picker import PickerUnavailableError, PickFolder, pick_folder
from .redaction import redact_public_payload
from .schemas import (
    BootstrapDTO,
    CapabilitiesDTO,
    ChangeSetDTO,
    ConversationCreateRequest,
    ConversationDTO,
    ConversationPageDTO,
    ConversationRenameRequest,
    ConversationVersionRequest,
    CredentialInfoDTO,
    CredentialSetRequest,
    ErrorDetail,
    ErrorResponse,
    FileChangeDTO,
    HealthDTO,
    InboxEditRequest,
    InboxEnqueueRequest,
    InboxOrderRequest,
    InboxSnapshotDTO,
    InboxVersionRequest,
    MemoryCreateRequest,
    MemoryDTO,
    MemoryEditRequest,
    MemoryPageDTO,
    MemoryResetRequest,
    MemorySettingsDTO,
    MemorySettingsRequest,
    MemoryUsageDTO,
    MemoryVersionRequest,
    PreviewDTO,
    ProfileDTO,
    ProfileInput,
    ProviderPresetDTO,
    RunSnapshotDTO,
    RunStartRequest,
    StreamSnapshotDTO,
    ToolEventDTO,
    TurnCreateRequest,
    TurnDTO,
    TurnPageDTO,
    UIPreferencesDTO,
    WorkspacePickResponse,
    WorkspaceValidateRequest,
    WorkspaceValidateResponse,
)
from .security import install_security, new_session_token

logger = logging.getLogger("coding_agent.web")

_ID_GENERATOR_RE = re.compile(r"[^a-z0-9]+")


def error_response(
    code: str, message: str, field: Optional[str] = None, status: int = 400
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(
            error=ErrorDetail(code=code, message=message, field=field)
        ).model_dump(),
    )


def profile_dto(profile: ProviderProfile, controller: RunController) -> ProfileDTO:
    info = (
        controller.credentials.info(profile.credential_ref)
        if profile.credential_ref
        else None
    )
    return ProfileDTO(
        id=profile.id,
        provider_id=profile.provider_id,
        display_name=profile.display_name,
        wire_api=profile.wire_api,
        base_url=profile.base_url,
        model=profile.model,
        credential_ref=profile.credential_ref,
        reasoning_mode=profile.reasoning_mode,
        reasoning_effort=profile.reasoning_effort,
        show_reasoning=profile.show_reasoning,
        credential=CredentialInfoDTO(
            configured=bool(info and info.configured),
            source=info.source if info else None,
            writable=info.writable if info else True,
        ),
    )


def generate_profile_id(
    store: ProfileStore, provider_id: str, display_name: str
) -> str:
    slug = _ID_GENERATOR_RE.sub("-", display_name.lower()).strip("-")[:40] or "profile"
    candidate = f"{provider_id}-{slug}"
    existing = set(store.load().profiles)
    if candidate not in existing:
        return candidate
    index = 2
    while f"{candidate}-{index}" in existing:
        index += 1
    return f"{candidate}-{index}"


def create_app(
    *,
    controller: RunController,
    static_dir: Path,
    session_token: Optional[str] = None,
    version: Optional[str] = None,
    folder_picker: Optional[PickFolder] = None,
    conversation_service: Optional[ConversationService] = None,
) -> FastAPI:
    token = session_token or new_session_token()
    resolved_version = version or __version__
    catalog = ProviderCatalog()
    picker = folder_picker or pick_folder
    # During the compatibility window, the old single-run endpoints are a
    # projection of the same persisted ConversationService worker/history.
    # Tests or embedders that deliberately omit the service retain the
    # standalone task_001 controller contract.
    run_api = (
        ConversationRunAdapter(
            conversation_service,
            max_events=getattr(controller, "_max_events", 2_000),
            max_event_chars=getattr(controller, "_max_event_chars", 1_000_000),
        )
        if conversation_service is not None
        else controller
    )

    app = FastAPI(
        title="Coding Agent Local GUI",
        version=resolved_version,
        # Disable the default Swagger/ReDoc UI: their HTML loads scripts from
        # cdn.jsdelivr.net, which violates the no-external-resources CSP.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_security(app, token)

    # ------------------------------------------------------------ exceptions

    @app.exception_handler(RunControllerError)
    async def handle_controller_error(_request: Request, exc: RunControllerError):
        if exc.code == "run_not_found":
            status = 404
        elif exc.code == "run_already_active":
            status = 409
        else:
            status = 400
        return error_response(exc.code, str(exc), exc.field, status=status)

    @app.exception_handler(ConversationServiceError)
    async def handle_conversation_error(
        _request: Request, exc: ConversationServiceError
    ):
        status = (
            404
            if exc.code
            in (
                "conversation_not_found",
                "turn_not_found",
                "artifact_not_found",
                "memory_not_found",
            )
            else 409
            if exc.code
            in (
                "version_conflict",
                "idempotency_conflict",
                "conversation_busy",
                "workspace_busy",
                "conversation_archived",
            )
            else 400
        )
        return error_response(exc.code, str(exc), exc.field, status=status)

    @app.exception_handler(ProfileError)
    async def handle_profile_error(_request: Request, exc: ProfileError):
        return error_response(
            getattr(exc, "code", "invalid_config"),
            str(exc),
            getattr(exc, "field", None),
        )

    @app.exception_handler(CredentialError)
    async def handle_credential_error(_request: Request, exc: CredentialError):
        status = 409 if exc.code == "credential_env_readonly" else 400
        return error_response(exc.code, str(exc), exc.field, status=status)

    @app.exception_handler(ConfigError)
    async def handle_config_error(_request: Request, exc: ConfigError):
        return error_response("invalid_config", str(exc))

    @app.exception_handler(StorageError)
    async def handle_storage_error(_request: Request, exc: StorageError):
        logger.warning("storage error: %s", type(exc).__name__)
        return error_response(
            "config_io_error", "配置存储读写失败，请检查用户目录权限", status=500
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError):
        field = None
        if exc.errors():
            loc = exc.errors()[0].get("loc") or []
            field = str(loc[-1]) if loc else None
        return error_response(
            "invalid_request", "请求参数不合法", field=field, status=422
        )

    @app.exception_handler(Exception)
    async def handle_generic_error(_request: Request, exc: Exception):
        logger.error("unhandled error: %s", type(exc).__name__)
        return error_response("internal_error", "服务器内部错误", status=500)

    # ------------------------------------------------------------ health

    @app.get("/api/health", response_model=HealthDTO)
    async def health() -> HealthDTO:
        snap = run_api.snapshot()
        return HealthDTO(
            status="ok",
            version=resolved_version,
            run_state=snap.state,
            run_id=snap.run_id or None,
        )

    @app.get("/api/bootstrap", response_model=BootstrapDTO)
    async def bootstrap() -> BootstrapDTO:
        snap = run_api.snapshot()
        profiles = [
            profile_dto(p, controller) for p in controller.profile_store.list_profiles()
        ]
        active = controller.profile_store.load().active_profile
        return BootstrapDTO(
            server_version=resolved_version,
            session_token=token,
            state=snap.state,
            run=snap if snap.run_id else None,
            profiles=profiles,
            active_profile_id=active,
            provider_presets=[
                ProviderPresetDTO(
                    provider_id=p.provider_id,
                    display_name=p.display_name,
                    default_base_url=p.default_base_url,
                    default_model=p.default_model,
                    note=p.note,
                )
                for p in catalog.presets()
            ],
            capabilities=CapabilitiesDTO(
                wire_apis=list(WIRE_APIS),
                max_steps=DEFAULT_MAX_STEPS,
                char_budget=DEFAULT_CHAR_BUDGET,
            ),
            ui=UIPreferencesDTO(locale=None, theme=None),
        )

    # ------------------------------------------------------------ workspace

    @app.post("/api/workspace/validate", response_model=WorkspaceValidateResponse)
    async def workspace_validate(
        body: WorkspaceValidateRequest,
    ) -> WorkspaceValidateResponse:
        try:
            path = RunController._validate_workspace(body.path)
        except RunControllerError as exc:
            return WorkspaceValidateResponse(
                valid=False,
                error=ErrorDetail(code=exc.code, message=str(exc), field=exc.field),
            )
        return WorkspaceValidateResponse(valid=True, resolved_path=str(path))

    @app.post("/api/workspace/pick", response_model=WorkspacePickResponse)
    def workspace_pick() -> WorkspacePickResponse:
        """Open the OS-native folder dialog on the server machine.

        A sync endpoint: Starlette runs it in a threadpool, so the modal
        dialog never blocks the HTTP event loop (SSE keeps streaming).
        """
        try:
            selected = picker()
        except PickerUnavailableError as exc:
            logger.warning("folder picker unavailable: %s", type(exc).__name__)
            return WorkspacePickResponse(
                cancelled=False,
                path=None,
                error=ErrorDetail(code="picker_unavailable", message=str(exc)),
            )
        if not selected:
            return WorkspacePickResponse(cancelled=True, path=None)
        return WorkspacePickResponse(cancelled=False, path=str(selected))

    # ------------------------------------------------------------ runs

    @app.post("/api/runs", response_model=RunSnapshotDTO)
    async def start_run(body: RunStartRequest) -> RunSnapshotDTO:
        return run_api.start(body)

    @app.get("/api/runs/{run_id}", response_model=RunSnapshotDTO)
    async def run_snapshot(run_id: str) -> RunSnapshotDTO:
        snap = run_api.snapshot()
        if snap.run_id != run_id or snap.state == "idle":
            raise RunControllerError("run_not_found", f"运行不存在：{run_id}")
        return snap

    @app.post("/api/runs/{run_id}/cancel", response_model=RunSnapshotDTO)
    async def cancel_run(run_id: str) -> RunSnapshotDTO:
        snap = run_api.snapshot()
        if snap.run_id != run_id or snap.state == "idle":
            raise RunControllerError("run_not_found", f"运行不存在：{run_id}")
        return run_api.cancel()

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str, request: Request, last_event_id: Optional[int] = None
    ):
        snap = run_api.snapshot()
        if snap.run_id != run_id or snap.state == "idle":
            raise RunControllerError("run_not_found", f"运行不存在：{run_id}")
        header_id = request.headers.get("last-event-id")
        last_id = last_event_id
        if last_id is None and header_id is not None:
            try:
                last_id = int(header_id)
            except ValueError:
                last_id = None
        return StreamingResponse(
            _event_stream(run_api, run_id, last_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------ conversations

    if conversation_service is not None:

        def _conv_dto(data: dict) -> ConversationDTO:
            # The service list payload does not include latest_turn; leave it
            # to the detail endpoint to enrich.
            return ConversationDTO(**data)

        def _turn_dto(data: dict) -> TurnDTO:
            return TurnDTO(**data)

        def _change_dto(data: dict) -> FileChangeDTO:
            return FileChangeDTO(
                id=data["id"],
                relative_path=data["relative_path"],
                old_relative_path=data.get("old_relative_path"),
                change_type=data["change_type"],
                source=data["source"],
                before_blob_id=data.get("before_blob_id"),
                after_blob_id=data.get("after_blob_id"),
                before_sha=data.get("before_sha"),
                after_sha=data.get("after_sha"),
                additions=int(data.get("additions", 0)),
                deletions=int(data.get("deletions", 0)),
                binary=bool(data.get("binary")),
                preview_status=data.get("preview_status", "available"),
                warnings=list(data.get("warnings", [])),
            )

        @app.post(
            "/api/conversations",
            response_model=ConversationDTO,
            status_code=201,
        )
        async def create_conversation(
            body: ConversationCreateRequest,
        ) -> ConversationDTO:
            data = conversation_service.create_conversation(
                workspace_path=body.workspace,
                profile_id=body.profile_id,
                title=body.title,
            )
            return _conv_dto(data)

        @app.get("/api/conversations", response_model=ConversationPageDTO)
        async def list_conversations(
            archived: Optional[bool] = False,
            query: Optional[str] = None,
            limit: int = 50,
            cursor: Optional[str] = None,
        ) -> ConversationPageDTO:
            page = conversation_service.list_conversations(
                archived=archived, query=query, limit=limit, cursor=cursor
            )
            return ConversationPageDTO(
                items=[_conv_dto(item) for item in page["items"]],
                next_cursor=page["next_cursor"],
            )

        @app.get("/api/conversations/{conversation_id}", response_model=ConversationDTO)
        async def get_conversation(conversation_id: str) -> ConversationDTO:
            data = conversation_service.get_conversation(conversation_id)
            active = conversation_service._repository.get_active_turn(conversation_id)
            if active is not None:
                data["latest_turn"] = conversation_service._turn_to_dict(active)
            return _conv_dto(data)

        @app.patch(
            "/api/conversations/{conversation_id}",
            response_model=ConversationDTO,
        )
        async def rename_conversation(
            conversation_id: str, body: ConversationRenameRequest
        ) -> ConversationDTO:
            return _conv_dto(
                conversation_service.rename_conversation(
                    conversation_id,
                    title=body.title,
                    expected_version=body.expected_version,
                )
            )

        @app.post(
            "/api/conversations/{conversation_id}/archive",
            response_model=ConversationDTO,
        )
        async def archive_conversation(
            conversation_id: str, body: ConversationVersionRequest
        ) -> ConversationDTO:
            return _conv_dto(
                conversation_service.archive_conversation(
                    conversation_id, expected_version=body.expected_version
                )
            )

        @app.post(
            "/api/conversations/{conversation_id}/unarchive",
            response_model=ConversationDTO,
        )
        async def unarchive_conversation(
            conversation_id: str, body: ConversationVersionRequest
        ) -> ConversationDTO:
            return _conv_dto(
                conversation_service.unarchive_conversation(
                    conversation_id, expected_version=body.expected_version
                )
            )

        @app.delete("/api/conversations/{conversation_id}", status_code=204)
        async def delete_conversation(
            conversation_id: str, body: ConversationVersionRequest
        ) -> None:
            if not body.confirm:
                raise ConversationServiceError(
                    "confirmation_required", "删除会话需要显式确认", field="confirm"
                )
            conversation_service.delete_conversation(
                conversation_id, expected_version=body.expected_version
            )

        @app.get(
            "/api/conversations/{conversation_id}/turns",
            response_model=TurnPageDTO,
        )
        async def list_turns(
            conversation_id: str, limit: int = 50, cursor: Optional[str] = None
        ) -> TurnPageDTO:
            page = conversation_service.list_turns(
                conversation_id, limit=limit, cursor=cursor
            )
            return TurnPageDTO(
                items=[_turn_dto(item) for item in page["items"]],
                next_cursor=page["next_cursor"],
            )

        @app.post(
            "/api/conversations/{conversation_id}/turns",
            response_model=TurnDTO,
            status_code=202,
        )
        async def start_turn(conversation_id: str, body: TurnCreateRequest) -> TurnDTO:
            return _turn_dto(
                conversation_service.start_turn(
                    conversation_id,
                    user_text=body.content,
                    idempotency_key=body.idempotency_key,
                    profile_id=body.profile_id,
                    reasoning_effort=body.reasoning_effort,
                )
            )

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}",
            response_model=TurnDTO,
        )
        async def get_turn(conversation_id: str, turn_id: str) -> TurnDTO:
            return _turn_dto(conversation_service.get_turn(conversation_id, turn_id))

        @app.post(
            "/api/conversations/{conversation_id}/turns/{turn_id}/cancel",
            response_model=TurnDTO,
        )
        async def cancel_turn(conversation_id: str, turn_id: str) -> TurnDTO:
            return _turn_dto(conversation_service.cancel_turn(conversation_id, turn_id))

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}/events",
            response_model=list[ToolEventDTO],
        )
        async def turn_events(
            conversation_id: str,
            turn_id: str,
            after_seq: int = 0,
            limit: int = 500,
        ) -> list[ToolEventDTO]:
            events = conversation_service.get_events(
                conversation_id, turn_id, after_seq=after_seq, limit=limit
            )
            result = []
            for event in events:
                payload = dict(event["payload"])
                if event.get("target") is not None:
                    payload["target"] = event["target"]
                payload = redact_public_payload(event["kind"], payload)
                target = payload.pop("target", None)
                result.append(
                    ToolEventDTO(
                        id=event["id"],
                        kind=event["kind"],
                        step=event["step"],
                        phase=event["phase"],
                        target=target if isinstance(target, str) else None,
                        payload=payload,
                    )
                )
            return result

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}/sse",
            include_in_schema=False,
        )
        async def turn_event_sse(
            conversation_id: str,
            turn_id: str,
            after_seq: int = 0,
        ):
            return StreamingResponse(
                _conversation_event_stream(
                    conversation_service, conversation_id, turn_id, after_seq
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}/stream",
            response_model=StreamSnapshotDTO,
        )
        async def stream_snapshot(
            conversation_id: str, turn_id: str
        ) -> StreamSnapshotDTO:
            return StreamSnapshotDTO(
                checkpoints=conversation_service.get_stream_snapshot(
                    conversation_id, turn_id
                )
            )

        @app.get(
            "/api/conversations/{conversation_id}/inbox/sse",
            include_in_schema=False,
        )
        async def inbox_sse(conversation_id: str):
            return StreamingResponse(
                _inbox_event_stream(conversation_service, conversation_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        @app.get(
            "/api/conversations/{conversation_id}/inbox",
            response_model=InboxSnapshotDTO,
        )
        async def get_inbox(conversation_id: str) -> InboxSnapshotDTO:
            return InboxSnapshotDTO(**conversation_service.get_inbox(conversation_id))

        @app.post(
            "/api/conversations/{conversation_id}/inbox",
            response_model=InboxSnapshotDTO,
        )
        async def enqueue_inbox(
            conversation_id: str, body: InboxEnqueueRequest
        ) -> InboxSnapshotDTO:
            return InboxSnapshotDTO(
                **conversation_service.enqueue_inbox(
                    conversation_id,
                    content=body.content,
                    mode=body.mode,
                    idempotency_key=body.idempotency_key,
                    profile_id=body.profile_id,
                    reasoning_effort=body.reasoning_effort,
                )
            )

        @app.patch(
            "/api/conversations/{conversation_id}/inbox/{item_id}",
            response_model=InboxSnapshotDTO,
        )
        async def edit_inbox(
            conversation_id: str, item_id: str, body: InboxEditRequest
        ) -> InboxSnapshotDTO:
            return InboxSnapshotDTO(
                **conversation_service.edit_inbox(
                    conversation_id,
                    item_id,
                    content=body.content,
                    mode=body.mode,
                    expected_version=body.expected_version,
                )
            )

        @app.delete(
            "/api/conversations/{conversation_id}/inbox/{item_id}",
            response_model=InboxSnapshotDTO,
        )
        async def remove_inbox(
            conversation_id: str, item_id: str, body: InboxVersionRequest
        ) -> InboxSnapshotDTO:
            return InboxSnapshotDTO(
                **conversation_service.remove_inbox(
                    conversation_id,
                    item_id,
                    expected_version=body.expected_version,
                )
            )

        @app.put(
            "/api/conversations/{conversation_id}/inbox/order",
            response_model=InboxSnapshotDTO,
        )
        async def reorder_inbox(
            conversation_id: str, body: InboxOrderRequest
        ) -> InboxSnapshotDTO:
            return InboxSnapshotDTO(
                **conversation_service.reorder_inbox(
                    conversation_id,
                    ordered_ids=body.ordered_ids,
                    expected_queue_version=body.expected_queue_version,
                )
            )

        @app.post(
            "/api/conversations/{conversation_id}/inbox/{item_id}/steer",
            response_model=InboxSnapshotDTO,
        )
        async def steer_inbox(
            conversation_id: str, item_id: str, body: InboxVersionRequest
        ) -> InboxSnapshotDTO:
            return InboxSnapshotDTO(
                **conversation_service.steer_inbox(
                    conversation_id,
                    item_id,
                    expected_version=body.expected_version,
                )
            )

        @app.post(
            "/api/conversations/{conversation_id}/inbox/{item_id}/retry",
            response_model=InboxSnapshotDTO,
        )
        async def retry_inbox(
            conversation_id: str, item_id: str, body: InboxVersionRequest
        ) -> InboxSnapshotDTO:
            return InboxSnapshotDTO(
                **conversation_service.retry_inbox(
                    conversation_id,
                    item_id,
                    expected_version=body.expected_version,
                )
            )

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}/changes",
            response_model=ChangeSetDTO,
        )
        async def turn_changes(conversation_id: str, turn_id: str) -> ChangeSetDTO:
            data = conversation_service.get_change_set(conversation_id, turn_id)
            if data is None:
                raise ConversationServiceError(
                    "change_set_not_found", "该 turn 没有变更摘要"
                )
            return ChangeSetDTO(
                id=data["id"],
                conversation_id=data["conversation_id"],
                turn_id=data["turn_id"],
                status=data["status"],
                additions=int(data["additions"]),
                deletions=int(data["deletions"]),
                file_count=int(data["file_count"]),
                coverage=data["coverage"],
                finalized_at=data.get("finalized_at"),
                files=[_change_dto(item) for item in data.get("files", [])],
            )

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}/changes/{change_id}",
            response_model=FileChangeDTO,
        )
        async def get_file_change(
            conversation_id: str, turn_id: str, change_id: str
        ) -> FileChangeDTO:
            data = conversation_service.get_file_change(
                conversation_id, turn_id, change_id
            )
            if data is None:
                raise ConversationServiceError("artifact_not_found", "文件变更不存在")
            return _change_dto(data)

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}/changes/{change_id}/preview",
            response_model=PreviewDTO,
        )
        async def get_preview(
            conversation_id: str,
            turn_id: str,
            change_id: str,
            mode: str = "diff",
        ) -> PreviewDTO:
            return PreviewDTO(
                **conversation_service.get_file_preview(
                    conversation_id, turn_id, change_id, mode=mode
                )
            )

        @app.get("/api/memories", response_model=MemoryPageDTO)
        async def list_memories(
            scope_type: Optional[str] = None,
            scope_key: Optional[str] = None,
            status: Optional[str] = None,
            query: Optional[str] = None,
            limit: int = 100,
            cursor: Optional[str] = None,
        ) -> MemoryPageDTO:
            page = conversation_service.list_memories(
                scope_type=scope_type,
                scope_key=scope_key,
                status=status,
                query=query,
                limit=limit,
                cursor=cursor,
            )
            return MemoryPageDTO(
                items=[MemoryDTO(**item) for item in page["items"]],
                next_cursor=page.get("next_cursor"),
            )

        @app.post("/api/memories", response_model=MemoryDTO, status_code=201)
        async def create_memory(body: MemoryCreateRequest) -> MemoryDTO:
            return MemoryDTO(
                **conversation_service.create_memory(
                    scope_type=body.scope_type,
                    scope_key=body.scope_key,
                    kind=body.kind,
                    content=body.content,
                    title=body.title,
                    source_conversation_id=body.source_conversation_id,
                    source_turn_id=body.source_turn_id,
                    source_excerpt=body.source_excerpt,
                    idempotency_key=body.idempotency_key,
                )
            )

        @app.post("/api/memories/reset")
        async def reset_memories(body: MemoryResetRequest) -> Dict[str, Any]:
            if not body.confirm:
                raise ConversationServiceError(
                    "confirmation_required", "清空记忆需要显式确认", field="confirm"
                )
            return conversation_service.reset_memories(
                scope_type=body.scope_type,
                scope_key=body.scope_key,
                idempotency_key=body.idempotency_key,
                expected_scope_version=body.expected_scope_version,
            )

        @app.get("/api/memories/settings", response_model=MemorySettingsDTO)
        async def get_memory_settings(
            scope_type: Optional[str] = None,
            scope_key: Optional[str] = None,
        ) -> MemorySettingsDTO:
            data = conversation_service.memory_settings(
                scope_type=scope_type, scope_key=scope_key
            )
            return MemorySettingsDTO(
                enabled=bool(data["enabled"]),
                candidate_enabled=bool(data.get("candidate_enabled", False)),
                scope_type=scope_type,
                scope_key=scope_key,
                scope_version=int(data.get("scope_version", 0)),
            )

        @app.post("/api/memories/settings", response_model=MemorySettingsDTO)
        async def set_memory_settings(
            body: MemorySettingsRequest,
        ) -> MemorySettingsDTO:
            data = conversation_service.memory_settings(
                scope_type=body.scope_type,
                scope_key=body.scope_key,
                enabled=body.enabled,
                candidate_enabled=body.candidate_enabled,
            )
            return MemorySettingsDTO(
                enabled=bool(data.get("enabled", True)),
                candidate_enabled=bool(data.get("candidate_enabled", False)),
                scope_type=data.get("scope_type", body.scope_type),
                scope_key=data.get("scope_key", body.scope_key),
                scope_version=int(data.get("scope_version", 0)),
            )

        @app.get(
            "/api/conversations/{conversation_id}/turns/{turn_id}/memory-usage",
            response_model=list[MemoryUsageDTO],
        )
        async def turn_memory_usage(
            conversation_id: str, turn_id: str
        ) -> list[MemoryUsageDTO]:
            conversation_service._require_turn(conversation_id, turn_id)
            return [
                MemoryUsageDTO(**item)
                for item in conversation_service.turn_memory_usage(turn_id)
            ]

        @app.get(
            "/api/turns/{turn_id}/memory-usage",
            response_model=list[MemoryUsageDTO],
        )
        async def turn_memory_usage_flat(turn_id: str) -> list[MemoryUsageDTO]:
            return [
                MemoryUsageDTO(**item)
                for item in conversation_service.turn_memory_usage(turn_id)
            ]

        @app.get("/api/memories/{memory_id}", response_model=MemoryDTO)
        async def get_memory(memory_id: str) -> MemoryDTO:
            return MemoryDTO(**conversation_service.get_memory(memory_id))

        @app.patch("/api/memories/{memory_id}", response_model=MemoryDTO)
        async def edit_memory(memory_id: str, body: MemoryEditRequest) -> MemoryDTO:
            return MemoryDTO(
                **conversation_service.edit_memory(
                    memory_id,
                    content=body.content,
                    kind=body.kind,
                    title=body.title,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key,
                )
            )

        @app.delete("/api/memories/{memory_id}", status_code=204)
        async def delete_memory(memory_id: str, body: MemoryVersionRequest) -> None:
            conversation_service.delete_memory(
                memory_id,
                expected_version=body.expected_version,
                idempotency_key=body.idempotency_key,
            )

        @app.post("/api/memories/{memory_id}/approve", response_model=MemoryDTO)
        async def approve_memory(
            memory_id: str, body: MemoryVersionRequest
        ) -> MemoryDTO:
            return MemoryDTO(
                **conversation_service.approve_memory(
                    memory_id,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key,
                )
            )

        @app.post("/api/memories/{memory_id}/reject", response_model=MemoryDTO)
        async def reject_memory(
            memory_id: str, body: MemoryVersionRequest
        ) -> MemoryDTO:
            return MemoryDTO(
                **conversation_service.reject_memory(
                    memory_id,
                    expected_version=body.expected_version,
                    idempotency_key=body.idempotency_key,
                )
            )

    # ------------------------------------------------------------ profiles

    @app.get("/api/profiles", response_model=list[ProfileDTO])
    async def list_profiles() -> list[ProfileDTO]:
        items = controller.profile_store.list_profiles()
        return [profile_dto(p, controller) for p in items]

    @app.post("/api/profiles", response_model=ProfileDTO, status_code=201)
    async def create_profile(body: ProfileInput) -> ProfileDTO:
        profile_id = generate_profile_id(
            controller.profile_store, body.provider_id, body.display_name
        )
        profile = validate_profile(
            profile_id=profile_id,
            provider_id=body.provider_id,
            display_name=body.display_name,
            base_url=body.base_url,
            model=body.model,
            credential_ref=body.credential_ref,
            wire_api=body.wire_api or "openai_chat_completions",
            reasoning_mode=body.reasoning_mode or "auto",
            reasoning_effort=body.reasoning_effort,
            show_reasoning=bool(body.show_reasoning),
        )
        controller.profile_store.create(profile)
        return profile_dto(profile, controller)

    @app.put("/api/profiles/{profile_id}", response_model=ProfileDTO)
    async def update_profile(profile_id: str, body: ProfileInput) -> ProfileDTO:
        existing = controller.profile_store.get(profile_id)
        if existing is None:
            raise ProfileError(f"profile 不存在：{profile_id}", field="id")
        profile = validate_profile(
            profile_id=profile_id,
            provider_id=body.provider_id,
            display_name=body.display_name,
            base_url=body.base_url,
            model=body.model,
            credential_ref=body.credential_ref,
            wire_api=body.wire_api or "openai_chat_completions",
            reasoning_mode=body.reasoning_mode or "auto",
            reasoning_effort=body.reasoning_effort,
            show_reasoning=bool(body.show_reasoning),
        )
        controller.profile_store.update(profile_id, profile)
        return profile_dto(profile, controller)

    @app.delete("/api/profiles/{profile_id}", status_code=204)
    async def delete_profile(profile_id: str) -> None:
        controller.profile_store.delete(profile_id)

    @app.post("/api/profiles/{profile_id}/activate", response_model=ProfileDTO)
    async def activate_profile(profile_id: str) -> ProfileDTO:
        profile = controller.profile_store.activate(profile_id)
        return profile_dto(profile, controller)

    @app.get("/api/profiles/{profile_id}/credential", response_model=CredentialInfoDTO)
    async def credential_info(profile_id: str) -> CredentialInfoDTO:
        profile = _require_profile(controller, profile_id)
        if not profile.credential_ref:
            return CredentialInfoDTO(configured=False, source=None, writable=True)
        info = controller.credentials.info(profile.credential_ref)
        return CredentialInfoDTO(
            configured=info.configured, source=info.source, writable=info.writable
        )

    @app.put("/api/profiles/{profile_id}/credential", response_model=CredentialInfoDTO)
    async def credential_set(
        profile_id: str, body: CredentialSetRequest
    ) -> CredentialInfoDTO:
        profile = _require_profile(controller, profile_id)
        if not profile.credential_ref:
            raise ProfileError(
                "该 profile 未配置 credential_ref，无法写入凭据", field="credential_ref"
            )
        info = controller.credentials.set(profile.credential_ref, body.secret)
        return CredentialInfoDTO(
            configured=info.configured, source=info.source, writable=info.writable
        )

    @app.delete(
        "/api/profiles/{profile_id}/credential", response_model=CredentialInfoDTO
    )
    async def credential_unset(profile_id: str) -> CredentialInfoDTO:
        profile = _require_profile(controller, profile_id)
        if not profile.credential_ref:
            raise ProfileError(
                "该 profile 未配置 credential_ref", field="credential_ref"
            )
        info = controller.credentials.unset(profile.credential_ref)
        return CredentialInfoDTO(
            configured=info.configured, source=info.source, writable=info.writable
        )

    # ------------------------------------------------------------ static

    static_dir_resolved = Path(static_dir)

    @app.get("/", include_in_schema=False)
    async def index():
        index_path = static_dir_resolved / "index.html"
        if not index_path.is_file():
            return PlainTextResponse(
                "Coding Agent GUI 静态资源缺失：请先运行 npm ci && npm run build；"
                "若使用打包安装请检查安装完整性。",
                status_code=503,
            )
        return FileResponse(index_path, media_type="text/html")

    @app.get("/assets/{path:path}", include_in_schema=False)
    async def assets(path: str):
        if ".." in path or not path:
            return PlainTextResponse("not found", status_code=404)
        target = (static_dir_resolved / "assets" / path).resolve()
        try:
            target.relative_to((static_dir_resolved / "assets").resolve())
        except ValueError:
            return PlainTextResponse("not found", status_code=404)
        if not target.is_file():
            return PlainTextResponse("not found", status_code=404)
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media_type)

    return app


def _require_profile(controller: RunController, profile_id: str) -> ProviderProfile:
    profile = controller.profile_store.get(profile_id)
    if profile is None:
        raise ProfileError(f"profile 不存在：{profile_id}", field="id")
    return profile


def _tool_event_from_stored(event: Dict[str, Any]) -> ToolEventDTO:
    payload = dict(event.get("payload") or {})
    target = event.get("target")
    if target is not None:
        payload["target"] = target
    payload = redact_public_payload(str(event.get("kind") or ""), payload)
    target = payload.pop("target", None)
    return ToolEventDTO(
        id=int(event.get("id", 0)),
        kind=str(event.get("kind", "")),
        step=int(event.get("step", 0)),
        phase=str(event.get("phase", "READY")),
        target=target if isinstance(target, str) else None,
        payload=payload,
    )


async def _conversation_event_stream(
    conversation_service: ConversationService,
    conversation_id: str,
    turn_id: str,
    last_id: int,
) -> AsyncIterator[str]:
    current_id = last_id
    yield _sse(
        "hello",
        {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "last_event_id": current_id,
        },
        event_id=None,
    )
    idle_ticks = 0
    while True:
        events = conversation_service.get_events(
            conversation_id, turn_id, after_seq=current_id, limit=500
        )
        for event in events:
            dto = _tool_event_from_stored(event)
            yield _sse(dto.kind, dto.model_dump(), event_id=str(dto.id))
            current_id = event["id"]
        turn = conversation_service.get_turn(conversation_id, turn_id)
        if not turn["active"]:
            yield _sse(
                "end",
                {"message": "turn finished"},
                event_id=str(current_id),
            )
            return
        idle_ticks += 1
        if idle_ticks >= 50:
            idle_ticks = 0
            yield ": ping\n\n"
        await asyncio.sleep(0.2)


async def _inbox_event_stream(
    conversation_service: ConversationService,
    conversation_id: str,
) -> AsyncIterator[str]:
    last_version: Optional[int] = None
    while True:
        snapshot = conversation_service.get_inbox(conversation_id)
        version = int(snapshot.get("queue_version", 1))
        if last_version is None or version != last_version:
            yield _sse(
                "inbox_snapshot" if last_version is None else "inbox_changed",
                snapshot,
                event_id=str(version),
            )
            last_version = version
        await asyncio.sleep(0.5)


async def _event_stream(
    controller: Any, run_id: str, last_id: Optional[int]
) -> AsyncIterator[str]:
    current_id = last_id
    yield _sse("hello", _hello_payload(controller, current_id), event_id=None)
    idle_ticks = 0
    while True:
        events, reset = controller.take_events(current_id)
        if reset:
            yield _sse(
                "reset",
                {"message": "event tail reset; refetch the snapshot"},
                event_id=str(current_id or 0),
            )
            for event in events:
                yield _sse(event.kind, event.model_dump(), event_id=str(event.id))
            current_id = events[-1].id if events else current_id
        else:
            for event in events:
                yield _sse(event.kind, event.model_dump(), event_id=str(event.id))
            if events:
                current_id = events[-1].id

        snap = controller.snapshot()
        if snap.run_id != run_id or snap.state == "idle":
            yield _sse(
                "end",
                {"message": "run no longer available"},
                event_id=str(current_id or 0),
            )
            return
        if snap.state == "terminal" and not events:
            yield _sse(
                "end", {"message": "run finished"}, event_id=str(current_id or 0)
            )
            return
        idle_ticks += 1
        if idle_ticks >= 50:  # heartbeat every ~10s
            idle_ticks = 0
            yield ": ping\n\n"
        await asyncio.sleep(0.2)


def _hello_payload(controller: Any, last_id: Optional[int]) -> Dict[str, Any]:
    snap = controller.snapshot()
    return {
        "run_id": snap.run_id,
        "state": snap.state,
        "last_event_id": snap.events[-1].id
        if snap.events
        else (snap.events_total or None),
        "client_last_event_id": last_id,
    }


def _sse(event: str, data: Dict[str, Any], *, event_id: Optional[str]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    lines = [f"event: {event}", f"data: {payload}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    return "\n".join(lines) + "\n\n"
