import { useState } from "react";
import { ApiError, api } from "../api";

type Props = { onDone: () => void };

export default function Setup({ onDone }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (password !== confirm) {
      setErr("Passwords don't match");
      return;
    }
    if (password.length < 8) {
      setErr("Password must be at least 8 characters");
      return;
    }
    setBusy(true);
    try {
      await api.post("/api/auth/setup", { username, password });
      onDone();
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
          <h1 className="text-2xl font-semibold tracking-wide">Welcome</h1>
          <p className="text-sm text-muted">
            Create the local admin account. This password protects your saved
            FunPay credentials on this machine.
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
            minLength={2}
          />
        </div>
        <div>
          <label className="label">Password</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            required
            minLength={8}
          />
        </div>
        <div>
          <label className="label">Confirm password</label>
          <input
            className="input"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
            minLength={8}
          />
        </div>
        {err && <div className="text-sm text-danger">{err}</div>}
        <button className="btn-primary w-full" disabled={busy}>
          {busy ? "Creating…" : "Create admin"}
        </button>
      </form>
    </div>
  );
}
