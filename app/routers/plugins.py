"""Plugin install / list / enable / remove endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.plugins.registry import PluginInfo as PluginRegistryInfo
from app.plugins.registry import PluginInstallError, get_plugin_registry
from app.security import get_current_user, require_csrf

# Hard cap on the upload size enforced at the HTTP layer so a malicious client
# can't stream gigabytes into the panel before the registry's own 5 MB cap
# rejects the request. We allow a small overhead (~64 KB) on top of the
# registry limit to give a clean error message rather than a connection drop.
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024 + 64 * 1024
_UPLOAD_CHUNK = 64 * 1024

router = APIRouter(
    prefix="/api/plugins",
    tags=["plugins"],
    dependencies=[Depends(get_current_user)],
)


class PluginOut(BaseModel):
    slug: str
    name: str
    version: str
    description: str
    author: str | None = None
    enabled: bool
    error: str | None = None
    files: list[str] = []


def _to_out(info: PluginRegistryInfo) -> PluginOut:
    return PluginOut(
        slug=info.slug,
        name=info.name,
        version=info.version,
        description=info.description,
        author=info.author,
        enabled=info.enabled,
        error=info.error,
        files=info.files,
    )


@router.get("", response_model=list[PluginOut])
def list_plugins(refresh: bool = False) -> list[PluginOut]:
    reg = get_plugin_registry()
    if refresh:
        reg.discover()
    return [_to_out(p) for p in reg.all()]


@router.post(
    "",
    response_model=PluginOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def install_plugin(file: UploadFile = File(...)) -> PluginOut:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Plugin upload exceeds the maximum allowed size",
            )
        chunks.append(chunk)
    blob = b"".join(chunks)
    try:
        info = get_plugin_registry().install(filename=file.filename or "plugin", blob=blob)
    except PluginInstallError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(info)


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def remove_plugin(slug: str) -> None:
    try:
        get_plugin_registry().remove(slug)
    except PluginInstallError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


class EnablePayload(BaseModel):
    enabled: bool


@router.post(
    "/{slug}/enabled",
    response_model=PluginOut,
    dependencies=[Depends(require_csrf)],
)
def set_plugin_enabled(slug: str, payload: EnablePayload) -> PluginOut:
    try:
        info = get_plugin_registry().set_enabled(slug, payload.enabled)
    except PluginInstallError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_out(info)
