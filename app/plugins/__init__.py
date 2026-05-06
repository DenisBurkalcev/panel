"""Plugin subsystem.

The MVP supports installing plugins from disk (`plugins-installed/<slug>/`)
and managing their `enabled` flag from the UI. The plugin **runtime** that
calls `on_message` / `on_tick` lands in a later iteration.
"""

from app.plugins.base import Plugin, PluginContext, PluginManifest
from app.plugins.registry import (
    PluginInfo,
    PluginInstallError,
    PluginRegistry,
    get_plugin_registry,
    get_registry,
)

__all__ = [
    "Plugin",
    "PluginContext",
    "PluginInfo",
    "PluginInstallError",
    "PluginManifest",
    "PluginRegistry",
    "get_plugin_registry",
    "get_registry",
]
