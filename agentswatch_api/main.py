import os
import threading
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from starlette.concurrency import run_in_threadpool

from agentswatch_automation import cleanup_stale_runner_workspaces

from . import __version__
from .agent_management import AgentManagementService
from .agent_operations import AgentOperationService
from .auth import (
    AuthenticationError,
    AuthorizationError,
    AuthService,
    PERMISSION_MATRIX,
)
from .automation_library import AutomationLibraryService, ManagedOperationCatalog
from .confirmation import CONFIRMATION_HEADER, ConfirmationError, ConfirmationService
from .database import Database
from .group_management import GroupManagementService
from .import_management import BulkImportService, MAX_FILE_BYTES
from .operation_events import record_system_event
from .observability_integration import (
    ObservabilityIntegrationClient,
    ObservabilityIntegrationError,
)
from .organization_management import OrganizationManagementService
from .queries import CoreAssetQueries
from .runtime_management import RuntimeManagementService, RuntimeOperationService
from .runtime_web_access import RuntimeWebAccessService
from .server_management import ConflictError, NotFoundError, OperationService, ServerManagementService
from .server_credentials import scan_ssh_host_keys
from .static_frontend import mount_configured_frontend
from .task_management import TaskManagementService


ServerLifecycle = Literal["planned", "active", "maintenance", "disabled", "retired"]
RuntimeLifecycle = Literal["planned", "active", "disabled", "retired"]
RuntimeManagementDriver = Literal["none", "systemd", "systemd_user", "launchd", "mock"]
RuntimeWebAccessMode = Literal["direct_url", "local_ssh_tunnel", "tailscale_serve"]
RuntimeWebAuthMode = Literal["none", "token", "password", "username_password"]
DeploymentMode = Literal["baremetal", "container"]
AgentRole = Literal["primary", "subagent"]
AgentArchetype = Literal["personal", "department", "business"]
AgentLifecycle = Literal["requested", "testing", "active", "suspended", "retired"]
DeploymentLifecycle = Literal["planned", "active", "inactive", "retired"]
GroupResourceType = Literal["server", "runtime", "agent"]
GroupLifecycle = Literal["active", "disabled", "retired"]
ImportStatus = Literal["uploaded", "validating", "ready", "committing", "committed", "rejected", "failed", "superseded"]
EmploymentStatus = Literal["active", "inactive", "departed"]
IssueSeverity = Literal["warning", "error"]
AutomationScope = Literal["server", "runtime", "agent"]
AutomationLifecycle = Literal["draft", "published", "disabled", "archived"]
AutomationRisk = Literal["low", "medium", "high"]
TaskStatus = Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]


class Page(BaseModel):
    limit: int
    offset: int
    total: int


class PaginatedResponse(BaseModel):
    data: list[dict[str, Any]]
    page: Page


class ItemResponse(BaseModel):
    data: dict[str, Any]


class TaskRunListResponse(PaginatedResponse):
    summary: dict[str, int]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: str
    database_user: str
    transaction_read_only: Literal["on"]


class ServerCreate(BaseModel):
    environment_id: UUID
    cloud_provider_id: UUID | None = None
    provider_instance_id: str | None = Field(None, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    lifecycle_status: ServerLifecycle = "planned"
    cpu_cores: float | None = Field(None, gt=0)
    memory_gib: float | None = Field(None, gt=0)
    disk_gib: float | None = Field(None, gt=0)
    internal_ip: str | None = None
    public_ip: str | None = None
    os_name: str | None = Field(None, max_length=200)
    os_version: str | None = Field(None, max_length=200)
    architecture: str | None = Field(None, max_length=100)
    work_account: str | None = Field(None, max_length=200)
    group_ids: list[UUID] = Field(default_factory=list)


class ServerUpdate(BaseModel):
    row_version: int = Field(ge=1)
    environment_id: UUID | None = None
    cloud_provider_id: UUID | None = None
    provider_instance_id: str | None = Field(None, max_length=200)
    name: str | None = Field(None, min_length=1, max_length=200)
    lifecycle_status: ServerLifecycle | None = None
    cpu_cores: float | None = Field(None, gt=0)
    memory_gib: float | None = Field(None, gt=0)
    disk_gib: float | None = Field(None, gt=0)
    internal_ip: str | None = None
    public_ip: str | None = None
    os_name: str | None = Field(None, max_length=200)
    os_version: str | None = Field(None, max_length=200)
    architecture: str | None = Field(None, max_length=100)
    work_account: str | None = Field(None, max_length=200)
    group_ids: list[UUID] | None = None


class ServerConnectionUpsert(BaseModel):
    connection_type: Literal["ssh", "local"] = "ssh"
    host: str = Field(min_length=1, max_length=500)
    port: int | None = Field(22, ge=1, le=65535)
    username: str | None = Field(None, max_length=200)
    credential_ref: str | None = Field(None, max_length=300)
    ssh_password: SecretStr | None = None
    ssh_host_keys: list[str] | None = Field(None, max_length=10)
    use_become: bool = False
    row_version: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def validate_connection(self):
        if self.connection_type == "ssh" and not self.username:
            raise ValueError("SSH connection requires username")
        if self.ssh_password is not None and not self.ssh_password.get_secret_value():
            raise ValueError("SSH password cannot be empty")
        if self.connection_type == "local":
            self.port = None
            self.username = None
            self.credential_ref = None
            self.ssh_password = None
            self.ssh_host_keys = None
        return self


class SshHostKeyScan(BaseModel):
    host: str = Field(min_length=1, max_length=500)
    port: int = Field(22, ge=1, le=65535)


class OperationRunCreate(BaseModel):
    operation_code: str = Field(min_length=1, max_length=200)
    server_ids: list[UUID] | None = None
    runtime_ids: list[UUID] | None = None
    agent_ids: list[UUID] | None = None
    group_id: UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = Field("local-user", min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_targets(self):
        if sum(bool(value) for value in (self.server_ids, self.runtime_ids, self.agent_ids, self.group_id)) != 1:
            raise ValueError("provide exactly one of server_ids, runtime_ids, agent_ids or group_id")
        return self


class TaskActionRequest(BaseModel):
    requested_by: str = Field("local-user", min_length=1, max_length=200)


class ObservabilitySyncPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["management-db"] = "management-db"


class ObservabilitySyncApplyRequest(ObservabilitySyncPreviewRequest):
    expected_snapshot_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = False


class ObservabilityRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False


class AutomationCreate(BaseModel):
    code: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=4000)
    category: str = Field("未分类", min_length=1, max_length=200)
    scope: AutomationScope
    risk_level: AutomationRisk = "low"
    requires_become: bool = False
    supports_batch: bool = False
    timeout_seconds: int = Field(120, ge=1, le=3600)
    version_label: str = Field("1.0.0", min_length=1, max_length=50)
    playbook_content: str = Field(min_length=1, max_length=262144)
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field("local-user", min_length=1, max_length=200)


class AutomationDraftVersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=50)
    created_by: str = Field("local-user", min_length=1, max_length=200)


