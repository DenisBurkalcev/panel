import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, ApiError, type Me, type SetupStatus } from "./api";
import Login from "./pages/Login";
import Setup from "./pages/Setup";
import Dashboard from "./pages/Dashboard";
import Accounts from "./pages/Accounts";
import Chats from "./pages/Chats";
import Plugins from "./pages/Plugins";
import Layout from "./components/Layout";

type AuthState =
  | { status: "loading" }
  | { status: "needs-setup" }
  | { status: "anon" }
  | { status: "authed"; me: Me };

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  const navigate = useNavigate();

  async function refresh() {
    try {
      const status = await api.get<SetupStatus>("/api/auth/status");
      if (!status.setup_complete) {
        setAuth({ status: "needs-setup" });
        return;
      }
      try {
        const me = await api.get<Me>("/api/auth/me");
        setAuth({ status: "authed", me });
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          setAuth({ status: "anon" });
        } else {
          setAuth({ status: "anon" });
        }
      }
    } catch {
      setAuth({ status: "anon" });
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  if (auth.status === "loading") {
    return (
      <div className="grid h-full place-items-center text-muted">Loading…</div>
    );
  }

  if (auth.status === "needs-setup") {
    return (
      <Routes>
        <Route
          path="*"
          element={
            <Setup
              onDone={async () => {
                await refresh();
                navigate("/");
              }}
            />
          }
        />
      </Routes>
    );
  }

  if (auth.status === "anon") {
    return (
      <Routes>
        <Route
          path="*"
          element={
            <Login
              onLoggedIn={async () => {
                await refresh();
                navigate("/");
              }}
            />
          }
        />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route
        path="/"
        element={
          <Layout
            me={auth.me}
            onLogout={async () => {
              try {
                await api.post("/api/auth/logout");
              } catch {
                /* ignored */
              }
              await refresh();
              navigate("/");
            }}
          />
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="accounts" element={<Accounts />} />
        <Route path="chats" element={<Chats />} />
        <Route path="plugins" element={<Plugins />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
