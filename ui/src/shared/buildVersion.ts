export function buildVersionLabel(version?: string | null) {
  const value = version?.trim();
  if (!value) return '不明';
  return value;
}
