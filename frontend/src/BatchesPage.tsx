import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Pause,
  Play,
  PlayCircle,
  Plus,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { api, Batch, BatchSource, Job, RepeatMode, Status, Workspace } from './api';

const intervalLabels: Record<string, string> = {
  hour: 'Every hour',
  day: 'Every day',
  week: 'Every week',
};
const reviewReasonLabels: Record<string, string> = {
  below_threshold: 'Below auto-apply threshold',
  unsupported_ats: 'ATS not supported for auto-apply',
  no_resume_file: 'No resume PDF on file',
  custom_questions: 'Form has custom questions',
  form_error: "Couldn't confirm submission",
};

function scheduleSummary(batch: Batch): string {
  const start = new Date(batch.start_at).toLocaleString();
  if (!batch.interval_unit) return `One-time · ${start}`;
  const cadence = intervalLabels[batch.interval_unit] ?? batch.interval_unit;
  const repeat =
    batch.repeat_mode === 'indefinite' ? 'until paused' : `${batch.run_limit ?? 1} run(s)`;
  return `${cadence}, starting ${start} · ${repeat}`;
}

export default function BatchesPage() {
  const [formOpen, setFormOpen] = useState(false);
  const batchesQ = useQuery({ queryKey: ['batches'], queryFn: api.batches });
  const workspacesQ = useQuery({ queryKey: ['workspaces'], queryFn: api.workspaces });

  return (
    <main className="mx-auto w-full max-w-[1500px] flex-1 space-y-7 px-5 py-10 lg:px-10">
      <section className="flex flex-wrap items-end justify-between gap-4 border-b border-fg/15 pb-7">
        <div>
          <p className="eyebrow mb-3 w-fit">Automation</p>
          <h1 className="font-display text-4xl font-extrabold tracking-[-0.045em] sm:text-5xl">
            Batches.
          </h1>
          <p className="mt-3 max-w-xl text-fg/65">
            Schedule unattended discovery for one workspace. Jobs at or above its auto-apply
            threshold get submitted automatically through supported ATS platforms; everything else
            lands in your review queue below.
          </p>
        </div>
        <button className="btn btn-dark" onClick={() => setFormOpen(true)}>
          <Plus size={16} /> Create batch
        </button>
      </section>
      {batchesQ.isLoading ? (
        <div className="p-12 text-center text-fg/65">Loading batches…</div>
      ) : batchesQ.data?.length ? (
        <div className="space-y-4">
          {batchesQ.data.map((batch) => (
            <BatchCard key={batch.id} batch={batch} />
          ))}
        </div>
      ) : (
        <div className="card p-16 text-center">
          <PlayCircle className="mx-auto mb-3 text-fg/65" size={40} />
          <p className="font-display font-bold">No batches yet</p>
          <p className="mt-1 text-sm text-fg/65">
            Create one to auto-discover — and, above your threshold, auto-apply — on a schedule.
          </p>
        </div>
      )}
      {formOpen && (
        <CreateBatchDialog workspaces={workspacesQ.data ?? []} onClose={() => setFormOpen(false)} />
      )}
    </main>
  );
}

