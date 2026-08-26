import { FormEvent, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  BarChart3,
  BriefcaseBusiness,
  ChevronDown,
  ChevronUp,
  Download,
  ExternalLink,
  FileText,
  Search,
  Sparkles,
  Target,
  Upload,
  UserRound,
  Users,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api, Job, Status } from './api';

const statusLabels: Record<Status, string> = {
  discovered: 'Discovered',
  to_apply: 'To apply',
  applied: 'Applied',
  skipped: 'Skipped',
};
const reviewReasonLabels: Record<string, string> = {
  below_threshold: 'Below auto-apply threshold',
  unsupported_ats: 'ATS not supported for auto-apply',
  no_resume_file: 'No resume PDF on file',
  custom_questions: 'Form has custom questions',
  form_error: "Couldn't confirm submission",
};
const colors = ['#2563eb', '#d89b24', '#168f83', '#df6c55'];

function scoreTone(score: number, threshold: number) {
  return score >= threshold
    ? 'bg-sage text-white'
    : score >= Math.max(0, threshold - 17)
      ? 'bg-sun/20 text-amber-900'
      : 'bg-neutral-100 text-neutral-600';
}

function WorkspaceView({
  workspaceId,
  workspaceName,
  onBack,
}: {
  workspaceId: number;
  workspaceName: string;
  onBack: () => void;
}) {
  const qc = useQueryClient(),
    settingsQ = useQuery({
      queryKey: ['settings', workspaceId],
      queryFn: () => api.settings(workspaceId),
    }),
    statsQ = useQuery({ queryKey: ['stats', workspaceId], queryFn: () => api.stats(workspaceId) });
  const [resume, setResume] = useState(''),
    [threshold, setThreshold] = useState(82),
    [autoApplyThreshold, setAutoApplyThreshold] = useState(95),
    [status, setStatus] = useState(''),
    [minScore, setMinScore] = useState(''),
    [search, setSearch] = useState(''),
    [sort, setSort] = useState('score'),
    [order, setOrder] = useState('desc');
  useEffect(() => {
    if (settingsQ.data) {
      setResume(settingsQ.data.resume);
      setThreshold(settingsQ.data.threshold);
      setAutoApplyThreshold(settingsQ.data.auto_apply_threshold);
    }
  }, [settingsQ.data]);
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (minScore) params.set('min_score', minScore);
  if (search) params.set('q', search);
  params.set('sort', sort);
  params.set('order', order);
  const jobsQ = useQuery({
    queryKey: ['jobs', workspaceId, params.toString()],
    queryFn: () => api.jobs(workspaceId, params),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['jobs', workspaceId] });
    qc.invalidateQueries({ queryKey: ['stats', workspaceId] });
  };
  const resumeM = useMutation({
    mutationFn: (text: string) => api.saveResume(workspaceId, text),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', workspaceId] }),
  });
  const thresholdM = useMutation({
    mutationFn: (value: number) => api.saveThreshold(workspaceId, value),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings', workspaceId] });
      qc.invalidateQueries({ queryKey: ['stats', workspaceId] });
    },
  });
  const autoApplyThresholdM = useMutation({
    mutationFn: (value: number) => api.saveAutoApplyThreshold(workspaceId, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', workspaceId] }),
  });
  return (
    <main className="mx-auto w-full max-w-[1500px] flex-1 space-y-7 px-5 py-10 lg:px-10">
      <section className="border-b border-black/15 pb-7">
        <button
          className="mb-4 flex items-center gap-2 text-sm font-semibold text-black/50 hover:text-black"
          onClick={onBack}
        >
          <ArrowLeft size={16} /> All workspaces
        </button>
        <p className="eyebrow mb-3 w-fit">{workspaceName}</p>
        <h1 className="font-display text-4xl font-extrabold tracking-[-0.045em] sm:text-5xl">
          Make your next move.
        </h1>
      </section>
      <section className="grid gap-5 xl:grid-cols-[1.1fr_.9fr]">
        <ResumePanel
          workspaceId={workspaceId}
          value={resume}
          setValue={setResume}
          saving={resumeM.isPending}
          onSave={() => resumeM.mutate(resume)}
        />
        <Discover workspaceId={workspaceId} onDone={refresh} />
      </section>
      <ProfilePanel workspaceId={workspaceId} settings={settingsQ.data} />
      <div className="grid gap-5 md:grid-cols-2">
        <ThresholdCard
          icon={<Target className="text-sage" />}
          title="Your match threshold"
          hint="Jobs at or above this score are highlighted"
          value={threshold}
          setValue={setThreshold}
          onCommit={(value) => thresholdM.mutate(value)}
        />
        <ThresholdCard
          icon={<Sparkles className="text-sage" />}
          title="Auto-apply threshold"
          hint="Batches only submit automatically at or above this score"
          value={autoApplyThreshold}
          setValue={setAutoApplyThreshold}
          onCommit={(value) => autoApplyThresholdM.mutate(value)}
        />
      </div>
      <StatCards stats={statsQ.data} />
      <Charts stats={statsQ.data} />
      <section className="card overflow-hidden">
        <div className="border-b border-black/5 p-5">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-display text-xl font-bold">Job pipeline</h2>
              <p className="text-sm text-black/45">Review matches and keep your search moving.</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-black/45">
                {jobsQ.data?.length ?? 0} jobs
              </span>
              <ExportButton workspaceId={workspaceId} params={params} />
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <label className="field flex items-center gap-2">
              <Search size={16} />
              <input
                className="min-w-0 flex-1 outline-none"
                placeholder="Search roles or companies"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <select className="field" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {Object.entries(statusLabels).map(([v, l]) => (
                <option value={v} key={v}>
                  {l}
                </option>
              ))}
            </select>
            <input
              className="field"
              type="number"
              min="0"
              max="100"
              placeholder="Minimum score"
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
            />
            <select
              className="field"
              value={`${sort}-${order}`}
              onChange={(e) => {
                const [s, o] = e.target.value.split('-');
                setSort(s);
                setOrder(o);
              }}
            >
              <option value="score-desc">Highest score</option>
              <option value="score-asc">Lowest score</option>
              <option value="date-desc">Newest first</option>
              <option value="date-asc">Oldest first</option>
            </select>
          </div>
          <p className="mt-2 text-right text-xs text-black/35">
            Excel export reflects the active status, score, and search filters.
          </p>
        </div>
        {jobsQ.isLoading ? (
          <div className="p-12 text-center text-black/45">Loading your pipeline…</div>
        ) : jobsQ.isError ? (
          <ErrorBox error={jobsQ.error} />
        ) : jobsQ.data?.length ? (
          <JobTable
            jobs={jobsQ.data}
            threshold={threshold}
            workspaceId={workspaceId}
            onChanged={refresh}
          />
        ) : (
          <div className="p-16 text-center">
            <BriefcaseBusiness className="mx-auto mb-3 text-black/25" size={40} />
            <p className="font-display font-bold">No jobs here yet</p>
            <p className="mt-1 text-sm text-black/45">
              Save your resume, then discover your first batch of roles.
            </p>
          </div>
        )}
      </section>
    </main>
  );
}

