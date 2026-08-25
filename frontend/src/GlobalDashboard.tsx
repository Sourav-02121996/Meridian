import { useQuery } from '@tanstack/react-query';
import {
  Bot,
  BriefcaseBusiness,
  CheckCircle2,
  Layers,
  PlayCircle,
  ShieldQuestion,
} from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from './api';

const axisTick = { fontSize: 11, fill: 'rgb(var(--fg) / 0.45)' };
const tooltipStyle = {
  background: 'rgb(var(--surface))',
  border: '1px solid rgb(var(--fg) / 0.1)',
  borderRadius: 8,
  color: 'rgb(var(--fg))',
  fontSize: 13,
};

export default function GlobalDashboard() {
  const dashboardQ = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard });
  const stats = dashboardQ.data;
  const cards = [
    [Layers, 'Workspaces', stats?.workspace_count ?? 0],
    [BriefcaseBusiness, 'Total jobs', stats?.total_jobs ?? 0],
    [CheckCircle2, 'Applied (all-time)', stats?.applied_total ?? 0],
    [Bot, 'Auto-applied by batches', stats?.applied_auto_total ?? 0],
    [ShieldQuestion, 'Needs your review', stats?.needs_review_total ?? 0],
    [PlayCircle, 'Active batches', stats?.active_batches ?? 0],
  ] as const;
  return (
    <main className="mx-auto w-full max-w-[1500px] flex-1 space-y-7 px-5 py-10 lg:px-10">
      <section className="border-b border-fg/15 pb-7">
        <p className="eyebrow mb-3 w-fit">Overview</p>
        <h1 className="font-display text-4xl font-extrabold tracking-[-0.045em] sm:text-5xl">
          Dashboard.
        </h1>
        <p className="mt-3 max-w-xl text-fg/65">
          How your whole job search is going, across every workspace and batch.
        </p>
      </section>
      {dashboardQ.isLoading ? (
        <div className="p-12 text-center text-fg/65">Loading metrics…</div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            {cards.map(([Icon, label, value]) => (
              <div className="card flex items-center gap-3 p-5" key={label}>
                <div className="rounded-2xl bg-accent/10 p-3 text-accent">
                  <Icon size={20} />
                </div>
                <div>
                  <p className="text-xs text-fg/65">{label}</p>
                  <p className="mono-num text-xl font-bold">{value}</p>
                </div>
              </div>
            ))}
          </div>
          <section className="card p-5">
            <h2 className="mb-4 font-display text-xl font-bold">Progress by workspace</h2>
            {stats?.by_workspace.length ? (
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={stats.by_workspace}>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      vertical={false}
                      stroke="currentColor"
                      strokeOpacity={0.1}
                    />
                    <XAxis dataKey="name" tick={axisTick} />
                    <YAxis allowDecimals={false} tick={axisTick} />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Bar
                      name="Total jobs"
                      dataKey="total_jobs"
                      fill="#2563eb"
                      radius={[3, 3, 0, 0]}
                    />
                    <Bar name="Applied" dataKey="applied" fill="#16a34a" radius={[3, 3, 0, 0]} />
                    <Bar
                      name="Above threshold"
                      dataKey="above_threshold"
                      fill="#d89b24"
                      radius={[3, 3, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="p-8 text-center text-sm text-fg/65">
                Create a workspace and discover some jobs to see progress here.
              </p>
            )}
          </section>
          <section className="card overflow-hidden">
            <div className="border-b border-fg/10 p-5">
              <h2 className="font-display text-xl font-bold">Workspace breakdown</h2>
            </div>
            <div className="hidden grid-cols-[1.2fr_1fr_1fr_1fr_1fr] gap-3 border-b border-fg/10 bg-fg/[.03] px-5 py-2 text-xs font-bold uppercase tracking-wider text-fg/65 md:grid">
              <span>Workspace</span>
              <span>Total jobs</span>
              <span>Above threshold</span>
              <span>Applied</span>
              <span>Auto-applied</span>
            </div>
            <div className="mono-num divide-y divide-fg/10">
              {stats?.by_workspace.map((row) => (
                <div
                  key={row.workspace_id}
                  className="grid grid-cols-2 gap-3 px-5 py-4 text-sm md:grid-cols-[1.2fr_1fr_1fr_1fr_1fr]"
                >
                  <span className="font-sans font-semibold">{row.name}</span>
                  <span>{row.total_jobs}</span>
                  <span>{row.above_threshold}</span>
                  <span>{row.applied}</span>
                  <span>{row.applied_auto}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
