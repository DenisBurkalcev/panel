import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Account, type PluginInfo } from "../api";

export default function Dashboard() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [plugins, setPlugins] = useState<PluginInfo[] | null>(null);

  useEffect(() => {
    void Promise.all([
      api.get<Account[]>("/api/accounts"),
      api.get<PluginInfo[]>("/api/plugins"),
    ])
      .then(([a, p]) => {
        setAccounts(a);
        setPlugins(p);
      })
      .catch(() => {
        setAccounts([]);
        setPlugins([]);
      });
  }, []);

  const total = accounts?.length ?? 0;
  const ok = accounts?.filter((a) => a.last_check_ok).length ?? 0;
  const enabled = accounts?.filter((a) => a.enabled).length ?? 0;
  const enabledPlugins = plugins?.filter((p) => p.enabled).length ?? 0;

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-3xl font-semibold tracking-wide">Overview</h1>
        <p className="mt-2 text-sm text-muted">
          MVP scope: secure account management, messaging, plugin file
          installation. Per-account chat caches keep the UI snappy.
        </p>
      </section>

      <section className="grid grid-cols-1 gap-5 md:grid-cols-4">
        <Stat title="Accounts" value={total} hint="Configured" />
        <Stat title="Enabled" value={enabled} hint="Active" />
        <Stat title="Healthy" value={ok} hint="Last probe OK" />
        <Stat title="Plugins" value={enabledPlugins} hint="Enabled" />
      </section>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="card">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Quick links</h2>
            <Link to="/accounts" className="btn-primary">
              Manage accounts
            </Link>
          </div>
          <ul className="space-y-2 text-sm text-ink2">
            <li>· Add a FunPay account with its own proxy and user-agent.</li>
            <li>· Open chats from the Chats tab and reply in the right pane.</li>
            <li>· Drop plugin files into the Plugins tab to install them.</li>
          </ul>
        </div>
        <div className="card">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Plugins</h2>
            <Link to="/plugins" className="btn-ghost">
              Open
            </Link>
          </div>
          {plugins === null ? (
            <div className="text-sm text-muted">Loading…</div>
          ) : plugins.length === 0 ? (
            <div className="text-sm text-muted">
              No plugins installed. Drop a <code>.zip</code> or <code>.py</code>{" "}
              in the Plugins tab.
            </div>
          ) : (
            <ul className="grid gap-2">
              {plugins.map((p) => (
                <li
                  key={p.slug}
                  className="neu-inset flex items-center justify-between p-3"
                >
                  <div>
                    <div className="font-medium">{p.name}</div>
                    <div className="text-xs text-muted">{p.description}</div>
                  </div>
                  <span className="chip">
                    {p.enabled ? "ON" : "OFF"} · {p.version}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}

function Stat({
  title,
  value,
  hint,
}: {
  title: string;
  value: number;
  hint: string;
}) {
  return (
    <div className="card">
      <div className="label">{title}</div>
      <div className="mt-1 text-4xl font-semibold tabular-nums">{value}</div>
      <div className="mt-1 text-xs text-muted">{hint}</div>
    </div>
  );
}