class AutomationDraftUpdate(BaseModel):
    row_version: int = Field(ge=1)
    version_row_version: int = Field(ge=1)
    version_label: str | None = Field(None, min_length=1, max_length=50)
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    category: str | None = Field(None, min_length=1, max_length=200)
    scope: AutomationScope | None = None
    risk_level: AutomationRisk | None = None
    requires_become: bool | None = None
    supports_batch: bool | None = None
    timeout_seconds: int | None = Field(None, ge=1, le=3600)
    playbook_content: str | None = Field(None, min_length=1, max_length=262144)
    parameter_schema: dict[str, Any] | None = None
    updated_by: str = Field("local-user", min_length=1, max_length=200)


class AutomationVersionAction(BaseModel):
    requested_by: str = Field("local-user", min_length=1, max_length=200)


class RuntimeCreate(BaseModel):
    server_id: UUID
    runtime_family_id: UUID
    name: str = Field(min_length=1, max_length=200)
    deployment_mode: DeploymentMode
    lifecycle_status: RuntimeLifecycle = "planned"
    bind_address: str | None = Field(None, max_length=500)
    endpoint_port: int | None = Field(None, ge=1, le=65535)
    runtime_account: str | None = Field(None, max_length=200)
    config_path: str | None = Field(None, max_length=1000)
    state_path: str | None = Field(None, max_length=1000)
    command_path: str | None = Field(None, max_length=1000, pattern=r"^/")
    container_name: str | None = Field(None, max_length=500)
    image_ref: str | None = Field(None, max_length=1000)
    desired_version: str | None = Field(None, max_length=200)
    management_driver: RuntimeManagementDriver = "none"
    management_target: str | None = Field(None, max_length=500)


class RuntimeUpdate(BaseModel):
    row_version: int = Field(ge=1)
    server_id: UUID | None = None
    runtime_family_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    deployment_mode: DeploymentMode | None = None
    lifecycle_status: RuntimeLifecycle | None = None
    bind_address: str | None = Field(None, max_length=500)
    endpoint_port: int | None = Field(None, ge=1, le=65535)
    runtime_account: str | None = Field(None, max_length=200)
    config_path: str | None = Field(None, max_length=1000)
    state_path: str | None = Field(None, max_length=1000)
    command_path: str | None = Field(None, max_length=1000, pattern=r"^/")
    container_name: str | None = Field(None, max_length=500)
    image_ref: str | None = Field(None, max_length=1000)
    desired_version: str | None = Field(None, max_length=200)
    management_driver: RuntimeManagementDriver | None = None
    management_target: str | None = Field(None, max_length=500)


class RuntimeWebEndpointUpsert(BaseModel):
    endpoint_code: str = Field("control_ui", min_length=1, max_length=100)
    display_name: str = Field("WebUI", min_length=1, max_length=200)
    access_mode: RuntimeWebAccessMode
    scheme: Literal["http", "https"] = "http"
    direct_url: str | None = Field(None, max_length=2000)
    remote_host: str = Field("127.0.0.1", min_length=1, max_length=200)
    port_override: int | None = Field(None, ge=1, le=65535)
    base_path: str = Field("/", min_length=1, max_length=500)
    auth_mode: RuntimeWebAuthMode = "none"
    token: SecretStr | None = None
    username: str | None = Field(None, max_length=500)
    password: SecretStr | None = None
    enabled: bool = True
    is_primary: bool = True
    row_version: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def validate_access_shape(self):
        if self.access_mode in {"direct_url", "tailscale_serve"} and not self.direct_url:
            raise ValueError("direct_url is required for direct access")
        if self.access_mode == "local_ssh_tunnel" and self.direct_url:
            raise ValueError("direct_url is not used with local SSH tunnel access")
        return self


class RuntimeWebAccessSessionCreate(BaseModel):
    requested_by: str = Field("local-user", min_length=1, max_length=200)


class AgentChannelInput(BaseModel):
    channel_type: Literal["none", "feishu"] = "none"
    application_name: str | None = Field(None, max_length=200)
    app_id: str | None = Field(None, max_length=300)
    app_secret: SecretStr | None = None


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    structure_role: AgentRole
    parent_agent_id: UUID | None = None
    service_archetype: AgentArchetype
    lifecycle_status: AgentLifecycle = "requested"
    owning_department_id: UUID | None = None
    business_admin_person_id: UUID | None = None
    it_admin_person_id: UUID | None = None
    primary_service_person_id: UUID | None = None
    primary_service_department_id: UUID | None = None
    business_domain: str | None = Field(None, max_length=500)
    business_scenario: str | None = Field(None, max_length=1000)
    description: str | None = Field(None, max_length=4000)
    accessible_person_ids: list[UUID] = Field(default_factory=list)
    group_ids: list[UUID] = Field(default_factory=list)
    channel: AgentChannelInput = Field(default_factory=AgentChannelInput)


class AgentUpdate(BaseModel):
    row_version: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    service_archetype: AgentArchetype | None = None
    lifecycle_status: AgentLifecycle | None = None
    owning_department_id: UUID | None = None
    business_admin_person_id: UUID | None = None
    it_admin_person_id: UUID | None = None
    primary_service_person_id: UUID | None = None
    primary_service_department_id: UUID | None = None
    business_domain: str | None = Field(None, max_length=500)
    business_scenario: str | None = Field(None, max_length=1000)
    description: str | None = Field(None, max_length=4000)
    accessible_person_ids: list[UUID] | None = None
    group_ids: list[UUID] | None = None
    channel: AgentChannelInput | None = None


