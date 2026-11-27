"use client";

// A labelled native select over fixed options (mockup Select), controlled by its caller
export function SelectField<V extends string>({ label, value, options, onChange, className }: {
  label?: string;
  value: V;
  options: Record<V, string>; // value → text
  onChange: (value: V) => void;
  className?: string;
}) {
  return (
    <label className={className ?? "block"}>
      {label && <span className="mb-1.5 block text-[13px] font-medium text-ink">{label}</span>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as V)}
        aria-label={label ? undefined : Object.values<string>(options).join("·")}
        className="h-10 w-full rounded-lg border border-line bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-4 focus:ring-brand-100"
      >
        {(Object.keys(options) as V[]).map((v) => <option key={v} value={v}>{options[v]}</option>)}
      </select>
    </label>
  );
}
