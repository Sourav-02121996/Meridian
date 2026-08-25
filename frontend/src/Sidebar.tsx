import { useState } from 'react';
import {
  BarChart3,
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  FolderPlus,
  LayoutGrid,
  LogOut,
  Moon,
  Sun,
  Zap,
} from 'lucide-react';
import { Theme } from './theme';

const COLLAPSE_KEY = 'meridian-sidebar-collapsed';

export type AppView = 'workspaces' | 'workspace' | 'batches' | 'dashboard';

export default function Sidebar({
  view,
  onNavigate,
  onCreateWorkspace,
  onCreateBatch,
  email,
  theme,
  onToggleTheme,
  onLogout,
}: {
  view: AppView;
  onNavigate: (view: 'workspaces' | 'batches' | 'dashboard') => void;
  onCreateWorkspace: () => void;
  onCreateBatch: () => void;
  email: string;
  theme: Theme;
  onToggleTheme: () => void;
  onLogout: () => void;
}) {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === '1');
  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0');
      return next;
    });
  };
  const initial = (email[0] || '?').toUpperCase();

  return (
    <aside
      className={`sidebar flex shrink-0 flex-col bg-sidebar transition-[width] duration-200 ${collapsed ? 'w-[76px]' : 'w-64'}`}
    >
      <div className={`flex items-center p-4 ${collapsed ? 'flex-col gap-3' : 'justify-between'}`}>
        <span className="flex items-center gap-2 overflow-hidden">
          <img src="/favicon-512.png" alt="" className="h-8 w-8 shrink-0 rounded-lg" />
          {!collapsed && (
            <span className="truncate font-display text-lg font-extrabold text-white">
              Meridian
            </span>
          )}
        </span>
        <button
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-white/50 transition hover:bg-white/10 hover:text-white"
          onClick={toggleCollapsed}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-2">
        <SidebarGroup label="Main menu" collapsed={collapsed}>
          <SidebarItem
            icon={LayoutGrid}
            label="Workspaces"
            collapsed={collapsed}
            active={view === 'workspaces' || view === 'workspace'}
            onClick={() => onNavigate('workspaces')}
          />
          <SidebarItem
            icon={Zap}
            label="Batches"
            collapsed={collapsed}
            active={view === 'batches'}
            onClick={() => onNavigate('batches')}
          />
          <SidebarItem
            icon={BarChart3}
            label="Dashboard"
            collapsed={collapsed}
            active={view === 'dashboard'}
            onClick={() => onNavigate('dashboard')}
          />
        </SidebarGroup>
        <SidebarGroup label="Quick actions" collapsed={collapsed}>
          <SidebarItem
            icon={FolderPlus}
            label="Create new workspace"
            collapsed={collapsed}
            onClick={onCreateWorkspace}
          />
          <SidebarItem
            icon={CalendarPlus}
            label="Create batch"
            collapsed={collapsed}
            onClick={onCreateBatch}
          />
        </SidebarGroup>
      </nav>
      <div className="space-y-1 border-t border-white/10 p-3">
        <SidebarItem
          icon={theme === 'dark' ? Sun : Moon}
          label={theme === 'dark' ? 'Light mode' : 'Dark mode'}
          collapsed={collapsed}
          onClick={onToggleTheme}
        />
        <div className="flex items-center gap-3 rounded-xl px-3 py-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent text-xs font-bold text-white">
            {initial}
          </span>
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1 truncate text-sm font-semibold text-white">
                {email}
              </span>
              <button
                className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-white/50 transition hover:bg-white/10 hover:text-white"
                onClick={onLogout}
                aria-label="Log out"
                title="Log out"
              >
                <LogOut size={15} />
              </button>
            </>
          )}
        </div>
      </div>
    </aside>
  );
}

function SidebarGroup({
  label,
  collapsed,
  children,
}: {
  label: string;
  collapsed: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      {!collapsed && (
        <p className="mb-2 px-3 text-xs font-bold uppercase tracking-wider text-white/50">
          {label}
        </p>
      )}
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function SidebarItem({
  icon: Icon,
  label,
  collapsed,
  active,
  onClick,
}: {
  icon: any;
  label: string;
  collapsed: boolean;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`sidebar-item ${active ? 'sidebar-item-active' : ''} ${collapsed ? 'justify-center' : ''}`}
      onClick={onClick}
      title={collapsed ? label : undefined}
      aria-current={active ? 'page' : undefined}
    >
      <Icon size={18} />
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  );
}
