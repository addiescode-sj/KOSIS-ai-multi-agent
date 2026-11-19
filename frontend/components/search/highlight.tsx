// The first case-insensitive match of q in text, marked.
export function Highlight({ text, q }: { text: string; q: string }) {
  const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
  if (i < 0) return text;
  return <>{text.slice(0, i)}<mark className="rounded bg-brand-100 px-0.5 text-brand-700">{text.slice(i, i + q.length)}</mark>{text.slice(i + q.length)}</>;
}
