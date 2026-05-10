import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard, FileText, Settings, LogOut, ShieldAlert,
  Bell, Search, Menu, X, Lightbulb, CheckCircle2, AlertTriangle,
  Clock, Info,
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';

// ── Static demo notifications (replace with real API when endpoint exists) ──────
const DEMO_NOTIFICATIONS = [
  {
    id: '1',
    type: 'critical',
    title: 'Critical SAE Blocked',
    body: 'DOC-GPP2KMZV6QKY flagged — life-threatening SAE classification.',
    time: '1 hr ago',
    read: false,
  },
  {
    id: '2',
    type: 'warning',
    title: 'Pending Review',
    body: 'DOC-VFW7VZF71QNL requires human review before approval.',
    time: '1 hr ago',
    read: false,
  },
  {
    id: '3',
    type: 'info',
    title: 'AI Pipeline Complete',
    body: 'Anonymisation and summarisation complete for 3 new submissions.',
    time: '2 hrs ago',
    read: true,
  },
  {
    id: '4',
    type: 'success',
    title: 'Compliance Check Passed',
    body: '5 documents auto-approved — all DPDP & ICMR checks passed.',
    time: '3 hrs ago',
    read: true,
  },
];

const notifMeta = {
  critical: { icon: AlertTriangle, color: 'text-red-500', bg: 'bg-red-500/10' },
  warning: { icon: Clock, color: 'text-orange-500', bg: 'bg-orange-500/10' },
  info: { icon: Info, color: 'text-blue-500', bg: 'bg-blue-500/10' },
  success: { icon: CheckCircle2, color: 'text-green-500', bg: 'bg-green-500/10' },
};

// ── Sidebar nav items ──────────────────────────────────────────────────────────
const navItems = [
  { name: 'Dashboard', path: '/dashboard', icon: LayoutDashboard },
  { name: 'Submissions Queue', path: '/submissions', icon: FileText },
  { name: 'Compliance Alerts', path: '/compliance', icon: ShieldAlert },
  { name: 'How It Works', path: '/explainability', icon: Lightbulb },
  { name: 'Settings', path: '/settings', icon: Settings },
];

// ── Sidebar (extracted to avoid remount on every render) ──────────────────────
interface SidebarProps {
  onClose?: () => void;
}

