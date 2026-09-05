import { ChevronLeft, ChevronRight } from 'lucide-react';

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

export function Pagination({
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const hasPrev = page > 1;
  const hasNext = page < totalPages;

  return (
    <nav
      aria-label="Job results pages"
      className="flex flex-wrap items-center justify-between gap-3 border-t border-fg/10 px-5 py-4"
    >
      <label className="flex items-center gap-2 text-sm text-fg/65">
        Per page
        <select
          className="field"
          aria-label="Jobs per page"
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option value={size} key={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="btn btn-outline"
          aria-label="Previous page"
          disabled={!hasPrev}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={16} />
          Prev
        </button>
        <span className="mono-num text-sm text-fg/65" aria-live="polite">
          Page {page} of {totalPages}
        </span>
        <button
          type="button"
          className="btn btn-outline"
          aria-label="Next page"
          disabled={!hasNext}
          onClick={() => onPageChange(page + 1)}
        >
          Next
          <ChevronRight size={16} />
        </button>
      </div>
    </nav>
  );
}
