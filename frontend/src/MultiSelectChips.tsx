export default function MultiSelectChips({
  label,
  hint,
  options,
  selected,
  onToggle,
}: {
  label: string;
  hint?: string;
  options: string[];
  selected: string[];
  onToggle: (option: string) => void;
}) {
  return (
    <div>
      <p className="text-xs font-semibold text-fg/50">{label}</p>
      {hint && <p className="mt-0.5 text-xs text-fg/45">{hint}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => {
          const active = selected.includes(option);
          return (
            <button
              type="button"
              key={option}
              className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                active
                  ? 'border-accent bg-accent text-white'
                  : 'border-fg/20 text-fg/65 hover:border-accent/50 hover:text-accent'
              }`}
              onClick={() => onToggle(option)}
              aria-pressed={active}
            >
              {option}
            </button>
          );
        })}
      </div>
    </div>
  );
}