function ThresholdCard({
  icon,
  title,
  hint,
  value,
  setValue,
  onCommit,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  value: number;
  setValue: (v: number) => void;
  onCommit: (v: number) => void;
}) {
  return (
    <div className="card flex flex-wrap items-center gap-5 px-5 py-4">
      {icon}
      <div>
        <p className="font-semibold">{title}</p>
        <p className="text-xs text-black/45">{hint}</p>
      </div>
      <input
        className="min-w-48 flex-1 accent-[#2563eb]"
        aria-label={title}
        type="range"
        min="0"
        max="100"
        value={value}
        onChange={(e) => setValue(+e.target.value)}
        onPointerUp={(e) => onCommit(+(e.currentTarget as HTMLInputElement).value)}
        onKeyUp={(e) => {
          if (['ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(e.key))
            onCommit(+(e.currentTarget as HTMLInputElement).value);
        }}
      />
      <span className="rounded-xl bg-sage px-3 py-1.5 font-display font-bold text-white">
        {value}
      </span>
    </div>
  );
}

function ResumePanel({
  workspaceId,
  value,
  setValue,
  saving,
  onSave,
}: {
  workspaceId: number;
  value: string;
  setValue: (v: string) => void;
  saving: boolean;
  onSave: () => void;
}) {
  const [uploadMessage, setUploadMessage] = useState('');
  const uploadM = useMutation({
    mutationFn: (file: File) => api.uploadResumePdf(workspaceId, file),
    onSuccess: (data) => {
      setValue(data.text);
      setUploadMessage(
        `${data.filename} · ${data.pages} page${data.pages === 1 ? '' : 's'} imported`,
      );
    },
  });
  const choose = (file?: File) => {
    if (file) {
      setUploadMessage('');
      uploadM.mutate(file);
    }
  };
  return (
    <section className="card p-6">
      <div className="mb-4 flex items-start gap-3">
        <div className="rounded-xl bg-sage/10 p-2 text-sage">
          <FileText size={20} />
        </div>
        <div>
          <h2 className="font-display font-bold">Your resume</h2>
          <p className="text-sm text-black/45">
            Paste text or upload a text-based PDF. Uploading a PDF also keeps the original file on
            hand so batches can attach it when auto-applying.
          </p>
        </div>
      </div>
      <label className="mb-3 flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-sage/40 bg-sage/5 px-4 py-3 text-sm font-semibold text-sage transition hover:bg-sage/10">
        <Upload size={17} />
        {uploadM.isPending ? 'Extracting PDF…' : 'Upload resume PDF'}
        <input
          className="hidden"
          type="file"
          accept="application/pdf,.pdf"
          disabled={uploadM.isPending}
          onChange={(e) => choose(e.target.files?.[0])}
        />
      </label>
      {uploadM.error && <p className="mb-3 text-sm text-red-600">{uploadM.error.message}</p>}
      {uploadMessage && (
        <p className="mb-3 text-sm font-medium text-neutral-700">{uploadMessage}</p>
      )}
      <textarea
        className="field h-36 w-full resize-none"
        placeholder="Paste your resume text here…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="mt-3 flex items-center justify-between">
        <span className="text-xs text-black/35">{value.length.toLocaleString()} characters</span>
        <button
          className="btn bg-ink text-white hover:bg-sage"
          disabled={saving || !value.trim()}
          onClick={onSave}
        >
          {saving ? 'Saving…' : 'Save resume'}
        </button>
      </div>
    </section>
  );
}

function ProfilePanel({
  workspaceId,
  settings,
}: {
  workspaceId: number;
  settings?: {
    profile_name: string;
    profile_email: string;
    profile_phone: string;
    profile_linkedin: string;
    cover_letter: string;
  };
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(''),
    [email, setEmail] = useState(''),
    [phone, setPhone] = useState(''),
    [linkedin, setLinkedin] = useState(''),
    [coverLetter, setCoverLetter] = useState('');
  useEffect(() => {
    if (settings) {
      setName(settings.profile_name);
      setEmail(settings.profile_email);
      setPhone(settings.profile_phone);
      setLinkedin(settings.profile_linkedin);
      setCoverLetter(settings.cover_letter);
    }
  }, [settings]);
  const saveM = useMutation({
    mutationFn: () =>
      api.saveProfile(workspaceId, { name, email, phone, linkedin, cover_letter: coverLetter }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', workspaceId] }),
  });
  return (
    <section className="card p-6">
      <div className="mb-4 flex items-start gap-3">
        <div className="rounded-xl bg-sage/10 p-2 text-sage">
          <UserRound size={20} />
        </div>
        <div>
          <h2 className="font-display font-bold">Applicant profile</h2>
          <p className="text-sm text-black/45">
            Used to fill standard fields when a batch auto-applies on your behalf.
          </p>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <input
          className="field"
          placeholder="Full name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="field"
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="field"
          placeholder="Phone"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        <input
          className="field"
          placeholder="LinkedIn URL"
          value={linkedin}
          onChange={(e) => setLinkedin(e.target.value)}
        />
      </div>
      <textarea
        className="field mt-3 h-20 w-full resize-none"
        placeholder="Optional cover letter template"
        value={coverLetter}
        onChange={(e) => setCoverLetter(e.target.value)}
      />
      <div className="mt-3 flex justify-end">
        <button
          className="btn bg-ink text-white hover:bg-sage"
          onClick={() => saveM.mutate()}
          disabled={saveM.isPending}
        >
          {saveM.isPending ? 'Saving…' : 'Save profile'}
        </button>
      </div>
    </section>
  );
}

function Discover({ workspaceId, onDone }: { workspaceId: number; onDone: () => void }) {
  const [query, setQuery] = useState('Software Engineer'),
    [days, setDays] = useState(2),
    [max, setMax] = useState(100),
    [polling, setPolling] = useState(false);
  const mutation = useMutation({
    mutationFn: () => api.scrape(workspaceId, { query, days, max_jobs: max }),
    onSuccess: () => setPolling(true),
  });
  const statusQ = useQuery({
    queryKey: ['scrape-status', workspaceId],
    queryFn: () => api.scrapeStatus(workspaceId),
    enabled: polling,
    refetchInterval: polling ? 1000 : false,
  });
  useEffect(() => {
    if (polling && statusQ.data?.done) {
      setPolling(false);
      if (!statusQ.data.error) onDone();
    }
  }, [polling, statusQ.data?.done, statusQ.data?.error, onDone]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    mutation.mutate();
  };
  const busy = mutation.isPending || polling;
  const result = statusQ.data?.result;
  return (
    <section className="card p-6">
      <div className="mb-5 flex items-start gap-3">
        <div className="rounded-xl bg-black/5 p-2 text-black">
          <Sparkles size={20} />
        </div>
        <div>
          <h2 className="font-display font-bold">Discover opportunities</h2>
          <p className="text-sm text-black/45">
            Crawl HiringCafe in Chromium and score every role against your resume.
          </p>
        </div>
      </div>
      <form className="space-y-3" onSubmit={submit}>
        <input
          className="field w-full"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search query"
        />
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs font-semibold text-black/50">
            Past days
            <input
              className="field mt-1 w-full"
              type="number"
              min="1"
              max="30"
              value={days}
              onChange={(e) => setDays(+e.target.value)}
            />
          </label>
          <label className="text-xs font-semibold text-black/50">
            Maximum jobs
            <input
              className="field mt-1 w-full"
              type="number"
              min="1"
              max="1000"
              value={max}
              onChange={(e) => setMax(+e.target.value)}
            />
          </label>
        </div>
        <button className="btn w-full bg-sage text-white hover:bg-ink" disabled={busy}>
          {busy ? (
            <>
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              Collected {statusQ.data?.collected ?? 0} · crawling/scoring…
            </>
          ) : (
            <>
              <Search size={17} />
              Discover jobs
            </>
          )}
        </button>
        {(mutation.error || statusQ.data?.error) && (
          <p className="text-sm text-red-600">{mutation.error?.message || statusQ.data?.error}</p>
        )}
        {result && !polling && (
          <p className="text-sm font-medium text-neutral-700">
            {result.fetched} fetched · {result.new} new · {result.above_threshold} strong matches
          </p>
        )}
      </form>
    </section>
  );
}

function ExportButton({ workspaceId, params }: { workspaceId: number; params: URLSearchParams }) {
  const mutation = useMutation({
    mutationFn: () => api.exportJobs(workspaceId, params),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `meridian_jobs_${new Date().toISOString().slice(0, 10)}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    },
  });
  return (
    <button
      className="btn bg-sage/10 text-sage hover:bg-sage hover:text-white"
      disabled={mutation.isPending}
      onClick={() => mutation.mutate()}
    >
      <Download size={16} />
      {mutation.isPending ? 'Preparing…' : 'Download Excel'}
    </button>
  );
}

function StatCards({ stats }: any) {
  const cards = [
    [BriefcaseBusiness, 'Total discovered', stats?.total ?? 0],
    [Target, 'Above threshold', stats?.above_threshold ?? 0],
    [Users, 'Applied', stats?.by_status?.applied ?? 0],
    [BarChart3, 'Average score', stats?.avg_score ?? 0],
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {cards.map(([Icon, label, value]: any) => (
        <div className="card flex items-center gap-4 p-5" key={label}>
          <div className="rounded-2xl bg-sage/10 p-3 text-sage">
            <Icon size={22} />
          </div>
          <div>
            <p className="text-sm text-black/45">{label}</p>
            <p className="font-display text-2xl font-extrabold">{value}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function Charts({ stats }: any) {
  const statusData = stats
    ? Object.entries(stats.by_status).map(([name, value]) => ({
        name: statusLabels[name as Status],
        value,
      }))
    : [];
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Chart title="Score distribution">
        <BarChart data={stats?.score_histogram ?? []}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e8e9e4" />
          <XAxis dataKey="bucket" tick={{ fontSize: 11 }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Bar name="Jobs" dataKey="count" fill="#2563eb" radius={[5, 5, 0, 0]} />
        </BarChart>
      </Chart>
      <Chart title="Jobs by ATS">
        <BarChart data={stats?.by_ats ?? []}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e8e9e4" />
          <XAxis dataKey="ats" tick={{ fontSize: 10 }} />
          <YAxis yAxisId="count" allowDecimals={false} tick={{ fontSize: 11 }} />
          <YAxis yAxisId="score" orientation="right" domain={[0, 100]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <Bar yAxisId="count" name="Jobs" dataKey="count" fill="#d89b24" radius={[5, 5, 0, 0]} />
          <Bar
            yAxisId="score"
            name="Avg score"
            dataKey="avg_score"
            fill="#168f83"
            radius={[5, 5, 0, 0]}
          />
        </BarChart>
      </Chart>
      <Chart title="Pipeline status">
        <PieChart>
          <Pie
            data={statusData}
            dataKey="value"
            nameKey="name"
            innerRadius={52}
            outerRadius={78}
            paddingAngle={3}
          >
            {statusData.map((_: any, i: number) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Pie>
          <Tooltip />
        </PieChart>
      </Chart>
      <Chart title="Applications over time">
        <LineChart data={stats?.applied_over_time ?? []}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e8e9e4" />
          <XAxis dataKey="date" tick={{ fontSize: 10 }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Line
            name="Applications"
            type="monotone"
            dataKey="count"
            stroke="#2563eb"
            strokeWidth={3}
            dot={{ fill: '#d89b24' }}
          />
        </LineChart>
      </Chart>
    </div>
  );
}
function Chart({ title, children }: { title: string; children: any }) {
  return (
    <div className="card p-5">
      <h3 className="mb-4 font-display font-bold">{title}</h3>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function JobTable({
  jobs,
  threshold,
  workspaceId,
  onChanged,
}: {
  jobs: Job[];
  threshold: number;
  workspaceId: number;
  onChanged: () => void;
}) {
  return (
    <div>
      <div className="hidden grid-cols-[72px_1fr_1fr_110px_130px_32px] gap-3 border-b border-black/5 bg-black/[.02] px-5 py-2 text-xs font-bold uppercase tracking-wider text-black/40 md:grid">
        <span>Score</span>
        <span>Title</span>
        <span>Company</span>
        <span>ATS</span>
        <span>Status</span>
        <span />
      </div>
      <div className="divide-y divide-black/5">
        {jobs.map((job) => (
          <JobRow
            job={job}
            threshold={threshold}
            workspaceId={workspaceId}
            key={job.id}
            onChanged={onChanged}
          />
        ))}
      </div>
    </div>
  );
}
function JobRow({
  job,
  threshold,
  workspaceId,
  onChanged,
}: {
  job: Job;
  threshold: number;
  workspaceId: number;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const patch = useMutation({
    mutationFn: (status: Status) => api.patchJob(workspaceId, job.id, status),
    onMutate: async (status) => {
      await qc.cancelQueries({ queryKey: ['jobs', workspaceId] });
      const snapshots = qc.getQueriesData<Job[]>({ queryKey: ['jobs', workspaceId] });
      qc.setQueriesData<Job[]>({ queryKey: ['jobs', workspaceId] }, (old) =>
        old?.map((j) => (j.id === job.id ? { ...j, status } : j)),
      );
      return { snapshots };
    },
    onError: (_error, _status, context) =>
      context?.snapshots.forEach(([key, data]) => qc.setQueryData(key, data)),
    onSettled: onChanged,
  });
  return (
    <article className={job.score >= threshold ? 'bg-neutral-100/70' : ''}>
      <div className="grid items-center gap-3 px-5 py-4 md:grid-cols-[72px_1fr_1fr_110px_130px_32px]">
        <span
          className={`w-fit rounded-xl px-3 py-2 font-display text-sm font-extrabold ${scoreTone(job.score, threshold)}`}
        >
          {job.score}
        </span>
        <div>
          <p className="font-semibold">{job.title}</p>
          <p className="mt-0.5 text-xs text-black/40">
            Fetched {new Date(job.date_fetched).toLocaleDateString()}
          </p>
          {job.auto_apply_state === 'applied_auto' && (
            <span className="mt-1 inline-block rounded-full bg-teal/10 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-teal">
              Auto-applied
            </span>
          )}
          {job.auto_apply_state === 'needs_review' && (
            <span className="mt-1 inline-block rounded-full bg-sun/20 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-amber-900">
              Needs review · {reviewReasonLabels[job.review_reason ?? ''] ?? job.review_reason}
            </span>
          )}
        </div>
        <p className="text-sm font-medium text-black/65">{job.company}</p>
        <span className="w-fit rounded-full bg-neutral-100 px-2.5 py-1 text-xs capitalize text-neutral-600">
          {job.ats_platform}
        </span>
        <select
          className="field py-1.5"
          aria-label={`Status for ${job.title}`}
          disabled={patch.isPending}
          value={job.status}
          onChange={(e) => patch.mutate(e.target.value as Status)}
        >
          {Object.entries(statusLabels).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <button
          className="rounded-lg p-1 hover:bg-black/5"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-label={`Toggle details for ${job.title}`}
        >
          {open ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>
      </div>
      {open && (
        <div className="border-t border-black/5 bg-white/70 px-5 py-5">
          <div className="mb-5 grid gap-3 rounded-xl bg-neutral-100 p-3 text-sm sm:grid-cols-3">
            <span>
              Requirements <b>{Math.round(job.requirement_coverage * 100)}%</b>
            </span>
            <span>
              Skills <b>{Math.round(job.skill_coverage * 100)}%</b>
            </span>
            <span>
              Overall similarity <b>{Math.round(job.global_similarity * 100)}%</b>
            </span>
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-wider text-black/40">
                Missing skills
              </p>
              <div className="flex flex-wrap gap-2">
                {job.missing_skills.length ? (
                  job.missing_skills.map((s) => (
                    <span
                      className="rounded-lg bg-neutral-200 px-2 py-1 text-xs text-neutral-700"
                      key={s}
                    >
                      {s}
                    </span>
                  ))
                ) : (
                  <span className="text-sm font-medium text-neutral-700">
                    No obvious skill gaps
                  </span>
                )}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-wider text-black/40">
                Weak requirements
              </p>
              {job.weak_requirements.length ? (
                <ul className="max-h-40 list-disc space-y-1 overflow-auto pl-5 text-sm text-black/60">
                  {job.weak_requirements.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm font-medium text-neutral-700">Strong requirement coverage</p>
              )}
            </div>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            {job.apply_url ? (
              <a
                className="btn bg-ink text-white hover:bg-sage"
                href={job.apply_url}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={15} />
                Open original posting
              </a>
            ) : (
              <span className="text-sm text-neutral-600">
                No external posting URL was provided.
              </span>
            )}
            <button
              className="btn bg-black text-white hover:bg-neutral-800"
              disabled={patch.isPending}
              onClick={() => patch.mutate('applied')}
            >
              Mark applied
            </button>
            <button
              className="btn bg-neutral-100 text-neutral-600 hover:bg-neutral-200"
              disabled={patch.isPending}
              onClick={() => patch.mutate('skipped')}
            >
              Skip
            </button>
          </div>
          {patch.error && <p className="mt-3 text-sm text-red-600">{patch.error.message}</p>}
        </div>
      )}
    </article>
  );
}
function ErrorBox({ error }: { error: Error }) {
  return <div className="m-5 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error.message}</div>;
}
export default WorkspaceView;
