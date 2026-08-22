export type Status = 'discovered' | 'to_apply' | 'applied' | 'skipped';
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
  date_fetched: string;
  date_scored: string | null;
  date_applied: string | null;
  created_at: string;
  updated_at: string;
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
export interface Settings {
  resume: string;
  threshold: number;
}
export interface ScrapeStatus {
  running: boolean;
  collected: number;
  done: boolean;
  error: string | null;
  result: { fetched: number; new: number; updated: number; above_threshold: number } | null;
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
  settings: () => request<Settings>('/api/settings'),
  saveResume: (text: string) =>
    request('/api/settings/resume', { method: 'POST', body: JSON.stringify({ text }) }),
  uploadResumePdf: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return upload<{ saved: boolean; text: string; filename: string; pages: number }>(
      '/api/settings/resume/pdf',
      body,
    );
  },
  saveThreshold: (value: number) =>
    request('/api/settings/threshold', { method: 'PUT', body: JSON.stringify({ value }) }),
  jobs: (params: URLSearchParams) => request<Job[]>(`/api/jobs?${params}`),
  stats: () => request<Stats>('/api/stats'),
  patchJob: (id: number, status: Status) =>
    request<Job>(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  scrape: (payload: { query: string; days: number; max_jobs: number }) =>
    request<{ started: boolean }>('/api/scrape', { method: 'POST', body: JSON.stringify(payload) }),
  scrapeStatus: () => request<ScrapeStatus>('/api/scrape/status'),
  exportJobs: async (params: URLSearchParams) => {
    const res = await fetch(`/api/jobs/export?${params}`);
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    return res.blob();
  },
};
