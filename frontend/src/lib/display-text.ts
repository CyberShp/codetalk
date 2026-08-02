export function compactMiddle(value: string, max = 24): string {
  const text = value.trim();
  if (!text || text.length <= max) return text;
  const head = Math.max(6, Math.floor(max * 0.58));
  const tail = Math.max(4, max - head - 1);
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

export function isLongMachineToken(value: string): boolean {
  const text = value.trim();
  if (text.length < 24 || /\s/.test(text)) return false;
  return (
    /[0-9a-f]{8,}/i.test(text) ||
    /(?:^|[_:-])(?:task|run|job|case|semantic|workflow|workspace|import)[_-]/i.test(text) ||
    /^[a-z0-9][a-z0-9_-]{23,}$/i.test(text)
  );
}

export function compactMachineToken(value: string | null | undefined, max = 24): string {
  const text = String(value ?? "").trim();
  if (!text) return "—";
  return isLongMachineToken(text) ? compactMiddle(text, max) : text;
}
