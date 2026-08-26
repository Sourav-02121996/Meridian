import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BriefcaseBusiness, Check, Plus, Target, Trash2, X } from 'lucide-react';
import { api, Workspace } from './api';

export default function WorkspaceGrid({ onOpen }: { onOpen: (workspace: Workspace) => void }) {
  const [createOpen, setCreateOpen] = useState(false);
  const workspacesQ = useQuery({ queryKey: ['workspaces'], queryFn: api.workspaces });

  return (
    <main className="mx-auto w-full max-w-[1500px] flex-1 space-y-7 px-5 py-10 lg:px-10">
      <section className="border-b border-black/15 pb-7">
        <p className="eyebrow mb-3 w-fit">Your workspaces</p>
        <h1 className="font-display text-4xl font-extrabold tracking-[-0.045em] sm:text-5xl">
          Pick up where you left off.
        </h1>
        <p className="mt-3 max-w-xl text-black/55">
          Each workspace keeps its own resume, thresholds, and job pipeline — one per role focus or
          search track.
        </p>
      </section>
      {workspacesQ.isLoading ? (
        <div className="p-12 text-center text-black/45">Loading your workspaces…</div>
      ) : (
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {workspacesQ.data?.map((workspace) => (
            <WorkspaceCard
              key={workspace.id}
              workspace={workspace}
              onOpen={() => onOpen(workspace)}
            />
          ))}
          <button
            className="card flex min-h-[180px] flex-col items-center justify-center gap-2 border-dashed text-black/40 transition hover:-translate-y-0.5 hover:border-sage hover:text-sage"
            onClick={() => setCreateOpen(true)}
            aria-label="Create new workspace"
          >
            <Plus size={28} />
            <span className="text-sm font-semibold">New workspace</span>
          </button>
        </div>
      )}
      {createOpen && <CreateWorkspaceDialog onClose={() => setCreateOpen(false)} />}
    </main>
  );
}

function WorkspaceCard({ workspace, onOpen }: { workspace: Workspace; onOpen: () => void }) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deleteM = useMutation({
    mutationFn: () => api.deleteWorkspace(workspace.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workspaces'] }),
  });
  return (
    <article className="card group relative flex flex-col justify-between p-6 transition hover:-translate-y-0.5 hover:shadow-[6px_6px_0_#2563eb]">
      <button
        className="absolute right-3 top-3 rounded-lg p-1.5 text-black/30 opacity-0 transition hover:bg-red-50 hover:text-red-600 group-hover:opacity-100"
        onClick={(e) => {
          e.stopPropagation();
          setConfirmDelete(true);
        }}
        aria-label={`Delete ${workspace.name}`}
      >
        <Trash2 size={16} />
      </button>
      <button className="text-left" onClick={onOpen}>
        <div className="mb-4 flex items-center gap-2 text-sage">
          <BriefcaseBusiness size={20} />
        </div>
        <h3 className="font-display text-lg font-bold">{workspace.name}</h3>
        <div className="mt-4 flex items-center gap-4 text-sm text-black/50">
          <span>{workspace.job_count} jobs</span>
          <span className="flex items-center gap-1">
            <Target size={13} /> {workspace.above_threshold} strong
          </span>
          <span className="flex items-center gap-1">
            <Check size={13} /> {workspace.applied_count} applied
          </span>
        </div>
      </button>
      {confirmDelete && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
          onMouseDown={(event) => event.target === event.currentTarget && setConfirmDelete(false)}
        >
          <div
            className="w-full max-w-sm border-2 border-black bg-white p-6"
            role="dialog"
            aria-modal="true"
          >
            <p className="font-display font-bold">Delete "{workspace.name}"?</p>
            <p className="mt-2 text-sm text-black/55">
              This permanently removes its resume, settings, and all {workspace.job_count} jobs.
            </p>
            {deleteM.error && <p className="mt-2 text-sm text-red-600">{deleteM.error.message}</p>}
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn btn-outline" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
              <button
                className="btn bg-red-600 text-white hover:bg-red-700"
                disabled={deleteM.isPending}
                onClick={() => deleteM.mutate()}
              >
                {deleteM.isPending ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

export function CreateWorkspaceDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState('');
  const qc = useQueryClient();
  const createM = useMutation({
    mutationFn: () => api.createWorkspace(name.trim()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspaces'] });
      onClose();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) createM.mutate();
  };
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4 backdrop-blur-sm"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        className="w-full max-w-md border-2 border-black bg-white p-7"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-workspace-title"
      >
        <div className="flex items-start justify-between">
          <h2
            id="create-workspace-title"
            className="font-display text-2xl font-extrabold tracking-tight"
          >
            New workspace
          </h2>
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={19} />
          </button>
        </div>
        <p className="mt-2 text-sm text-black/55">
          Give it a name — you'll set its resume and search filters once it's open.
        </p>
        <form className="mt-6" onSubmit={submit}>
          <label className="text-sm font-bold" htmlFor="workspace-name">
            Workspace name
          </label>
          <input
            id="workspace-name"
            className="field mt-2 w-full"
            placeholder="e.g. New Grad SWE"
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          {createM.error && <p className="mt-2 text-sm text-red-600">{createM.error.message}</p>}
          <button
            className="btn btn-dark mt-5 w-full py-3"
            type="submit"
            disabled={createM.isPending}
          >
            {createM.isPending ? 'Creating…' : 'Create workspace'}
          </button>
        </form>
      </section>
    </div>
  );
}
