import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import WorkspaceView from './WorkspaceView';
import { api, Job, JobsPage, Stats, WorkspaceSettings } from './api';

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return {
    ...actual,
    api: {
      ...actual.api,
      settings: vi.fn(),
      stats: vi.fn(),
      jobs: vi.fn(),
      exportJobs: vi.fn(),
    },
  };
});

const mockedApi = vi.mocked(api);

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    external_id: 'ext-1',
    title: 'Backend Engineer',
    company: 'Acme',
    ats_platform: 'greenhouse',
    apply_url: 'https://example.com/apply',
    description: 'A great role.',
    score: 90,
    requirement_coverage: 0.9,
    skill_coverage: 0.9,
    global_similarity: 0.9,
    matched_skills: [],
    missing_skills: [],
    weak_requirements: [],
    status: 'discovered',
    auto_apply_state: null,
    review_reason: null,
    last_apply_started_at: null,
    last_apply_finished_at: null,
    last_apply_detail: null,
    source_batch_id: null,
    date_fetched: '2026-01-01T00:00:00Z',
    date_scored: '2026-01-01T00:00:00Z',
    date_applied: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makePage(overrides: Partial<JobsPage> = {}): JobsPage {
  return {
    items: Array.from({ length: 20 }, (_, i) => makeJob({ id: i + 1, title: `Engineer ${i + 1}` })),
    total: 45,
    page: 1,
    page_size: 20,
    ...overrides,
  };
}

const settingsFixture: WorkspaceSettings = {
  resume: '',
  resume_filename: null,
  has_resume_file: false,
  threshold: 82,
  auto_apply_threshold: 95,
  profile_name: '',
  profile_email: '',
  profile_phone: '',
  profile_linkedin: '',
  profile_portfolio_url: '',
  profile_github_url: '',
  profile_city: '',
  profile_state: '',
  profile_country: '',
  profile_location: '',
  profile_current_company: '',
  profile_current_title: '',
  profile_desired_salary: '',
  profile_start_date: '',
  profile_work_authorized: '',
  profile_visa_sponsorship: '',
  profile_willing_to_relocate: '',
  profile_18_or_older: '',
  profile_gender: '',
  profile_race_ethnicity: '',
  profile_veteran_status: '',
  profile_disability_status: '',
  profile_citizenship: '',
  profile_security_clearance: '',
  profile_background_check_consent: '',
  profile_drug_test_consent: '',
  profile_criminal_history: '',
  cover_letter: '',
};

const statsFixture: Stats = {
  total: 45,
  by_status: { discovered: 45, to_apply: 0, applied: 0, skipped: 0 },
  above_threshold: 5,
  avg_score: 70,
  median_score: 72,
  score_histogram: [],
  by_ats: [],
  applied_over_time: [],
};

function renderWorkspaceView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <WorkspaceView workspaceId={1} workspaceName="Test workspace" onBack={vi.fn()} />
    </QueryClientProvider>,
  );
}

function latestJobsCallParams(): URLSearchParams {
  const calls = mockedApi.jobs.mock.calls;
  const [, params] = calls[calls.length - 1];
  return params;
}

describe('WorkspaceView jobs pagination', () => {
  beforeEach(() => {
    mockedApi.settings.mockResolvedValue(settingsFixture);
    mockedApi.stats.mockResolvedValue(statsFixture);
    mockedApi.jobs.mockResolvedValue(makePage());
    mockedApi.exportJobs.mockResolvedValue(new Blob());
  });

  it('requests page 1 with the default page size on first load', async () => {
    renderWorkspaceView();
    await waitFor(() => expect(mockedApi.jobs).toHaveBeenCalled());
    const params = latestJobsCallParams();
    expect(params.get('page')).toBe('1');
    expect(params.get('page_size')).toBe('20');
  });

  it('combines status, min score, search, sort and order into the jobs request', async () => {
    const user = userEvent.setup();
    renderWorkspaceView();
    await waitFor(() => expect(mockedApi.jobs).toHaveBeenCalled());

    await user.selectOptions(screen.getByDisplayValue('All statuses'), 'applied');
    await user.type(screen.getByPlaceholderText('Minimum score'), '75');
    await user.type(screen.getByPlaceholderText('Search roles or companies'), 'engineer');

    await waitFor(
      () => {
        const params = latestJobsCallParams();
        expect(params.get('status')).toBe('applied');
        expect(params.get('min_score')).toBe('75');
        expect(params.get('q')).toBe('engineer');
        expect(params.get('sort')).toBe('score');
        expect(params.get('order')).toBe('desc');
      },
      { timeout: 2000 },
    );
  });

  it('resets to page 1 when a filter changes after navigating to a later page', async () => {
    const user = userEvent.setup();
    renderWorkspaceView();
    await waitFor(() => expect(mockedApi.jobs).toHaveBeenCalled());

    const nav = await screen.findByRole('navigation', { name: /job results pages/i });
    await user.click(within(nav).getByRole('button', { name: /next page/i }));
    await waitFor(() => expect(latestJobsCallParams().get('page')).toBe('2'));

    await user.selectOptions(screen.getByDisplayValue('All statuses'), 'applied');
    await waitFor(() => {
      const params = latestJobsCallParams();
      expect(params.get('status')).toBe('applied');
      expect(params.get('page')).toBe('1');
    });
  });

  it('never sends page/page_size to the export endpoint', async () => {
    const user = userEvent.setup();
    renderWorkspaceView();
    await waitFor(() => expect(mockedApi.jobs).toHaveBeenCalled());

    const nav = await screen.findByRole('navigation', { name: /job results pages/i });
    await user.click(within(nav).getByRole('button', { name: /next page/i }));
    await waitFor(() => expect(latestJobsCallParams().get('page')).toBe('2'));

    await user.click(screen.getByRole('button', { name: /download excel/i }));
    await waitFor(() => expect(mockedApi.exportJobs).toHaveBeenCalled());
    const [, exportParams] = mockedApi.exportJobs.mock.calls[0];
    expect(exportParams.has('page')).toBe(false);
    expect(exportParams.has('page_size')).toBe(false);
  });
});
