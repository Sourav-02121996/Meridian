import { FormEvent, useEffect, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  Trash2,
  Upload,
  UserRound,
  Users,
  Zap,
} from 'lucide-react';
import { Pagination } from './Pagination';
import { useDebouncedValue } from './hooks/useDebouncedValue';
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
import {
  api,
  DEPARTMENT_OPTIONS,
  Job,
  Profile,
  SENIORITY_OPTIONS,
  Status,
  WorkspaceSettings,
} from './api';
import BlockedQuestionsPanel from './BlockedQuestionsPanel';
import MultiSelectChips from './MultiSelectChips';
import { reviewReasonLabels } from './reviewReasons';

const statusLabels: Record<Status, string> = {
  discovered: 'Discovered',
  to_apply: 'To apply',
  applied: 'Applied',
  skipped: 'Skipped',
};
const colors = ['#2563eb', '#d89b24', '#16a34a', '#dc2626'];
const axisTick = { fontSize: 11, fill: 'rgb(var(--fg) / 0.45)' };
const tooltipStyle = {
  background: 'rgb(var(--surface))',
  border: '1px solid rgb(var(--fg) / 0.1)',
  borderRadius: 8,
  color: 'rgb(var(--fg))',
  fontSize: 13,
};

function scoreTone(score: number, threshold: number) {
  return score >= threshold
    ? 'bg-accent text-white'
    : score >= Math.max(0, threshold - 17)
      ? 'bg-warning/20 text-warning'
      : 'bg-fg/5 text-fg/65';
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
    [order, setOrder] = useState('desc'),
    [page, setPage] = useState(1),
    [pageSize, setPageSize] = useState(20);
  const debouncedSearch = useDebouncedValue(search, 300);
  useEffect(() => {
    if (settingsQ.data) {
      setResume(settingsQ.data.resume);
      setThreshold(settingsQ.data.threshold);
      setAutoApplyThreshold(settingsQ.data.auto_apply_threshold);
    }
  }, [settingsQ.data]);
  const filterParams = new URLSearchParams();
  if (status) filterParams.set('status', status);
  if (minScore) filterParams.set('min_score', minScore);
  if (debouncedSearch) filterParams.set('q', debouncedSearch);
  filterParams.set('sort', sort);
  filterParams.set('order', order);
  const listParams = new URLSearchParams(filterParams);
  listParams.set('page', String(page));
  listParams.set('page_size', String(pageSize));
  const jobsQ = useQuery({
    queryKey: ['jobs', workspaceId, listParams.toString()],
    queryFn: () => api.jobs(workspaceId, listParams),
    placeholderData: keepPreviousData,
    // Only the current page's jobs are checked here, so a job `applying` on a
    // different page won't trigger polling while viewing this one — an accepted
    // trade-off of paginating a list that used to be fetched in full.
    refetchInterval: (query) =>
      query.state.data?.items?.some((job) => job.auto_apply_state === 'applying') ? 1000 : false,
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
      <section className="border-b border-fg/15 pb-7">
        <button
          className="mb-4 flex items-center gap-2 text-sm font-semibold text-fg/65 hover:text-fg"
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
          icon={<Target className="text-accent" />}
          title="Your match threshold"
          hint="Jobs at or above this score are highlighted"
          value={threshold}
          setValue={setThreshold}
          onCommit={(value) => thresholdM.mutate(value)}
        />
        <ThresholdCard
          icon={<Sparkles className="text-accent" />}
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
        <div className="border-b border-fg/10 p-5">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-display text-xl font-bold">Job pipeline</h2>
              <p className="text-sm text-fg/65">Review matches and keep your search moving.</p>
            </div>
            <div className="flex items-center gap-3">
              <span className="mono-num text-sm font-medium text-fg/65">
                {jobsQ.data?.total ?? 0} jobs
              </span>
              <ExportButton workspaceId={workspaceId} params={filterParams} />
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            <label className="field flex items-center gap-2">
              <Search size={16} />
              <input
                className="min-w-0 flex-1 outline-none"
                placeholder="Search roles or companies"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                }}
              />
            </label>
            <select
              className="field"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
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
              onChange={(e) => {
                setMinScore(e.target.value);
                setPage(1);
              }}
            />
            <select
              className="field"
              value={`${sort}-${order}`}
              onChange={(e) => {
                const [s, o] = e.target.value.split('-');
                setSort(s);
                setOrder(o);
                setPage(1);
              }}
            >
              <option value="score-desc">Highest score</option>
              <option value="score-asc">Lowest score</option>
              <option value="date-desc">Newest first</option>
              <option value="date-asc">Oldest first</option>
            </select>
          </div>
          <p className="mt-2 text-right text-xs text-fg/65">
            Excel export reflects the active status, score, and search filters.
          </p>
        </div>
        {jobsQ.isLoading ? (
          <div className="p-12 text-center text-fg/65">Loading your pipeline…</div>
        ) : jobsQ.isError ? (
          <ErrorBox error={jobsQ.error} onRetry={() => jobsQ.refetch()} />
        ) : jobsQ.data?.items.length ? (
          <>
            <JobTable
              jobs={jobsQ.data.items}
              threshold={threshold}
              workspaceId={workspaceId}
              onChanged={refresh}
            />
            <Pagination
              total={jobsQ.data.total}
              page={page}
              pageSize={pageSize}
              onPageChange={setPage}
              onPageSizeChange={(size) => {
                setPageSize(size);
                setPage(1);
              }}
            />
          </>
        ) : status || minScore || search ? (
          <div className="p-16 text-center">
            <BriefcaseBusiness className="mx-auto mb-3 text-fg/65" size={40} />
            <p className="font-display font-bold">No jobs match these filters</p>
            <p className="mt-1 text-sm text-fg/65">Try adjusting or clearing your filters.</p>
          </div>
        ) : (
          <div className="p-16 text-center">
            <BriefcaseBusiness className="mx-auto mb-3 text-fg/65" size={40} />
            <p className="font-display font-bold">No jobs here yet</p>
            <p className="mt-1 text-sm text-fg/65">
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
        <p className="text-xs text-fg/65">{hint}</p>
      </div>
      <input
        className="min-w-48 flex-1 accent-accent"
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
      <span className="mono-num rounded-xl bg-accent px-3 py-1.5 font-bold text-white">
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
        <div className="rounded-xl bg-accent/10 p-2 text-accent">
          <FileText size={20} />
        </div>
        <div>
          <h2 className="font-display font-bold">Your resume</h2>
          <p className="text-sm text-fg/65">
            Paste text or upload a text-based PDF. Uploading a PDF also keeps the original file on
            hand so batches can attach it when auto-applying.
          </p>
        </div>
      </div>
      <label className="mb-3 flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-accent/40 bg-accent/5 px-4 py-3 text-sm font-semibold text-accent transition hover:bg-accent/10">
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
      {uploadM.error && <p className="mb-3 text-sm text-danger">{uploadM.error.message}</p>}
      {uploadMessage && <p className="mb-3 text-sm font-medium text-fg/70">{uploadMessage}</p>}
      <textarea
        className="field h-36 w-full resize-none"
        placeholder="Paste your resume text here…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="mt-3 flex items-center justify-between">
        <span className="mono-num text-xs text-fg/65">
          {value.length.toLocaleString()} characters
        </span>
        <button className="btn btn-dark" disabled={saving || !value.trim()} onClick={onSave}>
          {saving ? 'Saving…' : 'Save resume'}
        </button>
      </div>
    </section>
  );
}

const EEO_GENDER_OPTIONS = ['Male', 'Female', 'Non-binary', 'Other'];
const EEO_RACE_OPTIONS = [
  'American Indian or Alaska Native',
  'Asian',
  'Black or African American',
  'Hispanic or Latino',
  'Native Hawaiian or Other Pacific Islander',
  'White',
  'Two or more races',
  'Other',
];
const EEO_VETERAN_OPTIONS = ['I am a veteran', 'I am not a veteran'];
const EEO_DISABILITY_OPTIONS = ['Yes, I have a disability', 'No, I do not have a disability'];

function ProfilePanel({
  workspaceId,
  settings,
}: {
  workspaceId: number;
  settings?: WorkspaceSettings;
}) {
  const qc = useQueryClient();
  const [profile, setProfile] = useState<Profile>({
    name: '',
    email: '',
    phone: '',
    linkedin: '',
    portfolio_url: '',
    github_url: '',
    city: '',
    state: '',
    country: '',
    current_company: '',
    current_title: '',
    desired_salary: '',
    start_date: '',
    work_authorized: '',
    visa_sponsorship: '',
    willing_to_relocate: '',
    is_18_or_older: '',
    gender: '',
    race_ethnicity: '',
    veteran_status: '',
    disability_status: '',
    citizenship: '',
    security_clearance: '',
    background_check_consent: '',
    drug_test_consent: '',
    criminal_history: '',
    cover_letter: '',
  });
  const set =
    <K extends keyof Profile>(key: K) =>
    (value: Profile[K]) =>
      setProfile((current) => ({ ...current, [key]: value }));
  useEffect(() => {
    if (settings) {
      setProfile({
        name: settings.profile_name,
        email: settings.profile_email,
        phone: settings.profile_phone,
        linkedin: settings.profile_linkedin,
        portfolio_url: settings.profile_portfolio_url,
        github_url: settings.profile_github_url,
        city: settings.profile_city,
        state: settings.profile_state,
        country: settings.profile_country,
        current_company: settings.profile_current_company,
        current_title: settings.profile_current_title,
        desired_salary: settings.profile_desired_salary,
        start_date: settings.profile_start_date,
        work_authorized: settings.profile_work_authorized,
        visa_sponsorship: settings.profile_visa_sponsorship,
        willing_to_relocate: settings.profile_willing_to_relocate,
        is_18_or_older: settings.profile_18_or_older,
        gender: settings.profile_gender,
        race_ethnicity: settings.profile_race_ethnicity,
        veteran_status: settings.profile_veteran_status,
        disability_status: settings.profile_disability_status,
        citizenship: settings.profile_citizenship,
        security_clearance: settings.profile_security_clearance,
        background_check_consent: settings.profile_background_check_consent,
        drug_test_consent: settings.profile_drug_test_consent,
        criminal_history: settings.profile_criminal_history,
        cover_letter: settings.cover_letter,
      });
    }
  }, [settings]);
  const saveM = useMutation({
    mutationFn: () => api.saveProfile(workspaceId, profile),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings', workspaceId] }),
  });
  return (
    <section className="card p-6">
      <div className="mb-4 flex items-start gap-3">
        <div className="rounded-xl bg-accent/10 p-2 text-accent">
          <UserRound size={20} />
        </div>
        <div>
          <h2 className="font-display font-bold">Applicant profile</h2>
          <p className="text-sm text-fg/65">
            Used to fill standard fields when a batch auto-applies on your behalf. Anything left
            blank is simply skipped — never guessed.
          </p>
        </div>
      </div>

      <p className="mb-2 text-xs font-bold uppercase tracking-wider text-fg/40">
        Contact &amp; links
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Full name" value={profile.name} onChange={set('name')} />
        <TextField label="Email" type="email" value={profile.email} onChange={set('email')} />
        <TextField label="Phone" value={profile.phone} onChange={set('phone')} />
        <TextField label="LinkedIn URL" value={profile.linkedin} onChange={set('linkedin')} />
        <TextField
          label="Portfolio / website URL"
          value={profile.portfolio_url}
          onChange={set('portfolio_url')}
        />
        <TextField label="GitHub URL" value={profile.github_url} onChange={set('github_url')} />
      </div>

      <p className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-fg/40">Location</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField label="City" value={profile.city} onChange={set('city')} />
        <TextField label="State / region" value={profile.state} onChange={set('state')} />
        <TextField label="Country" value={profile.country} onChange={set('country')} />
      </div>

      <p className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-fg/40">
        Work details
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Current company"
          value={profile.current_company}
          onChange={set('current_company')}
        />
        <TextField
          label="Current title"
          value={profile.current_title}
          onChange={set('current_title')}
        />
        <TextField
          label="Desired salary"
          value={profile.desired_salary}
          onChange={set('desired_salary')}
        />
        <TextField
          label="Earliest start date"
          value={profile.start_date}
          onChange={set('start_date')}
        />
      </div>

      <p className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-fg/40">Eligibility</p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <YesNoField
          label="Authorized to work"
          value={profile.work_authorized}
          onChange={set('work_authorized')}
        />
        <YesNoField
          label="Needs visa sponsorship"
          value={profile.visa_sponsorship}
          onChange={set('visa_sponsorship')}
        />
        <YesNoField
          label="Willing to relocate"
          value={profile.willing_to_relocate}
          onChange={set('willing_to_relocate')}
        />
        <YesNoField
          label="18 years or older"
          value={profile.is_18_or_older}
          onChange={set('is_18_or_older')}
        />
      </div>

      <p className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-fg/40">
        Voluntary self-identification
      </p>
      <p className="mb-2 text-xs text-fg/45">
        Optional and legally protected — left as "Decline to self-identify" unless you choose
        otherwise.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <EeoField
          label="Gender"
          value={profile.gender}
          options={EEO_GENDER_OPTIONS}
          onChange={set('gender')}
        />
        <EeoField
          label="Race / ethnicity"
          value={profile.race_ethnicity}
          options={EEO_RACE_OPTIONS}
          onChange={set('race_ethnicity')}
        />
        <EeoField
          label="Veteran status"
          value={profile.veteran_status}
          options={EEO_VETERAN_OPTIONS}
          onChange={set('veteran_status')}
        />
        <EeoField
          label="Disability status"
          value={profile.disability_status}
          options={EEO_DISABILITY_OPTIONS}
          onChange={set('disability_status')}
        />
      </div>

      <p className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-fg/40">
        Compliance &amp; background
      </p>
      <p className="mb-2 text-xs text-fg/45">
        Left blank, a question like this is never guessed by the LLM tiers — it's sent to Needs
        Review instead. Answered here, it's filled the same as any other profile field.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <TextField label="Citizenship" value={profile.citizenship} onChange={set('citizenship')} />
        <TextField
          label="Security clearance"
          value={profile.security_clearance}
          onChange={set('security_clearance')}
        />
        <YesNoField
          label="Consent to background check"
          value={profile.background_check_consent}
          onChange={set('background_check_consent')}
        />
        <YesNoField
          label="Consent to drug test"
          value={profile.drug_test_consent}
          onChange={set('drug_test_consent')}
        />
        <YesNoField
          label="Ever convicted of a crime"
          value={profile.criminal_history}
          onChange={set('criminal_history')}
        />
      </div>

      <p className="mb-2 mt-5 text-xs font-bold uppercase tracking-wider text-fg/40">
        Cover letter
      </p>
      <textarea
        className="field h-20 w-full resize-none"
        placeholder="Optional cover letter template"
        value={profile.cover_letter}
        onChange={(e) => set('cover_letter')(e.target.value)}
      />

      <div className="mt-4 flex justify-end">
        <button className="btn btn-dark" onClick={() => saveM.mutate()} disabled={saveM.isPending}>
          {saveM.isPending ? 'Saving…' : 'Save profile'}
        </button>
      </div>
    </section>
  );
}

function TextField({
  label,
  value,
  onChange,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <label className="text-xs font-semibold text-fg/50">
      {label}
      <input
        className="field mt-1 w-full"
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function YesNoField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="text-xs font-semibold text-fg/50">
      {label}
      <select
        className="field mt-1 w-full"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Not answered</option>
        <option value="Yes">Yes</option>
        <option value="No">No</option>
      </select>
    </label>
  );
}

function EeoField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="text-xs font-semibold text-fg/50">
      {label}
      <select
        className="field mt-1 w-full"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Decline to self-identify</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function Discover({ workspaceId, onDone }: { workspaceId: number; onDone: () => void }) {
  const [query, setQuery] = useState('Software Engineer'),
    [days, setDays] = useState(2),
    [max, setMax] = useState(100),
    [jobTitleQuery, setJobTitleQuery] = useState(''),
    [technologyKeywordsQuery, setTechnologyKeywordsQuery] = useState(''),
    [jobDescriptionQuery, setJobDescriptionQuery] = useState(''),
    [departments, setDepartments] = useState<string[]>([]),
    [seniority, setSeniority] = useState<string[]>([]),
    [polling, setPolling] = useState(false);
  const toggle = (list: string[], setList: (v: string[]) => void, value: string) =>
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  const mutation = useMutation({
    mutationFn: () =>
      api.scrape(workspaceId, {
        query,
        days,
        max_jobs: max,
        job_title_query: jobTitleQuery,
        technology_keywords_query: technologyKeywordsQuery,
        job_description_query: jobDescriptionQuery,
        departments,
        seniority,
      }),
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
        <div className="rounded-xl bg-fg/5 p-2 text-fg">
          <Sparkles size={20} />
        </div>
        <div>
          <h2 className="font-display font-bold">Discover opportunities</h2>
          <p className="text-sm text-fg/65">
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
          placeholder="General search"
        />
        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs font-semibold text-fg/65">
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
          <label className="text-xs font-semibold text-fg/65">
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
        <details className="group">
          <summary className="cursor-pointer text-xs font-bold uppercase tracking-wider text-fg/40">
            More filters
          </summary>
          <div className="mt-3 space-y-3">
            <label className="text-xs font-semibold text-fg/65">
              Job title (boolean query)
              <input
                className="field mt-1 w-full"
                placeholder='e.g. "Software Engineer" OR "Backend Engineer"'
                value={jobTitleQuery}
                onChange={(e) => setJobTitleQuery(e.target.value)}
              />
            </label>
            <label className="text-xs font-semibold text-fg/65">
              Technology keywords (boolean query)
              <input
                className="field mt-1 w-full"
                placeholder='e.g. "Python" AND "AWS"'
                value={technologyKeywordsQuery}
                onChange={(e) => setTechnologyKeywordsQuery(e.target.value)}
              />
            </label>
            <label className="text-xs font-semibold text-fg/65">
              Description keywords (boolean query)
              <input
                className="field mt-1 w-full"
                placeholder='e.g. "remote" OR "unlimited PTO"'
                value={jobDescriptionQuery}
                onChange={(e) => setJobDescriptionQuery(e.target.value)}
              />
            </label>
            <MultiSelectChips
              label="Departments"
              hint="Leave empty to use the default (Engineering, Software Development, IT)"
              options={DEPARTMENT_OPTIONS}
              selected={departments}
              onToggle={(v) => toggle(departments, setDepartments, v)}
            />
            <MultiSelectChips
              label="Seniority level"
              hint="Leave empty to use the default (Entry + Mid level)"
              options={SENIORITY_OPTIONS}
              selected={seniority}
              onToggle={(v) => toggle(seniority, setSeniority, v)}
            />
          </div>
        </details>
        <button className="btn btn-dark w-full" disabled={busy}>
          {busy ? (
            <>
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              <span className="mono-num">Collected {statusQ.data?.collected ?? 0}</span> ·
              crawling/scoring…
            </>
          ) : (
            <>
              <Search size={17} />
              Discover jobs
            </>
          )}
        </button>
        {(mutation.error || statusQ.data?.error) && (
          <p className="text-sm text-danger">{mutation.error?.message || statusQ.data?.error}</p>
        )}
        {result && !polling && (
          <p className="mono-num text-sm font-medium text-fg/70">
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
      className="btn bg-accent/10 text-accent hover:bg-accent hover:text-white"
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
          <div className="rounded-2xl bg-accent/10 p-3 text-accent">
            <Icon size={22} />
          </div>
          <div>
            <p className="text-sm text-fg/65">{label}</p>
            <p className="mono-num text-2xl font-bold">{value}</p>
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
          <CartesianGrid
            strokeDasharray="3 3"
            vertical={false}
            stroke="currentColor"
            strokeOpacity={0.1}
          />
          <XAxis dataKey="bucket" tick={axisTick} />
          <YAxis allowDecimals={false} tick={axisTick} />
          <Tooltip contentStyle={tooltipStyle} />
          <Bar name="Jobs" dataKey="count" fill="#2563eb" radius={[3, 3, 0, 0]} />
        </BarChart>
      </Chart>
      <Chart title="Jobs by ATS">
        <BarChart data={stats?.by_ats ?? []}>
          <CartesianGrid
            strokeDasharray="3 3"
            vertical={false}
            stroke="currentColor"
            strokeOpacity={0.1}
          />
          <XAxis dataKey="ats" tick={{ ...axisTick, fontSize: 10 }} />
          <YAxis yAxisId="count" allowDecimals={false} tick={axisTick} />
          <YAxis yAxisId="score" orientation="right" domain={[0, 100]} tick={axisTick} />
          <Tooltip contentStyle={tooltipStyle} />
          <Legend wrapperStyle={{ color: 'rgb(var(--fg) / 0.7)', fontSize: 12 }} />
          <Bar yAxisId="count" name="Jobs" dataKey="count" fill="#d89b24" radius={[3, 3, 0, 0]} />
          <Bar
            yAxisId="score"
            name="Avg score"
            dataKey="avg_score"
            fill="#16a34a"
            radius={[3, 3, 0, 0]}
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
          <Tooltip contentStyle={tooltipStyle} />
        </PieChart>
      </Chart>
      <Chart title="Applications over time">
        <LineChart data={stats?.applied_over_time ?? []}>
          <CartesianGrid
            strokeDasharray="3 3"
            vertical={false}
            stroke="currentColor"
            strokeOpacity={0.1}
          />
          <XAxis dataKey="date" tick={{ ...axisTick, fontSize: 10 }} />
          <YAxis allowDecimals={false} tick={axisTick} />
          <Tooltip contentStyle={tooltipStyle} />
          <Line
            name="Applications"
            type="monotone"
            dataKey="count"
            stroke="#2563eb"
            strokeWidth={2.5}
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
      <div className="hidden grid-cols-[72px_1fr_1fr_110px_130px_32px] gap-3 border-b border-fg/10 bg-fg/[.03] px-5 py-2 text-xs font-bold uppercase tracking-wider text-fg/65 md:grid">
        <span>Score</span>
        <span>Title</span>
        <span>Company</span>
        <span>ATS</span>
        <span>Status</span>
        <span />
      </div>
      <div className="divide-y divide-fg/10">
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
  const deleteJob = useMutation({
    mutationFn: () => api.deleteJob(workspaceId, job.id),
    onSuccess: onChanged,
  });
  // Actually re-attempts the real automated submission — distinct from "Mark
  // applied" below, which is pure bookkeeping ("I did this myself") and never
  // launches a browser. Intended right after approving an answer in
  // BlockedQuestionsPanel below, but available for any needs_review reason.
  const retry = useMutation({
    mutationFn: () => api.retryApply(workspaceId, job.id),
    onSuccess: onChanged,
  });
  const handleDelete = () => {
    // Deletion is permanent (see routes/jobs.py::delete_job) — an extra, more
    // specific warning for a job already marked applied, since that's the one
    // case where deleting also erases Meridian's own record of a real
    // submitted application, not just a discovered/skipped listing.
    const warning =
      job.status === 'applied'
        ? `"${job.title}" is marked applied — deleting it removes Meridian's record of that application (it won't un-apply anywhere). Delete permanently? This can't be undone.`
        : 'Delete permanently? This action cannot be undone.';
    if (window.confirm(warning)) {
      deleteJob.mutate();
    }
  };
  return (
    <article className={job.score >= threshold ? 'bg-fg/[.03]' : ''}>
      <div className="grid items-center gap-3 px-5 py-4 md:grid-cols-[72px_1fr_1fr_110px_130px_32px]">
        <span
          className={`mono-num w-fit rounded-lg px-3 py-2 text-sm font-bold ${scoreTone(job.score, threshold)}`}
        >
          {job.score}
        </span>
        <div>
          <p className="font-semibold">{job.title}</p>
          <p className="mono-num mt-0.5 text-xs text-fg/65">
            Fetched {new Date(job.date_fetched).toLocaleDateString()}
          </p>
          {job.auto_apply_state === 'applied_auto' && (
            <span className="mt-1 inline-block rounded-full bg-success/10 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-success">
              Auto-applied
            </span>
          )}
          {job.auto_apply_state === 'applying' && (
            <span className="mt-1 inline-block rounded-full bg-accent/10 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-accent">
              Applying — waiting for employer response
            </span>
          )}
          {job.auto_apply_state === 'needs_review' && (
            <span className="mt-1 inline-block rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-warning">
              Needs review · {reviewReasonLabels[job.review_reason ?? ''] ?? job.review_reason}
            </span>
          )}
        </div>
        <p className="text-sm font-medium text-fg/65">{job.company}</p>
        <span className="w-fit rounded-full bg-fg/5 px-2.5 py-1 text-xs capitalize text-fg/65">
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
          className="rounded-lg p-1 hover:bg-fg/5"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-label={`Toggle details for ${job.title}`}
        >
          {open ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>
      </div>
      {open && (
        <div className="border-t border-fg/10 bg-fg/[.02] px-5 py-5">
          <div className="mono-num mb-5 grid gap-3 rounded-xl bg-fg/5 p-3 text-sm sm:grid-cols-3">
            <span className="font-sans">
              Requirements <b>{Math.round(job.requirement_coverage * 100)}%</b>
            </span>
            <span className="font-sans">
              Skills <b>{Math.round(job.skill_coverage * 100)}%</b>
            </span>
            <span className="font-sans">
              Overall similarity <b>{Math.round(job.global_similarity * 100)}%</b>
            </span>
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-wider text-fg/65">
                Missing skills
              </p>
              <div className="flex flex-wrap gap-2">
                {job.missing_skills.length ? (
                  job.missing_skills.map((s) => (
                    <span className="rounded-lg bg-fg/10 px-2 py-1 text-xs text-fg/70" key={s}>
                      {s}
                    </span>
                  ))
                ) : (
                  <span className="text-sm font-medium text-fg/70">No obvious skill gaps</span>
                )}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-wider text-fg/65">
                Weak requirements
              </p>
              {job.weak_requirements.length ? (
                <ul className="max-h-40 list-disc space-y-1 overflow-auto pl-5 text-sm text-fg/65">
                  {job.weak_requirements.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm font-medium text-fg/70">Strong requirement coverage</p>
              )}
            </div>
          </div>
          {job.auto_apply_state === 'needs_review' && (
            <div className="mb-5">
              <BlockedQuestionsPanel workspaceId={workspaceId} jobId={job.id} enabled={open} />
            </div>
          )}
          {job.auto_apply_state === 'needs_review' && job.last_apply_detail && (
            <p className="mb-5 break-words rounded-lg bg-danger/5 p-3 text-xs text-danger">
              {job.last_apply_detail}
            </p>
          )}
          <div className="mt-5 flex flex-wrap gap-2">
            {job.apply_url ? (
              <a
                className="btn bg-fg text-paper hover:brightness-110"
                href={job.apply_url}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={15} />
                Open original posting
              </a>
            ) : (
              <span className="text-sm text-fg/65">No external posting URL was provided.</span>
            )}
            {job.auto_apply_state === 'needs_review' && (
              <button
                className="btn bg-accent text-paper hover:brightness-110"
                disabled={retry.isPending}
                onClick={() => retry.mutate()}
                title="Actually re-attempt the automated submission now"
              >
                <Zap size={15} />
                Retry auto-apply
              </button>
            )}
            <button
              className="btn bg-fg text-paper hover:brightness-110"
              disabled={patch.isPending}
              onClick={() => patch.mutate('applied')}
              title="Bookkeeping only — records that you applied yourself, doesn't submit anything"
            >
              Mark applied
            </button>
            <button
              className="btn bg-fg/5 text-fg/65 hover:bg-fg/10"
              disabled={patch.isPending}
              onClick={() => patch.mutate('skipped')}
            >
              Skip
            </button>
            <button
              className="btn bg-fg/5 text-fg/65 hover:bg-danger/10 hover:text-danger"
              disabled={deleteJob.isPending}
              onClick={handleDelete}
              aria-label={`Delete ${job.title}`}
            >
              <Trash2 size={15} />
              Delete
            </button>
          </div>
          {retry.isSuccess && (
            <p className="mt-3 text-sm text-accent">Retry started — check back shortly.</p>
          )}
          {retry.error && <p className="mt-3 text-sm text-danger">{retry.error.message}</p>}
          {patch.error && <p className="mt-3 text-sm text-danger">{patch.error.message}</p>}
          {deleteJob.error && <p className="mt-3 text-sm text-danger">{deleteJob.error.message}</p>}
        </div>
      )}
    </article>
  );
}
function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="m-5 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-danger/10 p-4 text-sm text-danger">
      <span>{error.message}</span>
      {onRetry && (
        <button type="button" className="btn btn-outline" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
export default WorkspaceView;
