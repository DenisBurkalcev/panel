import { useState } from "react";
import { ApiError, api } from "../api";

type Props = { onLoggedIn: () => void };

export default function Login({ onLoggedIn }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api.post("/api/auth/login", { username, password });
      onLoggedIn();
    } catch (e) {
      if (e instanceof ApiError) setErr(e.detail);
      else setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="card w-full max-w-md space-y-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-wide">Sign in</h1>
          <p className="text-sm text-muted">
            FunPay Killer · local panel for managing FunPay accounts
          </p>
        </div>
        <div>
          <label className="label">Username</label>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </div>
        <div>
          <label className="label">Password</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>
        {err && <div className="text-sm text-danger">{err}</div>}
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
