import { Link, NavLink, Outlet } from "react-router-dom";
import { type Me } from "../api";
import ThemeToggle from "./ThemeToggle";

type Props = {
  me: Me;
  onLogout: () => void;
};

const NAV: Array<{
  to: string;
  label: string;
  end?: boolean;
  icon: string;
}> = [
  { to: "/", label: "Home", end: true, icon: "🏠" },
  { to: "/accounts", label: "Acct", icon: "👤" },
  { to: "/chats", label: "Chat", icon: "💬" },
  { to: "/plugins", label: "Plug", icon: "🧩" },
];

export default function Layout({ me, onLogout }: Props) {
  return (
    // The primary nav is now icon-only, halved in width (≈56px), so the chat
    // page has noticeably more horizontal room for messages and the new
    // right-hand product/order sidebar.
    <div className="grid h-full min-h-screen grid-cols-[56px_1fr] gap-3 p-3">
      <aside className="neu flex flex-col items-stretch gap-2 p-2">
        <Link
          to="/"
          className="grid h-9 w-9 mx-auto place-items-center rounded-xl bg-surface text-ink shadow-neu-sm font-black text-sm"
          title={`FunPay Killer · ${me.username}`}
        >
          F
        </Link>
        <nav className="flex flex-col gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) =>
                isActive
                  ? "flex items-center justify-center rounded-xl py-2 text-base text-ink shadow-neu-pressed bg-surface"
                  : "flex items-center justify-center rounded-xl py-2 text-base text-ink2 transition-shadow hover:text-ink"
              }
            >
              <span aria-hidden>{item.icon}</span>
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col items-center gap-1.5">
          <button
            onClick={onLogout}
            className="grid h-9 w-9 place-items-center rounded-xl bg-surface text-ink2 shadow-neu-sm transition-shadow hover:text-ink hover:shadow-neu-pressed"
            title={`Log out · ${me.username}`}
            aria-label="Log out"
          >
            ⏻
          </button>
        </div>
      </aside>
      <main className="flex min-h-0 min-w-0 flex-col gap-3">
        {/* Top toolbar row reserves vertical space at the top of the main
            pane so the theme toggle never overlaps page-level controls
            (e.g. "+ Add account", "+ Install plugin", thread "Refresh"). */}
        <div className="flex items-center justify-end">
          <ThemeToggle />
        </div>
        <div className="min-h-0 min-w-0 flex-1">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
