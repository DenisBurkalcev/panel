"""Plugin interface for FunPay Killer.

The MVP intentionally ships *no* concrete plugins. This module pins down the
interface that future plugins (auto-lift, auto-deliver, market dashboards,
seller analytics) must implement, plus the context object the host hands to
each plugin.

A plugin is a Python module/package that exposes a top-level
`PLUGIN: Plugin` attribute (or a `register()` factory returning a `Plugin`).
The host loads plugins from `plugins-installed/<slug>/` and from any
`funpay_killer.plugins` entry-points discovered at startup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fastapi import APIRouter

    from app.models.account import Account
    from app.schemas.chat import ChatMessage


@dataclass(frozen=True)
class PluginManifest:
    slug: str  # url-safe id, must match `^[a-z][a-z0-9_-]{1,40}$`
    name: str
    version: str
    description: str
    author: str | None = None
    homepage: str | None = None
    requires_funpay_account: bool = True


class DashboardWidget(Protocol):
    """A small piece of UI a plugin contributes to the host dashboard.

    The host renders the widget by fetching the plugin's `/widget` endpoint
    inside a sandboxed iframe (CSP-restricted). For the MVP this is a forward
    declaration — the dashboard runtime is implemented in a later iteration.
    """

    title: str
    iframe_src: str


@dataclass
class PluginContext:
    """Per-account runtime context passed to plugin lifecycle hooks."""

    account: Account
    log: Callable[[str], None]
    # NOTE: the message/lift/delivery service handles will be wired in a later iteration
    # when we add background workers. For now plugins receive a context-aware logger and
    # the (read-only) Account model.


class Plugin(ABC):
    """Abstract base for FunPay Killer plugins."""

    manifest: PluginManifest

    def __init__(self, manifest: PluginManifest) -> None:
        self.manifest = manifest

    # ---- Lifecycle -------------------------------------------------------

    async def on_load(self) -> None:
        """Called once when the host loads the plugin (process start)."""
        return None

    async def on_unload(self) -> None:
        """Called once when the host shuts down or the plugin is disabled."""
        return None

    async def on_account_attach(self, ctx: PluginContext) -> None:
        """Called when an account becomes available (added or enabled)."""
        return None

    async def on_account_detach(self, ctx: PluginContext) -> None:
        """Called when an account is disabled or removed."""
        return None

    # ---- Periodic / event hooks ------------------------------------------

    async def on_tick(self, ctx: PluginContext) -> None:
        """Called once per scheduler tick (e.g. every minute) per account."""
        return None

    async def on_message(self, ctx: PluginContext, message: ChatMessage) -> None:
        """Called for each new incoming message from a buyer."""
        return None

    # ---- HTTP / dashboard surface ---------------------------------------

    @abstractmethod
    def routes(self) -> APIRouter | None:
        """Return an APIRouter to mount under `/api/plugins/<slug>/`, or None."""

    def widgets(self) -> list[DashboardWidget]:
        return []


PluginFactory = Callable[[], Plugin] | Callable[[], Awaitable[Plugin]]


def make_log(prefix: str) -> Callable[[str], None]:
    import logging

    logger = logging.getLogger(f"plugin.{prefix}")
    return lambda msg: logger.info(msg)


__all__ = [
    "DashboardWidget",
    "Plugin",
    "PluginContext",
    "PluginFactory",
    "PluginManifest",
    "make_log",
]


def _typed_any() -> Any:  # pragma: no cover - silence "unused import" complaints
    return None
