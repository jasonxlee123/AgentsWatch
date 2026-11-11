from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import PlainTextResponse

from . import __version__
from .config import ControlConfig
from .control import (
    InventoryController,
    RevisionNotFound,
    SnapshotConflict,
    canonical_hash,
)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["management-db", "local-json"] = "management-db"
    expected_snapshot_hash: str | None = Field(None, min_length=64, max_length=64)


class JobStore:
    def __init__(self, root: Path):
        self.root = root / "jobs"
        self.index_path = self.root / "idempotency.json"
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def get(self, job_id: str) -> dict[str, Any] | None:
        if not job_id or any(character not in "0123456789abcdef-" for character in job_id):
            return None
        path = self.root / f"{job_id}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest persisted jobs so the UI survives a refresh."""
        self._ensure()
        recent: list[dict[str, Any]] = []
        with self._lock:
            for path in self.root.glob("*.json"):
                if path == self.index_path:
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and payload.get("job_id"):
                    recent.append(payload)
        recent.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return recent[: max(0, min(limit, 100))]

    def create(
        self,
        operation: str,
        request_payload: dict[str, Any],
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        self._ensure()
        fingerprint = canonical_hash(
            {"operation": operation, "request": request_payload}
        )
        with self._lock:
            index = (
                json.loads(self.index_path.read_text(encoding="utf-8"))
                if self.index_path.is_file()
                else {}
            )
            if idempotency_key:
                key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                existing = index.get(key_hash)
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise SnapshotConflict(
                            "Idempotency-Key was already used with a different request"
                        )
                    job = self.get(existing["job_id"])
                    if job is not None:
                        return job, False
            now = datetime.now(timezone.utc).isoformat()
            job = {
                "job_id": str(uuid4()),
                "operation": operation,
                "status": "running",
                "request": request_payload,
                "created_at": now,
                "started_at": now,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._write(self.root / f"{job['job_id']}.json", job)
            if idempotency_key:
                index[key_hash] = {
                    "job_id": job["job_id"],
                    "fingerprint": fingerprint,
                }
                self._write(self.index_path, index)
            return job, True

    def finish(
        self,
        job: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            updated = {
                **job,
                "status": "failed" if error else "succeeded",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "result": result,
                "error": error,
            }
            self._write(self.root / f"{job['job_id']}.json", updated)
            return updated


def require_control_token(authorization: str | None = Header(None)) -> None:
    configured = os.environ.get("AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN", "")
    if not configured:
        return
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.strip(), configured
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid observability control bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_app(
    controller: InventoryController | None = None,
    config: ControlConfig | None = None,
) -> FastAPI:
    inventory = controller or InventoryController(config)
    jobs = JobStore(inventory.root)
    app = FastAPI(
        title="AgentsWatch Observability Control Adapter",
        version=__version__,
    )

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        state = inventory.status()
        return {"status": "ok", "active_revision_id": state["active_revision_id"]}

    @app.get(
        "/api/v1/inventory/status",
        dependencies=[Depends(require_control_token)],
    )
    def inventory_status() -> dict[str, Any]:
        return {**inventory.status(), "jobs": jobs.list_recent()}

    @app.get(
        "/metrics",
        dependencies=[Depends(require_control_token)],
        response_class=PlainTextResponse,
    )
    def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            inventory.metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post(
        "/api/v1/inventory/sync:preview",
        dependencies=[Depends(require_control_token)],
    )
    def preview(request: SyncRequest | None = None) -> dict[str, Any]:
        request = request or SyncRequest()
        try:
            return inventory.preview(request.source, request.expected_snapshot_hash)
        except SnapshotConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/v1/inventory/sync",
        dependencies=[Depends(require_control_token)],
    )
    def sync(
        request: SyncRequest | None = None,
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        request = request or SyncRequest()
        request_payload = request.model_dump()
        try:
            job, created = jobs.create("inventory.sync", request_payload, idempotency_key)
            if not created:
                return job
            result = inventory.sync(request.source, request.expected_snapshot_hash)
            return jobs.finish(job, result=result)
        except SnapshotConflict as exc:
            if "job" in locals() and created:
                jobs.finish(job, error=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            if "job" in locals() and created:
                jobs.finish(job, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v1/jobs/{job_id}",
        dependencies=[Depends(require_control_token)],
    )
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.post(
        "/api/v1/inventory/rollback/{revision_id}",
        dependencies=[Depends(require_control_token)],
    )
    def rollback(
        revision_id: str,
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        request_payload = {"revision_id": revision_id}
        try:
            job, created = jobs.create(
                "inventory.rollback", request_payload, idempotency_key
            )
            if not created:
                return job
            result = inventory.rollback(revision_id)
            return jobs.finish(job, result=result)
        except SnapshotConflict as exc:
            if "job" in locals() and created:
                jobs.finish(job, error=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RevisionNotFound as exc:
            if "job" in locals() and created:
                jobs.finish(job, error=str(exc))
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            if "job" in locals() and created:
                jobs.finish(job, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
