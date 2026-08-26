export type Status = 'discovered' | 'to_apply' | 'applied' | 'skipped';
export type AutoApplyState = 'applied_auto' | 'needs_review' | null;

export interface Job {
  id: number;
  external_id: string;
  title: string;
  company: string;
  ats_platform: string;
  apply_url: string;
  description: string;
  score: number;
  requirement_coverage: number;
  skill_coverage: number;
  global_similarity: number;
  matched_skills: string[];
  missing_skills: string[];
  weak_requirements: string[];
  status: Status;
  auto_apply_state: AutoApplyState;
  review_reason: string | null;
  date_fetched: string;
  date_scored: string | null;
  date_applied: string | null;
  created_at: string;
  updated_at: string;
}

export interface Workspace {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
  job_count: number;
  applied_count: number;
  above_threshold: number;
}

export interface WorkspaceSettings {
  resume: string;
  resume_filename: string | null;
  has_resume_file: boolean;
  threshold: number;
  auto_apply_threshold: number;
  profile_name: string;
  profile_email: string;
  profile_phone: string;
  profile_linkedin: string;
  profile_portfolio_url: string;
  profile_github_url: string;
  profile_location: string;
  profile_current_company: string;
  profile_current_title: string;
  profile_desired_salary: string;
  profile_start_date: string;
  profile_work_authorized: string;
  profile_visa_sponsorship: string;
  profile_willing_to_relocate: string;
  profile_18_or_older: string;
  profile_gender: string;
  profile_race_ethnicity: string;
  profile_veteran_status: string;
  profile_disability_status: string;
  cover_letter: string;
}

export interface Profile {
  name: string;
  email: string;
  phone: string;
  linkedin: string;
  portfolio_url: string;
  github_url: string;
  location: string;
  current_company: string;
  current_title: string;
  desired_salary: string;
  start_date: string;
  work_authorized: string;
  visa_sponsorship: string;
  willing_to_relocate: string;
  is_18_or_older: string;
  gender: string;
  race_ethnicity: string;
  veteran_status: string;
  disability_status: string;
  cover_letter: string;
}

// Verified directly against HiringCafe's own filter panels (not guessed) — these are
// the literal strings its search expects for `departments` / `seniorityLevel`.
export const DEPARTMENT_OPTIONS = [
  'Engineering',
  'Software Development',
  'Information Technology',
  'Data and Analytics',
  'Design',
  'Creative and Art Services',
  'Project and Program Management',
  'Product Management',
  'Business Operations',
  'Legal and Compliance',
  'Finance and Accounting',
  'Human Resources',
];
export const SENIORITY_OPTIONS = [
  'No Prior Experience Required',
  'Entry Level',
  'Mid Level',
  'Senior Level',
];

export interface DiscoveryFilters {
  job_title_query: string;
  technology_keywords_query: string;
  job_description_query: string;
  departments: string[];
  seniority: string[];
}

export interface Stats {
  total: number;
  by_status: Record<Status, number>;
  above_threshold: number;
  avg_score: number;
  median_score: number;
  score_histogram: { bucket: string; count: number }[];
  by_ats: { ats: string; count: number; avg_score: number }[];
  applied_over_time: { date: string; count: number }[];
}

export interface ScrapeStatus {
  running: boolean;
  collected: number;
  done: boolean;
  error: string | null;
  result: { fetched: number; new: number; updated: number; above_threshold: number } | null;
}

export type IntervalUnit = 'hour' | 'day' | 'week' | null;
export type RepeatMode = 'count' | 'indefinite';
export type BatchStatus = 'active' | 'paused' | 'completed';
export type BatchSource = 'search' | 'upload';