class GroupCreate(BaseModel):
    group_code: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    resource_type: GroupResourceType
    purpose: str | None = Field(None, max_length=1000)
    lifecycle_status: GroupLifecycle = "active"
    member_ids: list[UUID] = Field(default_factory=list)


class GroupUpdate(BaseModel):
    row_version: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    purpose: str | None = Field(None, max_length=1000)
    lifecycle_status: GroupLifecycle | None = None
    member_ids: list[UUID] | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr
    challenge_id: UUID
    challenge_offset: int = Field(ge=0, le=238)


class PasswordChangeRequest(BaseModel):
    old_password: SecretStr
    new_password: SecretStr


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    password: SecretStr
    role: Literal["admin", "operator", "viewer"]


class UserUpdateRequest(BaseModel):
    row_version: int = Field(ge=1)
    display_name: str | None = Field(None, min_length=1, max_length=100)
    password: SecretStr | None = None
    role: Literal["admin", "operator", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    parent_department_id: UUID | None = None
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(ge=1)
    name: str | None = Field(None, min_length=1, max_length=200)
    parent_department_id: UUID | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        for field_name in ("name", "is_active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class PersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_no: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    primary_department_id: UUID | None = None
    employment_status: EmploymentStatus = "active"


class PersonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(ge=1)
    display_name: str | None = Field(None, min_length=1, max_length=200)
    primary_department_id: UUID | None = None
    employment_status: EmploymentStatus | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        for field_name in ("display_name", "employment_status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


app = FastAPI(
    title="AgentsWatch Core Asset API",
    version=__version__,
    description="Local-first management API for Server, Runtime, Agent and import data.",
)

SESSION_COOKIE = "agentswatch_session"
CSRF_COOKIE = "agentswatch_csrf"
PUBLIC_AUTH_PATHS = {"/api/v1/auth/challenge", "/api/v1/auth/login"}
PROTECTED_DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def client_fingerprint(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}|{request.headers.get('user-agent', '')[:500]}"


def cookie_settings():
    return {
        "secure": os.environ.get("AGENTSWATCH_SESSION_COOKIE_SECURE", "true").lower() == "true",
        "samesite": "strict",
        "path": os.environ.get("AGENTSWATCH_SESSION_COOKIE_PATH", "/"),
    }


def set_auth_cookies(response: Response, session_token: str, csrf_token: str, expires_at):
    settings = cookie_settings()
    max_age = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE, session_token, max_age=max_age, httponly=True, **settings,
    )
    response.set_cookie(
        CSRF_COOKIE, csrf_token, max_age=max_age, httponly=False, **settings,
    )


def clear_auth_cookies(response: Response):
    settings = cookie_settings()
    response.delete_cookie(SESSION_COOKIE, httponly=True, **settings)
    response.delete_cookie(CSRF_COOKIE, httponly=False, **settings)


@app.middleware("http")
async def authenticate_management_request(request: Request, call_next):
    path = request.url.path
    protected = (
        path.startswith("/api/v1/") and path not in PUBLIC_AUTH_PATHS
    ) or path in PROTECTED_DOC_PATHS
    if protected:
        try:
            authorization = request.headers.get("authorization", "")
            scheme, separator, credential = authorization.partition(" ")
            if separator and scheme.lower() == "bearer":
                user = await run_in_threadpool(
                    AuthService().authenticate_api_key, credential.strip(),
                )
                request.state.authentication_method = "api_key"
            else:
                user = await run_in_threadpool(
                    AuthService().authenticate_session,
                    request.cookies.get(SESSION_COOKIE),
                    client_fingerprint(request),
                )
                request.state.authentication_method = "session"
            request.state.current_user = user
            if request.method in MUTATING_METHODS and request.state.authentication_method == "session":
                await run_in_threadpool(
                    AuthService().verify_csrf,
                    user,
                    request.headers.get("x-csrf-token"),
                    request.cookies.get(CSRF_COOKIE),
                )
        except AuthenticationError as exc:
            response = JSONResponse(status_code=401, content={"detail": str(exc)})
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except AuthorizationError as exc:
            response = JSONResponse(status_code=403, content={"detail": str(exc)})
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
    response = await call_next(request)
    if path.startswith("/api/v1/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.on_event("startup")
def initialize_automation_library():
    cleanup_stale_runner_workspaces()
    if os.environ.get("AGENTSWATCH_ENABLE_MANAGEMENT") == "true":
        AutomationLibraryService().sync_builtin_catalog()
        catalog = ManagedOperationCatalog()
        for queued in TaskManagementService().recover_for_startup():
            service = {
                "server": lambda: OperationService(catalog=catalog),
                "runtime": lambda: RuntimeOperationService(catalog=catalog),
                "agent": lambda: AgentOperationService(catalog=catalog),
            }[queued["target_type"]]()
            record_system_event(
                queued["run_id"], "task_recovered", "API 启动后重新认领等待中的任务。",
                phase="dispatch",
            )
            threading.Thread(
                target=service.execute_run,
                args=(queued["run_id"],),
                name=f"agentswatch-operation-{queued['run_id']}",
                daemon=True,
            ).start()


def query_service():
    return CoreAssetQueries(Database())


def current_user(request: Request):
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_permission(request: Request, permission: str):
    if os.environ.get("AGENTSWATCH_ENABLE_MANAGEMENT") != "true":
        raise HTTPException(status_code=403, detail="management operations are disabled")
    user = current_user(request)
    if permission not in user["permissions"]:
        raise HTTPException(status_code=403, detail="当前账号没有执行此操作的权限")
    return user


def require_operation_confirmation(request: Request, user: dict[str, Any], body: Any = b""):
    try:
        return ConfirmationService().require(
            confirmation_id=request.headers.get(CONFIRMATION_HEADER),
            user_id=user["user_id"],
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            body=body,
        )
    except ConfirmationError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail()) from exc


def management_error(exc):
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ConflictError, psycopg.IntegrityError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (ValueError, KeyError, RuntimeError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def observability_integration_error(exc: Exception):
    if isinstance(exc, ObservabilityIntegrationError):
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    raise exc


@app.exception_handler(psycopg.Error)
def handle_database_error(request: Request, exc: psycopg.Error):
    return JSONResponse(status_code=503, content={"detail": "core asset database is unavailable"})


@app.get("/healthz", response_model=HealthResponse)
def healthz():
    health = query_service().health()
    return {"status": "ok", **health}


@app.post("/api/v1/auth/challenge", response_model=ItemResponse)
def create_login_challenge(request: Request):
    return {"data": AuthService().create_challenge(client_fingerprint(request))}


@app.post("/api/v1/auth/login", response_model=ItemResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    try:
        result = AuthService().login(
            payload.username,
            payload.password.get_secret_value(),
            payload.challenge_id,
            payload.challenge_offset,
            client_fingerprint(request),
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    set_auth_cookies(response, result["session_token"], result["csrf_token"], result["expires_at"])
    return {"data": {"user": result["user"], "expires_at": result["expires_at"]}}


@app.get("/api/v1/auth/me", response_model=ItemResponse)
def get_current_session(request: Request):
    user = dict(current_user(request))
    user.pop("csrf_token_hash", None)
    user.pop("session_id", None)
    return {"data": user}


@app.get("/api/v1/auth/api-key", response_model=ItemResponse)
def get_api_key(request: Request):
    user = current_user(request)
    return {"data": AuthService().get_api_key_status(UUID(user["user_id"]))}


@app.post("/api/v1/auth/api-key", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(request: Request):
    user = current_user(request)
    try:
        return {"data": AuthService().create_api_key(UUID(user["user_id"]))}
    except (AuthenticationError, ValueError, psycopg.IntegrityError) as exc:
        management_error(exc)


@app.post("/api/v1/auth/api-key/rotate", response_model=ItemResponse)
def rotate_api_key(request: Request):
    user = current_user(request)
    try:
        return {"data": AuthService().rotate_api_key(UUID(user["user_id"]))}
    except (AuthenticationError, ValueError, psycopg.IntegrityError) as exc:
        management_error(exc)


@app.delete("/api/v1/auth/api-key", response_model=ItemResponse)
def revoke_api_key(request: Request):
    user = current_user(request)
    try:
        return {"data": AuthService().revoke_api_key(UUID(user["user_id"]))}
    except (AuthenticationError, ValueError) as exc:
        management_error(exc)


@app.post("/api/v1/auth/logout", response_model=ItemResponse)
def logout(request: Request, response: Response):
    user = current_user(request)
    if user.get("session_id"):
        AuthService().logout(UUID(user["session_id"]))
    clear_auth_cookies(response)
    return {"data": {"logged_out": True}}


@app.post("/api/v1/auth/change-password", response_model=ItemResponse)
def change_password(payload: PasswordChangeRequest, request: Request, response: Response):
    user = current_user(request)
    try:
        AuthService().change_password(
            UUID(user["user_id"]),
            payload.old_password.get_secret_value(),
            payload.new_password.get_secret_value(),
        )
    except (AuthenticationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    clear_auth_cookies(response)
    return {"data": {"password_changed": True}}


@app.get("/api/v1/users", response_model=ItemResponse)
def list_users(request: Request):
    require_permission(request, "manage_users")
    return {"data": {"users": AuthService().list_users(), "roles": PERMISSION_MATRIX}}


@app.post("/api/v1/users", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreateRequest, request: Request):
    user = require_permission(request, "manage_users")
    try:
        created = AuthService().create_user(
            payload.username,
            payload.display_name,
            payload.password.get_secret_value(),
            payload.role,
            user["username"],
        )
        return {"data": created}
    except (ValueError, psycopg.IntegrityError) as exc:
        management_error(exc)


@app.patch("/api/v1/users/{user_id}", response_model=ItemResponse)
def update_user(user_id: UUID, payload: UserUpdateRequest, request: Request):
    actor = require_permission(request, "manage_users")
    values = payload.model_dump(mode="json", exclude={"row_version"}, exclude_unset=True)
    if payload.password is not None:
        values["password"] = payload.password.get_secret_value()
    try:
        return {
            "data": AuthService().update_user(
                user_id, payload.row_version, values, actor["username"]
            )
        }
    except ValueError as exc:
        management_error(exc)


@app.get("/api/v1/overview", response_model=ItemResponse)
def overview():
    return {"data": query_service().overview()}


@app.get("/api/v1/reference-data", response_model=ItemResponse)
def reference_data():
    return {"data": query_service().reference_data()}


@app.get("/api/v1/people", response_model=PaginatedResponse)
def list_people(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    employment_status: EmploymentStatus | None = None,
    department_code: str | None = None,
    q: str | None = Query(None, max_length=200),
):
    return query_service().people(limit, offset, employment_status, department_code, q)


@app.get("/api/v1/people/{person_id}", response_model=ItemResponse)
def get_person(person_id: UUID):
    item = query_service().person(person_id)
    if item is None:
        raise HTTPException(status_code=404, detail="person not found")
    return {"data": item}


@app.post("/api/v1/people", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_person(payload: PersonCreate, request: Request):
    user = require_permission(request, "manage_assets")
    try:
        person_id = OrganizationManagementService().create_person(
            payload.model_dump(mode="json"), user["username"],
        )
        return {"data": query_service().person(person_id)}
    except (ValueError, ConflictError, NotFoundError, psycopg.IntegrityError) as exc:
        management_error(exc)


@app.patch("/api/v1/people/{person_id}", response_model=ItemResponse)
def update_person(person_id: UUID, payload: PersonUpdate, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(mode="json", exclude={"row_version"}, exclude_unset=True)
    try:
        OrganizationManagementService().update_person(
            person_id, values, payload.row_version, user["username"],
        )
        return {"data": query_service().person(person_id)}
    except (ValueError, ConflictError, NotFoundError, psycopg.IntegrityError) as exc:
        management_error(exc)


@app.get("/api/v1/departments", response_model=PaginatedResponse)
def list_departments(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    is_active: bool | None = None,
    parent_department_id: UUID | None = None,
    q: str | None = Query(None, max_length=200),
):
    return query_service().departments(limit, offset, is_active, parent_department_id, q)


@app.get("/api/v1/departments/{department_id}", response_model=ItemResponse)
def get_department(department_id: UUID):
    item = query_service().department(department_id)
    if item is None:
        raise HTTPException(status_code=404, detail="department not found")
    return {"data": item}


@app.post("/api/v1/departments", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_department(payload: DepartmentCreate, request: Request):
    user = require_permission(request, "manage_assets")
    try:
        department_id = OrganizationManagementService().create_department(
            payload.model_dump(mode="json"), user["username"],
        )
        return {"data": query_service().department(department_id)}
    except (ValueError, ConflictError, NotFoundError, psycopg.IntegrityError) as exc:
        management_error(exc)


@app.patch("/api/v1/departments/{department_id}", response_model=ItemResponse)
def update_department(department_id: UUID, payload: DepartmentUpdate, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(mode="json", exclude={"row_version"}, exclude_unset=True)
    try:
        OrganizationManagementService().update_department(
            department_id, values, payload.row_version, user["username"],
        )
        return {"data": query_service().department(department_id)}
    except (ValueError, ConflictError, NotFoundError, psycopg.IntegrityError) as exc:
        management_error(exc)


@app.get("/api/v1/servers", response_model=PaginatedResponse)
def list_servers(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lifecycle_status: ServerLifecycle | None = None,
    environment_code: str | None = None,
    group_code: str | None = None,
    q: str | None = Query(None, max_length=200),
):
    return query_service().servers(limit, offset, lifecycle_status, environment_code, group_code, q)


@app.get("/api/v1/servers/{server_id}", response_model=ItemResponse)
def get_server(server_id: UUID):
    item = query_service().server(server_id)
    if item is None:
        raise HTTPException(status_code=404, detail="server not found")
    return {"data": item}


@app.post("/api/v1/servers", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_server(payload: ServerCreate, request: Request):
    user = require_permission(request, "manage_assets")
    try:
        server_id = ServerManagementService().create_server(
            payload.model_dump(mode="json"), user["username"],
        )
        return {"data": query_service().server(server_id)}
    except Exception as exc:
        management_error(exc)


@app.patch("/api/v1/servers/{server_id}", response_model=ItemResponse)
def update_server(server_id: UUID, payload: ServerUpdate, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(mode="json", exclude={"row_version"}, exclude_unset=True)
    try:
        ServerManagementService().update_server(
            server_id, values, payload.row_version, user["username"],
        )
        return {"data": query_service().server(server_id)}
    except Exception as exc:
        management_error(exc)


@app.delete("/api/v1/servers/{server_id}", response_model=ItemResponse)
def retire_server(request: Request, server_id: UUID, row_version: int = Query(ge=1)):
    user = require_permission(request, "manage_assets")
    require_operation_confirmation(request, user)
    try:
        ServerManagementService().retire_server(server_id, row_version, user["username"])
        return {"data": query_service().server(server_id)}
    except Exception as exc:
        management_error(exc)


@app.put("/api/v1/servers/{server_id}/connection", response_model=ItemResponse)
def save_server_connection(server_id: UUID, payload: ServerConnectionUpsert, request: Request):
    user = require_permission(request, "manage_assets")
    if payload.connection_type == "local" and os.environ.get("AGENTSWATCH_ALLOW_LOCAL_CONNECTION") != "true":
        raise HTTPException(status_code=422, detail="local connection is disabled")
    try:
        values = payload.model_dump(mode="json", exclude={"ssh_password"})
        values["ssh_password"] = (
            payload.ssh_password.get_secret_value() if payload.ssh_password is not None else None
        )
        ServerManagementService().save_connection(server_id, values, user["username"])
        return {"data": query_service().server(server_id)}
    except Exception as exc:
        management_error(exc)


@app.post("/api/v1/server-connections/ssh-host-keys:scan", response_model=ItemResponse)
def scan_server_ssh_host_keys(payload: SshHostKeyScan, request: Request):
    require_permission(request, "manage_assets")
    try:
        return {"data": scan_ssh_host_keys(payload.host, payload.port)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/automation/operations", response_model=ItemResponse)
def list_automation_operations():
    items = [
        {
            "code": item.code, "name": item.name, "scope": item.scope,
            "description": item.description, "category": item.category,
            "version": item.version,
            "risk_level": item.risk_level, "requires_become": item.requires_become,
            "supports_batch": item.supports_batch,
            "timeout_seconds": item.timeout_seconds, "parameters": item.parameters,
        }
        for item in ManagedOperationCatalog().list()
    ]
    return {"data": {"operations": items}}


@app.post("/api/v1/automation/runs", response_model=ItemResponse, status_code=status.HTTP_202_ACCEPTED)
def create_operation_run(payload: OperationRunCreate, background_tasks: BackgroundTasks, request: Request):
    user = require_permission(request, "execute_automation")
    try:
        catalog = ManagedOperationCatalog()
        operation = catalog.get(payload.operation_code)
        if operation.risk_level in {"medium", "high"}:
            require_operation_confirmation(request, user, payload.model_dump(mode="json"))
        if operation.scope == "server":
            if not (payload.server_ids or payload.group_id):
                raise ValueError("server operation requires server_ids or group_id")
            service = OperationService(catalog=catalog)
            run_id = service.create_run(
                payload.operation_code, payload.server_ids, payload.group_id,
                payload.parameters, user["username"],
            )
        elif operation.scope == "runtime":
            if not payload.runtime_ids:
                raise ValueError("runtime operation requires runtime_ids")
            service = RuntimeOperationService(catalog=catalog)
            run_id = service.create_run(
                payload.operation_code, payload.runtime_ids,
                payload.parameters, user["username"],
            )
        elif operation.scope == "agent":
            if not payload.agent_ids:
                raise ValueError("agent operation requires agent_ids")
            service = AgentOperationService(catalog=catalog)
            run_id = service.create_run(
                payload.operation_code, payload.agent_ids, payload.parameters, user["username"],
            )
        else:
            raise ValueError("unsupported operation scope")
        record_system_event(
            run_id, "task_queued", "任务已进入执行队列。",
            data={"operation_code": payload.operation_code},
        )
        background_tasks.add_task(service.execute_run, run_id)
        return {"data": query_service().operation_run(run_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/automation/runs", response_model=TaskRunListResponse)
def list_operation_runs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: TaskStatus | None = None,
    target_type: AutomationScope | None = None,
    operation_code: str | None = Query(None, max_length=200),
    requested_by: str | None = Query(None, max_length=200),
    q: str | None = Query(None, max_length=200),
):
    return query_service().operation_runs(
        limit, offset, status, target_type, operation_code, requested_by, q
    )


@app.get("/api/v1/automation/runs/{run_id}", response_model=ItemResponse)
def get_operation_run(run_id: UUID):
    item = query_service().operation_run(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail="operation run not found")
    return {"data": item}


@app.get("/api/v1/automation/runs/{run_id}/events")
def get_operation_run_events(
    run_id: UUID,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    target_key: str | None = Query(None, max_length=200),
):
    result = query_service().operation_events(run_id, after_sequence, limit, target_key)
    if result is None:
        raise HTTPException(status_code=404, detail="operation run not found")
    return result


@app.post("/api/v1/automation/runs/{run_id}/cancel", response_model=ItemResponse)
def cancel_operation_run(run_id: UUID, payload: TaskActionRequest, request: Request):
    user = require_permission(request, "execute_automation")
    try:
        TaskManagementService().cancel(run_id, user["username"])
        return {"data": query_service().operation_run(run_id)}
    except Exception as exc:
        management_error(exc)


@app.post("/api/v1/automation/runs/{run_id}/retry", response_model=ItemResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_operation_run(run_id: UUID, payload: TaskActionRequest, background_tasks: BackgroundTasks, request: Request):
    user = require_permission(request, "execute_automation")
    try:
        tasks = TaskManagementService()
        specification = tasks.retry_specification(run_id)
        catalog = ManagedOperationCatalog()
        operation = catalog.get(specification["operation_code"])
        if operation.risk_level in {"medium", "high"}:
            require_operation_confirmation(request, user, payload.model_dump(mode="json"))
        if specification["target_type"] == "server":
            service = OperationService(catalog=catalog)
            retry_id = service.create_run(
                operation.code, specification["server_ids"], None,
                specification["parameters"], user["username"],
            )
        elif specification["target_type"] == "runtime":
            service = RuntimeOperationService(catalog=catalog)
            retry_id = service.create_run(
                operation.code, specification["runtime_ids"],
                specification["parameters"], user["username"],
            )
        else:
            service = AgentOperationService(catalog=catalog)
            retry_id = service.create_run(
                operation.code, specification["agent_ids"],
                specification["parameters"], user["username"],
            )
        tasks.link_retry(retry_id, run_id)
        record_system_event(
            retry_id, "task_queued", "重试任务已进入执行队列。",
            data={"operation_code": operation.code, "retry_of_run_id": str(run_id)},
        )
        background_tasks.add_task(service.execute_run, retry_id)
        return {"data": query_service().operation_run(retry_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/automation-library", response_model=PaginatedResponse)
def list_automation_library(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    scope: AutomationScope | None = None,
    lifecycle_status: AutomationLifecycle | None = None,
    q: str | None = Query(None, max_length=200),
):
    return AutomationLibraryService().list(limit, offset, scope, lifecycle_status, q)


@app.post("/api/v1/automation-library", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_automation(payload: AutomationCreate, request: Request):
    user = require_permission(request, "manage_automation_library")
    try:
        values = payload.model_dump(mode="json", exclude={"created_by"})
        automation_id = AutomationLibraryService().create(values, user["username"])
        return {"data": AutomationLibraryService().get(automation_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/automation-library/{automation_id}", response_model=ItemResponse)
def get_automation(automation_id: UUID):
    item = AutomationLibraryService().get(automation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="automation definition not found")
    return {"data": item}


@app.post("/api/v1/automation-library/{automation_id}/versions", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_automation_version(automation_id: UUID, payload: AutomationDraftVersionCreate, request: Request):
    user = require_permission(request, "manage_automation_library")
    try:
        AutomationLibraryService().create_draft_version(
            automation_id, payload.version_label, user["username"]
        )
        return {"data": AutomationLibraryService().get(automation_id)}
    except Exception as exc:
        management_error(exc)


@app.patch("/api/v1/automation-library/{automation_id}/versions/{version_id}", response_model=ItemResponse)
def update_automation_version(automation_id: UUID, version_id: UUID, payload: AutomationDraftUpdate, request: Request):
    user = require_permission(request, "manage_automation_library")
    try:
        values = payload.model_dump(
            mode="json",
            exclude={"row_version", "version_row_version", "updated_by"},
            exclude_unset=True,
        )
        AutomationLibraryService().update_draft(
            automation_id, version_id, values, payload.row_version,
            payload.version_row_version, user["username"],
        )
        return {"data": AutomationLibraryService().get(automation_id)}
    except Exception as exc:
        management_error(exc)


@app.post("/api/v1/automation-library/{automation_id}/versions/{version_id}/validate", response_model=ItemResponse)
def validate_automation_version(automation_id: UUID, version_id: UUID, payload: AutomationVersionAction, request: Request):
    require_permission(request, "manage_automation_library")
    try:
        result = AutomationLibraryService().validate_version(automation_id, version_id)
        return {"data": result}
    except Exception as exc:
        management_error(exc)


@app.post("/api/v1/automation-library/{automation_id}/versions/{version_id}/publish", response_model=ItemResponse)
def publish_automation_version(automation_id: UUID, version_id: UUID, payload: AutomationVersionAction, request: Request):
    user = require_permission(request, "manage_automation_library")
    require_operation_confirmation(request, user, payload.model_dump(mode="json"))
    try:
        AutomationLibraryService().publish_version(
            automation_id, version_id, user["username"]
        )
        return {"data": AutomationLibraryService().get(automation_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/runtimes", response_model=PaginatedResponse)
def list_runtimes(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lifecycle_status: RuntimeLifecycle | None = None,
    family_code: str | None = None,
    deployment_mode: DeploymentMode | None = None,
    server_id: UUID | None = None,
    group_code: str | None = None,
    q: str | None = Query(None, max_length=200),
):
    return query_service().runtimes(
        limit, offset, lifecycle_status, family_code, deployment_mode, server_id, group_code, q
    )


@app.get("/api/v1/runtimes/{runtime_id}", response_model=ItemResponse)
def get_runtime(runtime_id: UUID):
    item = query_service().runtime(runtime_id)
    if item is None:
        raise HTTPException(status_code=404, detail="runtime not found")
    return {"data": item}


@app.post("/api/v1/runtimes", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_runtime(payload: RuntimeCreate, request: Request):
    user = require_permission(request, "manage_assets")
    try:
        runtime_id = RuntimeManagementService().create_runtime(
            payload.model_dump(mode="json"), user["username"],
        )
        return {"data": query_service().runtime(runtime_id)}
    except Exception as exc:
        management_error(exc)


@app.patch("/api/v1/runtimes/{runtime_id}", response_model=ItemResponse)
def update_runtime(runtime_id: UUID, payload: RuntimeUpdate, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(mode="json", exclude={"row_version"}, exclude_unset=True)
    try:
        RuntimeManagementService().update_runtime(
            runtime_id, values, payload.row_version, user["username"],
        )
        return {"data": query_service().runtime(runtime_id)}
    except Exception as exc:
        management_error(exc)


@app.delete("/api/v1/runtimes/{runtime_id}", response_model=ItemResponse)
def retire_runtime(request: Request, runtime_id: UUID, row_version: int = Query(ge=1)):
    user = require_permission(request, "manage_assets")
    require_operation_confirmation(request, user)
    try:
        RuntimeManagementService().retire_runtime(runtime_id, row_version, user["username"])
        return {"data": query_service().runtime(runtime_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/runtimes/{runtime_id}/web-endpoint", response_model=ItemResponse)
def get_runtime_web_endpoint(runtime_id: UUID):
    item = query_service().runtime_web_endpoint(runtime_id)
    if item is None:
        raise HTTPException(status_code=404, detail="runtime WebUI endpoint not found")
    return {"data": item}


@app.put("/api/v1/runtimes/{runtime_id}/web-endpoint", response_model=ItemResponse)
def save_runtime_web_endpoint(runtime_id: UUID, payload: RuntimeWebEndpointUpsert, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(mode="json", exclude={"token", "username", "password"})
    credentials = {
        "token": payload.token.get_secret_value() if payload.token is not None else None,
        "username": payload.username,
        "password": payload.password.get_secret_value() if payload.password is not None else None,
    }
    try:
        RuntimeWebAccessService().upsert_endpoint(
            runtime_id, values, credentials, user["username"],
        )
        return {"data": query_service().runtime_web_endpoint(runtime_id)}
    except Exception as exc:
        management_error(exc)


@app.post("/api/v1/runtimes/{runtime_id}/web-access-sessions", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_runtime_web_access_session(runtime_id: UUID, payload: RuntimeWebAccessSessionCreate, request: Request):
    user = require_permission(request, "manage_assets")
    try:
        return {"data": RuntimeWebAccessService().create_session(runtime_id, user["username"])}
    except Exception as exc:
        management_error(exc)


@app.delete("/api/v1/runtime-web-access-sessions/{session_id}", response_model=ItemResponse)
def close_runtime_web_access_session(session_id: UUID, request: Request):
    require_permission(request, "manage_assets")
    try:
        return {"data": RuntimeWebAccessService().close_session(session_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/agents", response_model=PaginatedResponse)
def list_agents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    structure_role: AgentRole | None = None,
    service_archetype: AgentArchetype | None = None,
    lifecycle_status: AgentLifecycle | None = None,
    department_code: str | None = None,
    group_code: str | None = None,
    q: str | None = Query(None, max_length=200),
):
    return query_service().agents(
        limit, offset, structure_role, service_archetype, lifecycle_status, department_code, group_code, q
    )


def agent_channel_values(channel: AgentChannelInput | None):
    if channel is None:
        return None
    values = channel.model_dump(mode="json", exclude={"app_secret"})
    values["app_secret"] = channel.app_secret.get_secret_value() if channel.app_secret is not None else None
    return values


@app.post("/api/v1/agents", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(
        mode="json", exclude={"accessible_person_ids", "group_ids", "channel"}
    )
    try:
        agent_id = AgentManagementService().create_agent(
            values,
            payload.accessible_person_ids,
            payload.group_ids,
            agent_channel_values(payload.channel),
            user["username"],
        )
        return {"data": query_service().agent(agent_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/agents/{agent_id}", response_model=ItemResponse)
def get_agent(agent_id: UUID):
    item = query_service().agent(agent_id)
    if item is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"data": item}


@app.patch("/api/v1/agents/{agent_id}", response_model=ItemResponse)
def update_agent(agent_id: UUID, payload: AgentUpdate, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(
        mode="json",
        exclude={"row_version", "accessible_person_ids", "group_ids", "channel"},
        exclude_unset=True,
    )
    try:
        AgentManagementService().update_agent(
            agent_id,
            values,
            payload.row_version,
            payload.accessible_person_ids if "accessible_person_ids" in payload.model_fields_set else None,
            payload.group_ids if "group_ids" in payload.model_fields_set else None,
            agent_channel_values(payload.channel) if "channel" in payload.model_fields_set else None,
            user["username"],
        )
        return {"data": query_service().agent(agent_id)}
    except Exception as exc:
        management_error(exc)


@app.delete("/api/v1/agents/{agent_id}", response_model=ItemResponse)
def retire_agent(request: Request, agent_id: UUID, row_version: int = Query(ge=1)):
    user = require_permission(request, "manage_assets")
    require_operation_confirmation(request, user)
    try:
        AgentManagementService().retire_agent(agent_id, row_version, user["username"])
        return {"data": query_service().agent(agent_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/deployments", response_model=PaginatedResponse)
def list_deployments(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    lifecycle_status: DeploymentLifecycle | None = None,
    runtime_id: UUID | None = None,
    primary_agent_id: UUID | None = None,
):
    return query_service().deployments(limit, offset, lifecycle_status, runtime_id, primary_agent_id)


@app.get("/api/v1/groups", response_model=PaginatedResponse)
def list_groups(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    resource_type: GroupResourceType | None = None,
    lifecycle_status: GroupLifecycle | None = None,
    q: str | None = Query(None, max_length=200),
):
    return query_service().groups(limit, offset, resource_type, lifecycle_status, q)


@app.get("/api/v1/groups/{group_id}", response_model=ItemResponse)
def get_group(group_id: UUID):
    item = query_service().group(group_id)
    if item is None:
        raise HTTPException(status_code=404, detail="management group not found")
    return {"data": item}


@app.post("/api/v1/groups", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, request: Request):
    user = require_permission(request, "manage_assets")
    try:
        group_id = GroupManagementService().create_group(
            payload.model_dump(mode="json"), user["username"],
        )
        return {"data": query_service().group(group_id)}
    except Exception as exc:
        management_error(exc)


@app.patch("/api/v1/groups/{group_id}", response_model=ItemResponse)
def update_group(group_id: UUID, payload: GroupUpdate, request: Request):
    user = require_permission(request, "manage_assets")
    values = payload.model_dump(mode="json", exclude={"row_version"}, exclude_unset=True)
    try:
        GroupManagementService().update_group(
            group_id, values, payload.row_version, user["username"],
        )
        return {"data": query_service().group(group_id)}
    except Exception as exc:
        management_error(exc)


@app.delete("/api/v1/groups/{group_id}", response_model=ItemResponse)
def retire_group(group_id: UUID, request: Request, row_version: int = Query(ge=1)):
    user = require_permission(request, "manage_assets")
    require_operation_confirmation(request, user)
    try:
        GroupManagementService().retire_group(group_id, row_version, user["username"])
        return {"data": query_service().group(group_id)}
    except Exception as exc:
        management_error(exc)


@app.get("/api/v1/imports", response_model=PaginatedResponse)
def list_imports(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: ImportStatus | None = None,
):
    return query_service().imports(limit, offset, status)


@app.get("/api/v1/imports/template")
def download_import_template():
    content = BulkImportService.template_bytes()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=agentswatch-bulk-import-v1.xlsx"},
    )


@app.post("/api/v1/imports/preview", response_model=ItemResponse, status_code=201)
async def preview_import(request: Request, file: UploadFile = File(...)):
    user = require_permission(request, "manage_assets")
    content = await file.read(MAX_FILE_BYTES + 1)
    file_name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1][:255]
    try:
        batch_id = await run_in_threadpool(
            BulkImportService().preview, file_name, content, user["username"],
        )
    except Exception as exc:
        management_error(exc)
    return {"data": query_service().import_batch(batch_id)}


@app.post("/api/v1/imports/{batch_id}/commit", response_model=ItemResponse)
def commit_import(batch_id: UUID, request: Request):
    user = require_permission(request, "manage_assets")
    try:
        BulkImportService().commit(batch_id, user["username"])
    except Exception as exc:
        management_error(exc)
    return {"data": query_service().import_batch(batch_id)}


@app.get("/api/v1/imports/{batch_id}", response_model=ItemResponse)
def get_import_batch(batch_id: UUID):
    item = query_service().import_batch(batch_id)
    if item is None:
        raise HTTPException(status_code=404, detail="import batch not found")
    return {"data": item}


@app.get("/api/v1/data-quality/issues", response_model=PaginatedResponse)
def list_data_quality_issues(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    severity: IssueSeverity | None = None,
    issue_code: str | None = None,
    batch_id: UUID | None = None,
):
    return query_service().data_quality_issues(limit, offset, severity, issue_code, batch_id)


@app.get("/api/v1/observability-integration/status", response_model=ItemResponse)
def get_observability_integration_status(request: Request):
    require_permission(request, "view")
    try:
        result = ObservabilityIntegrationClient().status()
    except Exception as exc:
        observability_integration_error(exc)
    return {"data": result}


@app.post("/api/v1/observability-integration/sync/preview", response_model=ItemResponse)
def preview_observability_integration_sync(
    payload: ObservabilitySyncPreviewRequest,
    request: Request,
):
    require_permission(request, "manage_assets")
    try:
        result = ObservabilityIntegrationClient().preview(payload.source)
    except Exception as exc:
        observability_integration_error(exc)
    return {"data": result}


@app.post(
    "/api/v1/observability-integration/sync/apply",
    response_model=ItemResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def apply_observability_integration_sync(
    payload: ObservabilitySyncApplyRequest,
    request: Request,
):
    require_permission(request, "execute_automation")
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="请先确认同步预览结果")
    try:
        result = ObservabilityIntegrationClient().apply(
            payload.source, payload.expected_snapshot_hash,
        )
    except Exception as exc:
        observability_integration_error(exc)
    return {"data": result}


@app.get("/api/v1/observability-integration/jobs/{job_id}", response_model=ItemResponse)
def get_observability_integration_job(job_id: str, request: Request):
    require_permission(request, "view")
    if not job_id or len(job_id) > 128 or not all(
        character.isalnum() or character in {"-", "_"} for character in job_id
    ):
        raise HTTPException(status_code=422, detail="同步任务编号无效")
    try:
        result = ObservabilityIntegrationClient().job(job_id)
    except Exception as exc:
        observability_integration_error(exc)
    return {"data": result}


@app.post(
    "/api/v1/observability-integration/rollback/{revision_id}",
    response_model=ItemResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def rollback_observability_integration(
    revision_id: str,
    payload: ObservabilityRollbackRequest,
    request: Request,
):
    require_permission(request, "execute_automation")
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="请先确认回滚操作")
    if not revision_id or len(revision_id) > 128 or not all(
        character.isalnum() or character in {"-", "_"} for character in revision_id
    ):
        raise HTTPException(status_code=422, detail="同步版本编号无效")
    try:
        result = ObservabilityIntegrationClient().rollback(revision_id)
    except Exception as exc:
        observability_integration_error(exc)
    return {"data": result}


# Register the optional deployment frontend last so every API route keeps
# precedence. Nginx strips the public /agentswatch/ prefix before proxying here.
mount_configured_frontend(app)