const Sidebar = ({ onClose }: SidebarProps) => (
  <>
    {/* Logo */}
    <div className="p-6 flex items-center gap-3 border-b border-border shrink-0">
      <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-primary-foreground font-black text-sm shadow-sm">
        S
      </div>
      <div>
        <span className="text-base font-extrabold tracking-tight text-foreground">SwasthaAI</span>
        <p className="text-[10px] text-muted-foreground -mt-0.5">CDSCO Regulatory Platform</p>
      </div>
    </div>

    {/* Nav */}
    <nav className="flex-1 px-3 py-5 space-y-1 overflow-y-auto">
      {navItems.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={onClose}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-150 ${
                isActive
                  ? 'bg-primary/10 text-primary border border-primary/20 shadow-sm'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`
            }
          >
            <Icon size={18} />
            {item.name}
          </NavLink>
        );
      })}
    </nav>

    {/* Footer */}
    <div className="p-3 border-t border-border shrink-0">
      <div className="px-3 py-2 mb-1">
        <p className="text-[10px] text-muted-foreground font-semibold uppercase tracking-widest">Backend Repo</p>
        <a
          href="https://github.com/satvik-svg/swastha-ai-hf"
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] text-primary hover:underline font-mono"
        >
          satvik-svg/swastha-ai-hf
        </a>
      </div>
      <button className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground w-full transition-colors">
        <LogOut size={18} />
        Sign Out
      </button>
    </div>
  </>
);

// ── Notification panel ────────────────────────────────────────────────────────
const NotificationPanel = ({ onClose }: { onClose: () => void }) => {
  const navigate = useNavigate();
  const [notifications, setNotifications] = useState(DEMO_NOTIFICATIONS);

  const markAllRead = () =>
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));

  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <div className="absolute right-0 top-full mt-2 w-96 bg-card border border-border rounded-2xl shadow-2xl z-50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-muted/20">
        <div className="flex items-center gap-2">
          <Bell size={16} className="text-primary" />
          <h3 className="font-bold text-foreground text-sm">Notifications</h3>
          {unreadCount > 0 && (
            <span className="text-[10px] font-bold bg-red-500 text-white px-1.5 py-0.5 rounded-full">
              {unreadCount}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {unreadCount > 0 && (
            <button
              onClick={markAllRead}
              className="text-[11px] text-primary hover:underline font-medium"
            >
              Mark all read
            </button>
          )}
          <button
            onClick={onClose}
            className="p-1 rounded-md hover:bg-muted text-muted-foreground transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Notification list */}
      <div className="max-h-96 overflow-y-auto divide-y divide-border">
        {notifications.map((notif) => {
          const meta = notifMeta[notif.type as keyof typeof notifMeta];
          const Icon = meta.icon;
          return (
            <div
              key={notif.id}
              className={`flex gap-3 px-5 py-4 transition-colors hover:bg-muted/30 cursor-pointer ${
                !notif.read ? 'bg-primary/[0.02]' : ''
              }`}
              onClick={() => {
                setNotifications((prev) =>
                  prev.map((n) => (n.id === notif.id ? { ...n, read: true } : n))
                );
                // Navigate to submissions if it mentions a DOC id
                if (notif.body.includes('DOC-')) {
                  const match = notif.body.match(/DOC-[A-Z0-9]+/);
                  if (match) {
                    navigate(`/submissions/${match[0]}`);
                    onClose();
                  }
                }
              }}
            >
              <div className={`shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${meta.bg}`}>
                <Icon size={15} className={meta.color} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-2">
                  <p className={`text-sm font-semibold text-foreground leading-tight ${!notif.read ? 'font-bold' : ''}`}>
                    {notif.title}
                  </p>
                  <span className="text-[10px] text-muted-foreground shrink-0 mt-0.5">{notif.time}</span>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{notif.body}</p>
              </div>
              {!notif.read && (
                <div className="shrink-0 w-1.5 h-1.5 rounded-full bg-primary mt-2" />
              )}
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="px-5 py-3 border-t border-border bg-muted/20 text-center">
        <button
          onClick={() => { navigate('/compliance'); onClose(); }}
          className="text-xs text-primary font-medium hover:underline"
        >
          View all compliance alerts →
        </button>
      </div>
    </div>
  );
};

// ── Layout ────────────────────────────────────────────────────────────────────

const Layout = () => {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const notifRef = useRef<HTMLDivElement>(null);

  const unreadCount = DEMO_NOTIFICATIONS.filter((n) => !n.read).length;

  // Close notification panel on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setNotifOpen(false);
      }
    };
    if (notifOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [notifOpen]);

  return (
    <div className="min-h-screen bg-background flex">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col w-64 bg-card border-r border-border shadow-sm shrink-0">
        <Sidebar />
      </aside>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            className="absolute inset-0 bg-background/80 backdrop-blur-sm"
            onClick={() => setSidebarOpen(false)}
          />
          <aside className="relative flex flex-col w-72 h-full bg-card border-r border-border shadow-xl">
            <Sidebar onClose={() => setSidebarOpen(false)} />
          </aside>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 flex flex-col min-h-screen overflow-hidden">
        {/* Header */}
        <header className="h-16 bg-card border-b border-border flex items-center justify-between px-4 sm:px-6 shrink-0 shadow-sm">
          <div className="flex items-center gap-3">
            <button
              className="md:hidden p-2 -ml-1 rounded-lg hover:bg-muted text-muted-foreground transition-colors"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              aria-label="Toggle sidebar"
            >
              {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
            <div className="relative hidden sm:block">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search doc ID, applicant..."
                className="pl-9 pr-4 py-2 bg-muted/50 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary w-64 transition-all"
              />
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Notification bell */}
            <div ref={notifRef} className="relative">
              <button
                className="relative p-2 rounded-full hover:bg-muted transition-colors text-muted-foreground"
                aria-label="Notifications"
                onClick={() => setNotifOpen((v) => !v)}
              >
                <Bell size={20} />
                {unreadCount > 0 && (
                  <span className="absolute top-1 right-1 w-2 h-2 bg-red-500 rounded-full border-2 border-card" />
                )}
              </button>
              {notifOpen && <NotificationPanel onClose={() => setNotifOpen(false)} />}
            </div>

            {/* User profile */}
            <div className="flex items-center gap-2.5">
              <div className="h-8 w-8 rounded-full bg-gradient-to-tr from-primary to-orange-400 border border-primary/20 shadow-sm flex items-center justify-center text-white text-xs font-bold">
                RC
              </div>
              <div className="hidden sm:block">
                <p className="text-xs font-semibold text-foreground leading-tight">CDSCO Reviewer</p>
                <p className="text-[10px] text-muted-foreground">reviewer@cdsco.gov.in</p>
              </div>
            </div>
          </div>
        </header>

        {/* Page content */}
        <div className="flex-1 overflow-auto p-4 sm:p-6 scroll-smooth">
          <div className="max-w-6xl mx-auto h-full">
            <Outlet />
          </div>
        </div>
      </main>
    </div>
  );
};

export default Layout;