function BatchCard({ batch }: { batch: Batch }) {
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();
  const toggleM = useMutation({
    mutationFn: () => api.setBatchStatus(batch.id, batch.status === 'active' ? 'paused' : 'active'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['batches'] }),
  });
  const runNowM = useMutation({ mutationFn: () => api.runBatchNow(batch.id) });
  const deleteM = useMutation({
    mutationFn: () => api.deleteBatch(batch.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['batches'] }),
  });
  const runsQ = useQuery({
    queryKey: ['batch-runs', batch.id],
    queryFn: () => api.batchRuns(batch.id),
    enabled: expanded,
  });
  const reviewQ = useQuery({
    queryKey: ['jobs', batch.workspace_id, 'needs_review'],
    queryFn: () =>
      api.jobs(batch.workspace_id, new URLSearchParams({ auto_apply_state: 'needs_review' })),
    enabled: expanded,
  });
  const statusPill: Record<Batch['status'], string> = {
    active: 'bg-accent/10 text-accent',
    paused: 'bg-warning/20 text-warning',
    completed: 'bg-fg/5 text-fg/65',
  };

  return (
    <article className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-4 p-5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-display font-bold">{batch.workspace_name}</h3>
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-bold capitalize ${statusPill[batch.status]}`}
            >
              {batch.status}
            </span>
            <span className="rounded-full bg-fg/5 px-2.5 py-0.5 text-xs font-bold text-fg/65">
              {batch.source === 'upload' ? 'Uploaded list' : 'Search'}
            </span>
          </div>
          <p className="mt-1 text-sm text-fg/65">
            {batch.source === 'upload' ? (
              batch.query
            ) : (
              <>
                "{batch.query}" · last {batch.days}d · up to {batch.max_jobs} jobs
              </>
            )}{' '}
            · auto-apply ≥ {batch.auto_apply_threshold}
          </p>
          <p className="mono-num mt-1 text-xs text-fg/65">{scheduleSummary(batch)}</p>
          {batch.status === 'active' && batch.next_run_at && (
            <p className="mono-num text-xs text-fg/65">
              Next run: {new Date(batch.next_run_at).toLocaleString()}
            </p>
          )}
          <p className="mono-num text-xs text-fg/65">
            Runs completed: {batch.runs_completed}
            {batch.run_limit ? ` / ${batch.run_limit}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {batch.status !== 'completed' && (
            <button
              className="btn btn-outline"
              onClick={() => toggleM.mutate()}
              disabled={toggleM.isPending}
            >
              {batch.status === 'active' ? (
                <>
                  <Pause size={14} /> Pause
                </>
              ) : (
                <>
                  <Play size={14} /> Resume
                </>
              )}
            </button>
          )}
          <button
            className="btn btn-outline"
            onClick={() => runNowM.mutate()}
            disabled={runNowM.isPending}
          >
            <Zap size={14} /> Run now
          </button>
          <button
            className="btn bg-fg/5 text-fg/65 hover:bg-danger/10 hover:text-danger"
            onClick={() => deleteM.mutate()}
            disabled={deleteM.isPending}
            aria-label="Delete batch"
          >
            <Trash2 size={14} />
          </button>
          <button
            className="icon-button"
            onClick={() => setExpanded(!expanded)}
            aria-expanded={expanded}
            aria-label="Toggle batch details"
          >
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
      </div>
      {runNowM.data && (
        <p className="px-5 pb-3 text-sm text-accent">Run started — check back shortly.</p>
      )}
      {runNowM.error && <p className="px-5 pb-3 text-sm text-danger">{runNowM.error.message}</p>}
      {expanded && (
        <div className="space-y-6 border-t border-fg/10 bg-fg/[.02] p-5">
          <div>
            <h4 className="mb-3 text-xs font-bold uppercase tracking-wider text-fg/65">
              Run history
            </h4>
            {runsQ.data?.length ? (
              <div className="space-y-2">
                {runsQ.data.map((run) => (
                  <div
                    key={run.id}
                    className="flex flex-wrap items-center gap-3 rounded-xl bg-fg/5 px-3 py-2 text-sm"
                  >
                    <span className="mono-num font-semibold">
                      {new Date(run.started_at).toLocaleString()}
                    </span>
                    <span className={run.status === 'failed' ? 'text-danger' : 'text-fg/65'}>
                      {run.status === 'failed'
                        ? `Failed: ${run.error}`
                        : `${run.fetched} fetched · ${run.auto_applied} auto-applied · ${run.needs_review} need review`}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-fg/65">No runs yet.</p>
            )}
          </div>
          <div>
            <h4 className="mb-3 text-xs font-bold uppercase tracking-wider text-fg/65">
              Needs your review
            </h4>
            {reviewQ.data?.length ? (
              <div className="space-y-2">
                {reviewQ.data.map((job) => (
                  <ReviewRow key={job.id} job={job} workspaceId={batch.workspace_id} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-fg/65">Nothing waiting on you right now.</p>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

function ReviewRow({ job, workspaceId }: { job: Job; workspaceId: number }) {
  const qc = useQueryClient();
  const patch = useMutation({
    mutationFn: (status: Status) => api.patchJob(workspaceId, job.id, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs', workspaceId] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-fg/10 px-3 py-2 text-sm">
      <span className="mono-num rounded-lg bg-warning/15 px-2 py-1 text-xs font-bold text-warning">
        {job.score}
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          {job.title} · <span className="font-normal text-fg/65">{job.company}</span>
        </p>
        <p className="text-xs text-fg/65">
          {reviewReasonLabels[job.review_reason ?? ''] ?? job.review_reason}
        </p>
      </div>
      {job.apply_url && (
        <a className="btn btn-outline" href={job.apply_url} target="_blank" rel="noreferrer">
          <ExternalLink size={14} /> Open
        </a>
      )}
      <button
        className="btn bg-fg text-paper hover:brightness-110"
        disabled={patch.isPending}
        onClick={() => patch.mutate('applied')}
      >
        Apply
      </button>
      <button
        className="btn bg-fg/5 text-fg/65 hover:bg-fg/10"
        disabled={patch.isPending}
        onClick={() => patch.mutate('skipped')}
      >
        Skip
      </button>
    </div>
  );
}

function CreateBatchDialog({
  workspaces,
  onClose,
}: {
  workspaces: Workspace[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [workspaceId, setWorkspaceId] = useState(workspaces[0]?.id ?? 0);
  const [source, setSource] = useState<BatchSource>('search');
  const [query, setQuery] = useState('Software Engineer');
  const [days, setDays] = useState(2);
  const [maxJobs, setMaxJobs] = useState(100);
  const [scheduleKind, setScheduleKind] = useState<'once' | 'recurring'>('once');
  const [intervalUnit, setIntervalUnit] = useState<'hour' | 'day' | 'week'>('day');
  const [startAt, setStartAt] = useState(() =>
    new Date(Date.now() + 5 * 60_000).toISOString().slice(0, 16),
  );
  const [repeatMode, setRepeatMode] = useState<RepeatMode>('count');
  const [runLimit, setRunLimit] = useState(5);
  const [autoApplyThreshold, setAutoApplyThreshold] = useState(95);
  const [file, setFile] = useState<File | null>(null);

  const createM = useMutation({
    mutationFn: () =>
      source === 'upload'
        ? api.createUploadBatch({
            workspace_id: workspaceId,
            auto_apply_threshold: autoApplyThreshold,
            start_at: new Date(startAt).toISOString(),
            file: file!,
          })
        : api.createBatch({
            workspace_id: workspaceId,
            query,
            days,
            max_jobs: maxJobs,
            interval_unit: scheduleKind === 'once' ? null : intervalUnit,
            start_at: new Date(startAt).toISOString(),
            repeat_mode: scheduleKind === 'once' ? 'count' : repeatMode,
            run_limit: scheduleKind === 'once' ? 1 : repeatMode === 'count' ? runLimit : null,
            auto_apply_threshold: autoApplyThreshold,
          }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['batches'] });
      onClose();
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!workspaceId) return;
    if (source === 'upload' && !file) return;
    createM.mutate();
  };

  if (!workspaces.length) {
    return (
      <div
        className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
        onMouseDown={(event) => event.target === event.currentTarget && onClose()}
      >
        <div className="w-full max-w-md border-2 border-fg bg-surface p-7">
          <p className="font-display font-bold">Create a workspace first</p>
          <p className="mt-2 text-sm text-fg/65">
            A batch automates one existing workspace — you'll need at least one before you can
            schedule anything.
          </p>
          <button className="btn btn-dark mt-5 w-full" onClick={onClose}>
            Got it
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        className="w-full max-w-lg border-2 border-fg bg-surface p-7"
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start justify-between">
          <h2 className="font-display text-2xl font-extrabold tracking-tight">Create batch</h2>
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={19} />
          </button>
        </div>
        <form className="mt-5 space-y-4" onSubmit={submit}>
          <label className="block text-sm font-bold">
            Workspace
            <select
              className="field mt-1 w-full"
              value={workspaceId}
              onChange={(e) => setWorkspaceId(+e.target.value)}
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
          <div>
            <p className="text-sm font-bold">Job source</p>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                className={source === 'search' ? 'btn bg-fg text-paper' : 'btn btn-outline'}
                onClick={() => setSource('search')}
              >
                Search HiringCafe
              </button>
              <button
                type="button"
                className={source === 'upload' ? 'btn bg-fg text-paper' : 'btn btn-outline'}
                onClick={() => {
                  setSource('upload');
                  setScheduleKind('once');
                }}
              >
                Upload job list
              </button>
            </div>
          </div>
          {source === 'upload' ? (
            <label className="block text-sm font-bold">
              Job list (.xlsx or .csv)
              <input
                className="field mt-1 w-full"
                type="file"
                accept=".xlsx,.csv"
                required
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <span className="mt-1 block text-xs font-normal text-fg/65">
                Needs Title, Company, and Apply URL columns — the same layout "Download Excel"
                produces. Score, if present, is reused as-is; rows already marked applied or skipped
                are imported untouched.
              </span>
            </label>
          ) : (
            <>
              <label className="block text-sm font-bold">
                Search query
                <input
                  className="field mt-1 w-full"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="text-xs font-semibold text-fg/65">
                  Past days
                  <input
                    className="field mt-1 w-full"
                    type="number"
                    min={1}
                    max={30}
                    value={days}
                    onChange={(e) => setDays(+e.target.value)}
                  />
                </label>
                <label className="text-xs font-semibold text-fg/65">
                  Maximum jobs
                  <input
                    className="field mt-1 w-full"
                    type="number"
                    min={1}
                    max={1000}
                    value={maxJobs}
                    onChange={(e) => setMaxJobs(+e.target.value)}
                  />
                </label>
              </div>
              <div>
                <p className="text-sm font-bold">When</p>
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    className={scheduleKind === 'once' ? 'btn bg-fg text-paper' : 'btn btn-outline'}
                    onClick={() => setScheduleKind('once')}
                  >
                    One-time
                  </button>
                  <button
                    type="button"
                    className={
                      scheduleKind === 'recurring' ? 'btn bg-fg text-paper' : 'btn btn-outline'
                    }
                    onClick={() => setScheduleKind('recurring')}
                  >
                    Recurring
                  </button>
                </div>
              </div>
            </>
          )}
          <label className="block text-sm font-bold">
            {scheduleKind === 'once' ? 'Run at' : 'Starting'}
            <input
              className="field mt-1 w-full"
              type="datetime-local"
              value={startAt}
              onChange={(e) => setStartAt(e.target.value)}
              required
            />
          </label>
          {source === 'search' && scheduleKind === 'recurring' && (
            <>
              <label className="block text-sm font-bold">
                Every
                <select
                  className="field mt-1 w-full"
                  value={intervalUnit}
                  onChange={(e) => setIntervalUnit(e.target.value as 'hour' | 'day' | 'week')}
                >
                  <option value="hour">Hour</option>
                  <option value="day">Day</option>
                  <option value="week">Week</option>
                </select>
              </label>
              <div>
                <p className="text-sm font-bold">Repeat</p>
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      checked={repeatMode === 'count'}
                      onChange={() => setRepeatMode('count')}
                    />
                    Exactly
                  </label>
                  <input
                    className="field w-20"
                    type="number"
                    min={1}
                    max={1000}
                    value={runLimit}
                    disabled={repeatMode !== 'count'}
                    onChange={(e) => setRunLimit(+e.target.value)}
                  />
                  <span className="text-sm text-fg/65">times</span>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      checked={repeatMode === 'indefinite'}
                      onChange={() => setRepeatMode('indefinite')}
                    />
                    Until I pause it
                  </label>
                </div>
              </div>
            </>
          )}
          <label className="block text-sm font-bold">
            Auto-apply threshold ({autoApplyThreshold})
            <input
              className="mt-2 w-full accent-accent"
              type="range"
              min={0}
              max={100}
              value={autoApplyThreshold}
              onChange={(e) => setAutoApplyThreshold(+e.target.value)}
            />
            <span className="mt-1 block text-xs font-normal text-fg/65">
              Only jobs at or above this score get submitted automatically through Greenhouse;
              everything else lands in your review queue.
            </span>
          </label>
          {createM.error && <p className="text-sm text-danger">{createM.error.message}</p>}
          <button
            className="btn btn-dark w-full py-3"
            type="submit"
            disabled={createM.isPending || (source === 'upload' && !file)}
          >
            {createM.isPending ? 'Creating…' : 'Create batch'}
          </button>
        </form>
      </section>
    </div>
  );
}
