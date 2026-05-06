"""Tests for the plugin registry: zip extraction safety + happy path."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest


def _build_zip(entries: dict[str, bytes], *, symlink: tuple[str, str] | None = None) -> bytes:
    """Pack `{name: content}` into a zip blob.

    If `symlink` is given as `(name, target)`, the entry is added with the
    unix mode bits that mark it as a symbolic link (so the registry has a
    real-world hostile zip to reject).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
        if symlink is not None:
            name, target = symlink
            info = zipfile.ZipInfo(name)
            info.create_system = 3  # unix
            # 0o120000 = S_IFLNK; left-shift into the upper 16 bits.
            info.external_attr = (0o120000 | 0o777) << 16
            zf.writestr(info, target)
    return buf.getvalue()


@pytest.mark.usefixtures("app_env")
def test_install_simple_zip(tmp_path: Path) -> None:
    from app.plugins.registry import PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    blob = _build_zip(
        {
            "plugin.json": json.dumps(
                {"slug": "hello", "name": "Hello", "version": "0.1.0"}
            ).encode("utf-8"),
            "main.py": b"PLUGIN = None\n",
        }
    )
    info = reg.install(filename="hello.zip", blob=blob)
    assert info.slug == "hello"
    assert info.error is None
    assert "main.py" in info.files


@pytest.mark.usefixtures("app_env")
def test_install_rejects_path_traversal(tmp_path: Path) -> None:
    from app.plugins.registry import PluginInstallError, PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    blob = _build_zip(
        {
            "../escape.py": b"print('boom')\n",
            "plugin.json": b"{}",
        }
    )
    with pytest.raises(PluginInstallError):
        reg.install(filename="evil.zip", blob=blob)


@pytest.mark.usefixtures("app_env")
def test_install_strips_leading_slashes(tmp_path: Path) -> None:
    """Leading-slash entries (`/etc/passwd`) are normalised, not rejected.

    The lstrip step ensures `tmp_path/<slug>/etc/passwd` instead of escaping
    the plugin dir. This is safe and matches what `unzip(1)` does too.
    """
    from app.plugins.registry import PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    blob = _build_zip(
        {
            "/main.py": b"x = 1\n",
            "plugin.json": json.dumps({"slug": "tricky"}).encode("utf-8"),
        }
    )
    info = reg.install(filename="tricky.zip", blob=blob)
    plugin_dir = (tmp_path / info.slug).resolve()
    for f in plugin_dir.rglob("*"):
        if f.is_file():
            f.relative_to(plugin_dir)  # raises if outside


@pytest.mark.usefixtures("app_env")
def test_install_rejects_symlink(tmp_path: Path) -> None:
    from app.plugins.registry import PluginInstallError, PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    blob = _build_zip(
        {"plugin.json": b"{}", "main.py": b"x = 1\n"},
        symlink=("link", "/etc/passwd"),
    )
    with pytest.raises(PluginInstallError):
        reg.install(filename="evil.zip", blob=blob)


@pytest.mark.usefixtures("app_env")
def test_install_rejects_disallowed_extension(tmp_path: Path) -> None:
    from app.plugins.registry import PluginInstallError, PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    blob = _build_zip({"main.exe": b"MZ", "plugin.json": b"{}"})
    with pytest.raises(PluginInstallError):
        reg.install(filename="evil.zip", blob=blob)


@pytest.mark.usefixtures("app_env")
def test_install_single_py(tmp_path: Path) -> None:
    from app.plugins.registry import PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    info = reg.install(filename="my-plugin.py", blob=b"PLUGIN = None\n")
    assert info.slug == "my-plugin"
    assert info.error is None
    assert "main.py" in info.files
    assert "plugin.json" in info.files


@pytest.mark.usefixtures("app_env")
def test_set_enabled_round_trip(tmp_path: Path) -> None:
    from app.plugins.registry import PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    reg.install(filename="hello.py", blob=b"PLUGIN = None\n")
    info = reg.set_enabled("hello", False)
    assert info.enabled is False
    info = reg.set_enabled("hello", True)
    assert info.enabled is True


@pytest.mark.usefixtures("app_env")
def test_remove(tmp_path: Path) -> None:
    from app.plugins.registry import PluginRegistry

    reg = PluginRegistry(root=tmp_path)
    reg.install(filename="hello.py", blob=b"PLUGIN = None\n")
    assert reg.get("hello") is not None
    reg.remove("hello")
    assert reg.get("hello") is None