export interface Batch extends DiscoveryFilters {
  id: number;
  workspace_id: number;
  workspace_name: string;
  query: string;
  days: number;
  max_jobs: number;
  interval_unit: IntervalUnit;
  start_at: string;
  repeat_mode: RepeatMode;
  run_limit: number | null;
  runs_completed: number;
  auto_apply_threshold: number;
  source: BatchSource;
  status: BatchStatus;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BatchRun {
  id: number;
  batch_id: number;
  started_at: string;
  finished_at: string | null;
  fetched: number;
  new: number;
  updated: number;
  auto_applied: number;
  needs_review: number;
  status: string;
  error: string | null;
}

export interface DashboardStats {
  workspace_count: number;
  total_jobs: number;
  applied_total: number;
  applied_auto_total: number;
  needs_review_total: number;
  active_batches: number;
  total_batches: number;
  by_workspace: {
    workspace_id: number;
    name: string;
    total_jobs: number;
    applied: number;
    applied_auto: number;
    above_threshold: number;
  }[];
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}
async function upload<T>(url: string, body: FormData): Promise<T> {
  const res = await fetch(url, { method: 'POST', body });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Upload failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  workspaces: () => request<Workspace[]>('/api/workspaces'),
  createWorkspace: (name: string) =>
    request<Workspace>('/api/workspaces', { method: 'POST', body: JSON.stringify({ name }) }),
  renameWorkspace: (id: number, name: string) =>
    request<Workspace>(`/api/workspaces/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),
  deleteWorkspace: (id: number) => request<void>(`/api/workspaces/${id}`, { method: 'DELETE' }),

  settings: (workspaceId: number) =>
    request<WorkspaceSettings>(`/api/workspaces/${workspaceId}/settings`),
  saveResume: (workspaceId: number, text: string) =>
    request(`/api/workspaces/${workspaceId}/settings/resume`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  uploadResumePdf: (workspaceId: number, file: File) => {
    const body = new FormData();
    body.append('file', file);
    return upload<{ saved: boolean; text: string; filename: string; pages: number }>(
      `/api/workspaces/${workspaceId}/settings/resume/pdf`,
      body,
    );
  },
  saveThreshold: (workspaceId: number, value: number) =>
    request(`/api/workspaces/${workspaceId}/settings/threshold`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  saveAutoApplyThreshold: (workspaceId: number, value: number) =>
    request(`/api/workspaces/${workspaceId}/settings/auto-apply-threshold`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  saveProfile: (workspaceId: number, profile: Profile) =>
    request(`/api/workspaces/${workspaceId}/settings/profile`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),

  jobs: (workspaceId: number, params: URLSearchParams) =>
    request<Job[]>(`/api/workspaces/${workspaceId}/jobs?${params}`),
  stats: (workspaceId: number) => request<Stats>(`/api/workspaces/${workspaceId}/stats`),
  patchJob: (workspaceId: number, id: number, status: Status) =>
    request<Job>(`/api/workspaces/${workspaceId}/jobs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  scrape: (
    workspaceId: number,
    payload: { query: string; days: number; max_jobs: number } & Partial<DiscoveryFilters>,
  ) =>
    request<{ started: boolean }>(`/api/workspaces/${workspaceId}/scrape`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  scrapeStatus: (workspaceId: number) =>
    request<ScrapeStatus>(`/api/workspaces/${workspaceId}/scrape/status`),
  exportJobs: async (workspaceId: number, params: URLSearchParams) => {
    const res = await fetch(`/api/workspaces/${workspaceId}/jobs/export?${params}`);
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    return res.blob();
  },

  dashboard: () => request<DashboardStats>('/api/dashboard'),

  batches: () => request<Batch[]>('/api/batches'),
  createBatch: (
    payload: {
      workspace_id: number;
      query: string;
      days: number;
      max_jobs: number;
      interval_unit: IntervalUnit;
      start_at: string;
      repeat_mode: RepeatMode;
      run_limit: number | null;
      auto_apply_threshold: number;
    } & DiscoveryFilters,
  ) => request<Batch>('/api/batches', { method: 'POST', body: JSON.stringify(payload) }),
  createUploadBatch: (payload: {
    workspace_id: number;
    auto_apply_threshold: number;
    start_at: string;
    file: File;
  }) => {
    const body = new FormData();
    body.append('workspace_id', String(payload.workspace_id));
    body.append('auto_apply_threshold', String(payload.auto_apply_threshold));
    body.append('start_at', payload.start_at);
    body.append('file', payload.file);
    return upload<Batch>('/api/batches/upload', body);
  },
  setBatchStatus: (id: number, status: 'active' | 'paused') =>
    request<Batch>(`/api/batches/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  deleteBatch: (id: number) => request<void>(`/api/batches/${id}`, { method: 'DELETE' }),
  batchRuns: (id: number) => request<BatchRun[]>(`/api/batches/${id}/runs`),
  runBatchNow: (id: number) =>
    request<{ started: boolean }>(`/api/batches/${id}/run-now`, { method: 'POST' }),
};
