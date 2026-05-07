# Plugin subsystem

The MVP **does not ship with any plugins**. This directory only contains the
interfaces that future plugins must implement.

## What a plugin will look like

```python
# plugins-installed/auto_lift/__init__.py
from fastapi import APIRouter

from app.plugins import Plugin, PluginManifest


class AutoLiftPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(
            PluginManifest(
                slug="auto_lift",
                name="Auto-lift",
                version="0.1.0",
                description="Periodically re-lifts your offers to the top.",
            )
        )

    def routes(self) -> APIRouter | None:
        r = APIRouter()
        # ... custom HTTP routes for the plugin UI
        return r


PLUGIN = AutoLiftPlugin()
```

## Lifecycle hooks (see `app/plugins/base.py`)

* `on_load` / `on_unload` — process-wide.
* `on_account_attach` / `on_account_detach` — per FunPay account.
* `on_tick` — scheduled work (rate-limited per account by the host).
* `on_message` — incoming buyer messages.

## Why empty?

The current iteration is an MVP focused on:

1. Securely managing many FunPay accounts (encrypted secrets, per-account proxy
   and user-agent).
2. Reading and sending messages from the local web panel.
3. Defining the contract that future plugins (auto-lift, auto-deliver,
   dashboards, seller analytics) will plug into.

Future iterations will add:
* Background scheduler driving `on_tick` and `on_message`.
* Plugin discovery via Python entry-points and `plugins-installed/<slug>/`.
* CSP-sandboxed iframe widgets for plugin dashboards.
