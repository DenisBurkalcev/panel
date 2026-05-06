"""Filesystem-backed plugin registry.

The MVP doesn't *execute* plugins yet (the runtime that calls `on_message` /
`on_tick` lands in a later iteration). This registry handles the operator
side: scan `plugins-installed/<slug>/` for installed plugins, install new
plugins from an uploaded `.py` or `.zip` file, and toggle their `enabled`
flag.

A plugin lives as a directory containing at minimum:

    plugins-installed/
        <slug>/
            plugin.json    # manifest (slug, name, version, description, ...)
            main.py        # entry-point exposing `PLUGIN: Plugin` (loaded later)
            <other files...>

A single uploaded `.py` file is auto-wrapped: the file is renamed `main.py`
and a stub `plugin.json` is generated using the file basename as `slug`.

Security:
- Slugs must match `^[a-z][a-z0-9_-]{1,40}$`.
- Zip extraction strips path traversal (`..`, absolute paths, symlinks).
- Only files matching an allowlist of extensions are accepted.
- Total decompressed size and per-file size are capped (zip-bomb defence).
- Plugin code is **never** imported at install time. It is only loaded
  during normal startup discovery, which the operator can disable.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock

from app.config import PLUGINS_INSTALLED_DIR

log = logging.getLogger("funpay.plugins")

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")

_ALLOWED_EXTS = {
    ".py",
    ".json",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".js",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".toml",
    ".yaml",
    ".yml",
}

_MAX_PLUGIN_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB compressed
_MAX_DECOMPRESSED_BYTES = 25 * 1024 * 1024  # 25 MB decompressed
_MAX_FILES_PER_PLUGIN = 200


class PluginInstallError(ValueError):
    """Raised when an uploaded plugin fails validation."""


@dataclass
class PluginInfo:
    slug: str
    name: str
    version: str
    description: str = ""
    author: str | None = None
    enabled: bool = True
    error: str | None = None
    files: list[str] = field(default_factory=list)


class PluginRegistry:
    """Tracks installed plugins on disk."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or PLUGINS_INSTALLED_DIR
        self._lock = RLock()
        self._plugins: dict[str, PluginInfo] = {}

    @property
    def root(self) -> Path:
        return self._root

    def all(self) -> list[PluginInfo]:
        with self._lock:
            return sorted(self._plugins.values(), key=lambda p: p.slug)

    def get(self, slug: str) -> PluginInfo | None:
        with self._lock:
            return self._plugins.get(slug)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> list[PluginInfo]:
        """Scan the plugins directory and refresh the in-memory map."""
        with self._lock:
            self._plugins.clear()
            if not self._root.exists():
                return []
            for entry in sorted(self._root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                info = self._load_info(entry)
                self._plugins[info.slug] = info
            return list(self._plugins.values())

    def _load_info(self, plugin_dir: Path) -> PluginInfo:
        slug = plugin_dir.name
        manifest_path = plugin_dir / "plugin.json"
        files = sorted(
            str(p.relative_to(plugin_dir).as_posix())
            for p in plugin_dir.rglob("*")
            if p.is_file()
        )
        if not manifest_path.exists():
            return PluginInfo(
                slug=slug,
                name=slug,
                version="0.0.0",
                description="",
                enabled=False,
                error="plugin.json is missing",
                files=files,
            )
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return PluginInfo(
                slug=slug,
                name=slug,
                version="0.0.0",
                description="",
                enabled=False,
                error=f"Invalid plugin.json: {exc}",
                files=files,
            )
        normalised_slug = str(data.get("slug") or slug)
        if not _SLUG_RE.match(normalised_slug):
            return PluginInfo(
                slug=slug,
                name=str(data.get("name", slug)),
                version=str(data.get("version", "0.0.0")),
                description=str(data.get("description", "")),
                author=data.get("author"),
                enabled=False,
                error=f"Invalid slug '{normalised_slug}'",
                files=files,
            )
        return PluginInfo(
            slug=normalised_slug,
            name=str(data.get("name") or normalised_slug),
            version=str(data.get("version") or "0.0.0"),
            description=str(data.get("description") or ""),
            author=data.get("author"),
            enabled=bool(data.get("enabled", True)),
            files=files,
        )

    # ------------------------------------------------------------------
    # Install / remove / enable
    # ------------------------------------------------------------------

    def install(self, *, filename: str, blob: bytes) -> PluginInfo:
        """Install a plugin from an uploaded file (`.py` or `.zip`)."""
        if len(blob) == 0:
            raise PluginInstallError("Empty upload")
        if len(blob) > _MAX_PLUGIN_SIZE_BYTES:
            raise PluginInstallError("Plugin upload exceeds 5 MB limit")
        suffix = Path(filename).suffix.lower()
        if suffix == ".py":
            slug = self._slug_from_filename(filename)
            return self._install_single_py(slug, blob)
        if suffix == ".zip":
            return self._install_zip(blob, fallback_slug=self._slug_from_filename(filename))
        raise PluginInstallError("Unsupported plugin format (allowed: .py, .zip)")

    def remove(self, slug: str) -> None:
        with self._lock:
            if not _SLUG_RE.match(slug):
                raise PluginInstallError("Invalid slug")
            target = (self._root / slug).resolve()
            if not target.exists() or not _is_within(self._root.resolve(), target):
                raise PluginInstallError("Plugin not found")
            shutil.rmtree(target)
            self._plugins.pop(slug, None)

    def set_enabled(self, slug: str, enabled: bool) -> PluginInfo:
        with self._lock:
            if not _SLUG_RE.match(slug):
                raise PluginInstallError("Invalid slug")
            target = (self._root / slug).resolve()
            if not target.exists() or not _is_within(self._root.resolve(), target):
                raise PluginInstallError("Plugin not found")
            manifest_path = target / "plugin.json"
            data: dict[str, object] = {}
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except ValueError:
                    data = {}
            data["enabled"] = enabled
            manifest_path.write_text(
                json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
            )
            info = self._load_info(target)
            self._plugins[info.slug] = info
            return info

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _install_single_py(self, slug: str, blob: bytes) -> PluginInfo:
        if len(blob) > _MAX_DECOMPRESSED_BYTES:
            raise PluginInstallError("Plugin file too large")
        # Validate that it's at least decodable as UTF-8 source.
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PluginInstallError("Plugin source is not valid UTF-8") from exc

        with self._lock:
            target = self._root / slug
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
            (target / "main.py").write_bytes(blob)
            manifest = {
                "slug": slug,
                "name": slug,
                "version": "0.0.1",
                "description": "Single-file plugin (auto-generated manifest).",
                "enabled": True,
            }
            (target / "plugin.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
            info = self._load_info(target)
            self._plugins[info.slug] = info
            return info

    def _install_zip(self, blob: bytes, *, fallback_slug: str) -> PluginInfo:
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile as exc:
            raise PluginInstallError("File is not a valid zip archive") from exc

        with zf:
            members = zf.infolist()
            if len(members) == 0:
                raise PluginInstallError("Empty zip archive")
            if len(members) > _MAX_FILES_PER_PLUGIN:
                raise PluginInstallError("Zip archive contains too many files")

            # Strip a single common top-level directory if any.
            common_root = _common_top_dir([m.filename for m in members])
            decompressed_bytes = 0
            entries: list[tuple[str, bytes]] = []
            for member in members:
                if member.is_dir():
                    continue
                rel = member.filename
                if common_root:
                    rel = rel[len(common_root) :]
                rel = rel.lstrip("/").replace("\\", "/")
                if not rel:
                    continue
                if ".." in Path(rel).parts:
                    raise PluginInstallError(f"Refusing path traversal in {member.filename!r}")
                if Path(rel).is_absolute():
                    raise PluginInstallError(f"Refusing absolute path {member.filename!r}")
                if member.file_size > _MAX_DECOMPRESSED_BYTES:
                    raise PluginInstallError(f"File {rel!r} too large")
                decompressed_bytes += member.file_size
                if decompressed_bytes > _MAX_DECOMPRESSED_BYTES:
                    raise PluginInstallError("Decompressed plugin exceeds size limit")
                ext = Path(rel).suffix.lower()
                if ext and ext not in _ALLOWED_EXTS:
                    raise PluginInstallError(f"File extension {ext!r} not allowed")
                with zf.open(member) as fh:
                    entries.append((rel, fh.read()))

            manifest_blob = next((b for n, b in entries if n == "plugin.json"), None)
            if manifest_blob is None:
                # Fallback: synthesize a manifest for the upload.
                slug = fallback_slug
                manifest = {
                    "slug": slug,
                    "name": slug,
                    "version": "0.0.1",
                    "description": "Plugin (auto-generated manifest).",
                    "enabled": True,
                }
                entries.append(
                    (
                        "plugin.json",
                        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode(
                            "utf-8"
                        ),
                    )
                )
            else:
                try:
                    parsed = json.loads(manifest_blob.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise PluginInstallError(f"Invalid plugin.json: {exc}") from exc
                slug = str(parsed.get("slug") or fallback_slug)
                if not _SLUG_RE.match(slug):
                    raise PluginInstallError(f"Invalid plugin slug {slug!r}")

            with self._lock:
                target = self._root / slug
                if target.exists():
                    shutil.rmtree(target)
                target.mkdir(parents=True, exist_ok=True)
                for rel, data in entries:
                    out = target / rel
                    if not _is_within(target.resolve(), out.resolve().parent):
                        raise PluginInstallError(f"Refusing path escape in {rel!r}")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
                info = self._load_info(target)
                self._plugins[info.slug] = info
                return info

    @staticmethod
    def _slug_from_filename(filename: str) -> str:
        stem = Path(filename).stem.lower()
        slug = re.sub(r"[^a-z0-9_-]+", "-", stem).strip("-")
        if not slug:
            raise PluginInstallError("Could not derive a slug from the file name")
        if not _SLUG_RE.match(slug):
            raise PluginInstallError(f"Invalid slug {slug!r}")
        return slug


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _common_top_dir(names: list[str]) -> str | None:
    if not names:
        return None
    first = names[0].split("/")[0]
    if not first:
        return None
    prefix = first + "/"
    if all(n.startswith(prefix) for n in names):
        return prefix
    return None


_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        try:
            _registry.discover()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Initial plugin discovery failed: %s", exc)
    return _registry


# Compat alias for older imports.
get_registry = get_plugin_registry


def _info_to_dict(info: PluginInfo) -> dict[str, object]:
    return asdict(info)
