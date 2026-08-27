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
from .schemas import (
    BootstrapDTO,
    CapabilitiesDTO,
    CredentialInfoDTO,
    CredentialSetRequest,
    ErrorDetail,
    ErrorResponse,
    HealthDTO,
    ProfileDTO,
    ProfileInput,
    ProviderPresetDTO,
    RunSnapshotDTO,
    RunStartRequest,
    UIPreferencesDTO,
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
) -> FastAPI:
    token = session_token or new_session_token()
    resolved_version = version or __version__
    catalog = ProviderCatalog()

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
        snap = controller.snapshot()
        return HealthDTO(
            status="ok",
            version=resolved_version,
            run_state=snap.state,
            run_id=snap.run_id or None,
        )

    @app.get("/api/bootstrap", response_model=BootstrapDTO)
    async def bootstrap() -> BootstrapDTO:
        snap = controller.snapshot()
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

    # ------------------------------------------------------------ runs

    @app.post("/api/runs", response_model=RunSnapshotDTO)
    async def start_run(body: RunStartRequest) -> RunSnapshotDTO:
        return controller.start(body)

    @app.get("/api/runs/{run_id}", response_model=RunSnapshotDTO)
    async def run_snapshot(run_id: str) -> RunSnapshotDTO:
        snap = controller.snapshot()
        if snap.run_id != run_id or snap.state == "idle":
            raise RunControllerError("run_not_found", f"运行不存在：{run_id}")
        return snap

    @app.post("/api/runs/{run_id}/cancel", response_model=RunSnapshotDTO)
    async def cancel_run(run_id: str) -> RunSnapshotDTO:
        snap = controller.snapshot()
        if snap.run_id != run_id or snap.state == "idle":
            raise RunControllerError("run_not_found", f"运行不存在：{run_id}")
        return controller.cancel()

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str, request: Request, last_event_id: Optional[int] = None
    ):
        snap = controller.snapshot()
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
            _event_stream(controller, run_id, last_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
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
            wire_api="openai_chat_completions",
            credential_ref=body.credential_ref,
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
            wire_api="openai_chat_completions",
            credential_ref=body.credential_ref,
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


async def _event_stream(
    controller: RunController, run_id: str, last_id: Optional[int]
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


def _hello_payload(controller: RunController, last_id: Optional[int]) -> Dict[str, Any]:
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
