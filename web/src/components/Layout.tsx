import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router";
import {
  clearToken,
  getSystemStatus,
  listEvents,
  type SystemStatus,
} from "../api";
import { usePoll } from "../hooks";
import {
  BellIcon,
  BrandIcon,
  ClockIcon,
  DatabaseIcon,
  GaugeIcon,
  GearIcon,
  HomeIcon,
  ProvidersIcon,
  TasksIcon,
} from "./icons";
import { TimezoneProvider } from "./TimezoneContext";
import { ToastProvider } from "./Toast";

const NAV_GROUPS: {
  label: string;
  items: { to: string; label: string; end?: boolean; icon: ReactNode }[];
}[] = [
  { label: "Overview", items: [{ to: "/", label: "Home", end: true, icon: <HomeIcon /> }] },
  { label: "Data", items: [{ to: "/datasets", label: "Datasets", icon: <DatabaseIcon /> }] },
  {
    label: "Activity",
    items: [
      { to: "/tasks", label: "Tasks", icon: <TasksIcon /> },
      { to: "/events", label: "Events", icon: <BellIcon /> },
    ],
  },
  { label: "Automation", items: [{ to: "/cron", label: "Cron", icon: <ClockIcon /> }] },
  {
    label: "System",
    items: [
      { to: "/providers", label: "Providers", icon: <ProvidersIcon /> },
      { to: "/config", label: "Config", icon: <GearIcon /> },
      { to: "/server", label: "Server", icon: <GaugeIcon /> },
    ],
  },
];

/** Global status bar: live running-task count, unread events, connection state. */
function StatusBar({ onLogout }: { onLogout: () => void }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [unread, setUnread] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([getSystemStatus(), listEvents({ unread: true })]);
      setStatus(s);
      setUnread(e.items.filter((ev) => !ev.acknowledged).length);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  // Slow ambient cadence; individual pages poll faster on their own.
  usePoll(refresh, 10_000);

  // Browser tab: findata <version> · <workspace-name>.
  useEffect(() => {
    const base = `findata ${__APP_VERSION__}`;
    const name = status?.workspace.split("/").filter(Boolean).pop();
    document.title = name ? `${base} · ${name}` : base;
  }, [status]);

  const running = status?.running_tasks ?? 0;

  return (
    <header className="topbar">
      <div className="statusbar">
        <Link
          to="/tasks?status=active"
          className={`statusbar-item ${running > 0 ? "statusbar-live" : ""}`}
          title="active tasks"
        >
          <span className={`dot ${running > 0 ? "dot-live" : "dot-idle"}`} />
          {running} running
        </Link>
        <Link
          to="/events?unread"
          className={`statusbar-item ${(unread ?? 0) > 0 ? "statusbar-warn" : ""}`}
          title="unacknowledged events"
        >
          {unread ?? "—"} unread
        </Link>
        <span
          className={`statusbar-item ${failed ? "statusbar-warn" : ""}`}
          title={failed ? "last poll failed" : "connected"}
        >
          <span className={`dot ${failed ? "dot-error" : "dot-ok"}`} />
          {failed ? "connection lost" : "connected"}
        </span>
      </div>
      <button className="btn" onClick={onLogout}>
        Logout
      </button>
    </header>
  );
}

export default function Layout() {
  const navigate = useNavigate();
  const logout = (): void => {
    clearToken();
    navigate("/login", { replace: true });
  };

  return (
    <ToastProvider>
      <TimezoneProvider>
        <div className="app">
          <aside className="sidebar">
            <div className="brand">
              <span className="brand-mark">
                <BrandIcon />
              </span>
              findata
              <span className="brand-version">v{__APP_VERSION__}</span>
            </div>
            <nav>
              {NAV_GROUPS.map((group) => (
                <div key={group.label} className="nav-group">
                  <div className="nav-group-label">{group.label}</div>
                  {group.items.map((item) => (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      end={item.end}
                      className={({ isActive }) =>
                        isActive ? "nav-link active" : "nav-link"
                      }
                    >
                      <span className="nav-icon">{item.icon}</span>
                      {item.label}
                    </NavLink>
                  ))}
                </div>
              ))}
            </nav>
          </aside>
          <div className="main">
            <StatusBar onLogout={logout} />
            <main className="content">
              <Outlet />
            </main>
          </div>
        </div>
      </TimezoneProvider>
    </ToastProvider>
  );
}
