import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from './api';

// Shared between WorkspaceView.tsx's job detail panel and BatchesPage.tsx's review
// list — same list-pending-questions / approve-or-dismiss UI either place a job's
// review_reason is "custom_questions". Approving an answer here is per-job only —
// it's what "Retry auto-apply" on *this* job picks up, but isn't saved anywhere
// for reuse on a different job's form.
export default function BlockedQuestionsPanel({
  workspaceId,
  jobId,
  enabled = true,
}: {
  workspaceId: number;
  jobId: number;
  enabled?: boolean;
}) {
  const qc = useQueryClient();
  const questionsQ = useQuery({
    queryKey: ['blocked-questions', workspaceId, jobId],
    queryFn: () => api.blockedQuestions(workspaceId, jobId),
    enabled,
  });
  const [drafts, setDrafts] = useState<Record<number, string>>({});

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['blocked-questions', workspaceId, jobId] });
    qc.invalidateQueries({ queryKey: ['jobs', workspaceId] });
  };
  const answerM = useMutation({
    mutationFn: ({ id, answer }: { id: number; answer: string }) =>
      api.answerBlockedQuestion(workspaceId, jobId, id, answer),
    onSuccess: invalidate,
  });
  const dismissM = useMutation({
    mutationFn: (id: number) => api.dismissBlockedQuestion(workspaceId, jobId, id),
    onSuccess: invalidate,
  });

  if (!enabled) return null;
  // Approved rows remain visible/editable. Hiding them made a bad approval
  // effectively permanent and produced an empty panel if that same answer later
  // failed replay and the job returned to review.
  const questions = (questionsQ.data ?? []).filter((q) => q.status !== 'dismissed');
  if (!questions.length) return null;

  return (
    <div className="space-y-3 rounded-xl border border-warning/30 bg-warning/5 p-4">
      <p className="text-xs font-bold uppercase tracking-wider text-warning">
        Review answers used for this application
      </p>
      {questions.map((q) => {
        const isCheckbox = q.field_type === 'checkbox';
        const isChoice = q.field_type === 'select' || q.field_type === 'radio' || isCheckbox;
        const options = isCheckbox ? ['Yes', 'No'] : q.options;
        // An AI draft is a suggestion, never the effective form value. Keep a
        // human answer prefilled even after a retry reopens the question: that
        // means the ATS control rejected replay, not that the applicant never
        // answered it. This makes constrained values correctable instead of lost.
        const value = drafts[q.id] ?? q.answer_text ?? '';
        return (
          <div key={q.id} className="rounded-lg bg-paper p-3">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-semibold">{q.question_text}</p>
              <span className="rounded-full bg-fg/5 px-2 py-0.5 text-[10px] font-bold uppercase text-fg/60">
                {q.status}
              </span>
            </div>
            {q.drafted_answer && (
              <div className="mb-2 mt-1 rounded-lg bg-accent/5 p-2 text-xs text-fg/70">
                <p className="font-bold uppercase tracking-wide text-accent">AI suggestion</p>
                <p className="mt-1">{q.drafted_answer}</p>
                <button
                  className="mt-1 font-semibold text-accent underline"
                  type="button"
                  onClick={() => setDrafts((d) => ({ ...d, [q.id]: q.drafted_answer ?? '' }))}
                >
                  Use this suggestion
                </button>
              </div>
            )}
            {isChoice ? (
              <select
                className="field mt-1 w-full"
                value={value}
                onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
              >
                <option value="">Choose an answer…</option>
                {options.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            ) : (
              <textarea
                className="field mt-1 w-full resize-none"
                rows={2}
                value={value}
                onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
              />
            )}
            <div className="mt-2 flex gap-2">
              <button
                className="btn bg-fg text-paper hover:brightness-110"
                disabled={!value.trim() || answerM.isPending}
                onClick={() => answerM.mutate({ id: q.id, answer: value })}
              >
                {q.status === 'approved' ? 'Save answer' : 'Approve'}
              </button>
              <button
                className="btn bg-fg/5 text-fg/65 hover:bg-fg/10"
                disabled={dismissM.isPending}
                onClick={() => dismissM.mutate(q.id)}
              >
                Dismiss
              </button>
            </div>
            {(answerM.isError || dismissM.isError) && (
              <p className="mt-2 text-sm text-danger">
                {(answerM.error ?? dismissM.error)?.message}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
