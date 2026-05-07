import { useEffect, useRef, useState } from "react";
import { ApiError, api, type PluginInfo } from "../api";

export default function PluginsPage() {
  const [items, setItems] = useState<PluginInfo[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  async function refresh() {
    try {
      setItems(await api.get<PluginInfo[]>("/api/plugins?refresh=true"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function upload(file: File) {
    setBusy(true);
    setErr(null);
    try {
      await api.upload<PluginInfo>("/api/plugins", file);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function toggle(slug: string, enabled: boolean) {
    try {
      await api.post(`/api/plugins/${slug}/enabled`, { enabled });
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function destroy(slug: string) {
    if (!confirm(`Remove plugin "${slug}"? Files will be deleted from disk.`)) {
      return;
    }
    try {
      await api.del(`/api/plugins/${slug}`);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-wide">Plugins</h1>
          <p className="text-sm text-muted">
            Drop a <code>.zip</code> with a <code>plugin.json</code> manifest, or
            a single <code>.py</code> file. The runtime that calls{" "}
            <code>on_message</code> / <code>on_tick</code> ships in a future
            iteration; for now the panel just installs and lists them.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".zip,.py,application/zip,application/x-zip-compressed,text/x-python"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
            }}
          />
          <button
            className="btn-primary"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
          >
            {busy ? "Uploading…" : "Upload plugin"}
          </button>
          <button className="btn-ghost" onClick={refresh}>
            Rescan
          </button>
        </div>
      </header>
      {err && <div className="card-tight text-sm text-danger">{err}</div>}
      <section className="space-y-3">
        {items === null ? (
          <div className="card text-sm text-muted">Loading…</div>
        ) : items.length === 0 ? (
          <div className="card text-sm text-muted">
            No plugins installed yet. Use the upload button above.
          </div>
        ) : (
          items.map((p) => (
            <div
              key={p.slug}
              className="card flex flex-col gap-3 md:flex-row md:items-center md:justify-between"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-lg font-semibold">{p.name}</h3>
                  <span className="chip">{p.slug}</span>
                  <span className="chip">v{p.version}</span>
                  {p.author && <span className="chip">by {p.author}</span>}
                  <span className="chip">
                    {p.enabled ? "enabled" : "disabled"}
                  </span>
                </div>
                {p.description && (
                  <div className="mt-2 text-sm text-ink2">{p.description}</div>
                )}
                {p.error && (
                  <div className="mt-1 text-xs text-danger">{p.error}</div>
                )}
                {p.files.length > 0 && (
                  <details className="mt-2 text-xs text-muted">
                    <summary className="cursor-pointer hover:text-ink">
                      Files ({p.files.length})
                    </summary>
                    <ul className="mt-2 ml-3 list-disc font-mono">
                      {p.files.map((f) => (
                        <li key={f}>{f}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  className="btn-ghost"
                  onClick={() => void toggle(p.slug, !p.enabled)}
                >
                  {p.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  className="btn-danger"
                  onClick={() => void destroy(p.slug)}
                >
                  Remove
                </button>
              </div>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
