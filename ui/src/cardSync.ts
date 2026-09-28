// Keep unsaved fields when background processing returns a newer server record.
export function mergeServerCard<T extends { id: string }>(
  draft: T, previous: T, incoming: T, editableFields: readonly (keyof T)[],
): T {
  if (draft.id !== incoming.id) return incoming;
  const merged = { ...incoming };
  for (const field of editableFields) {
    if ((draft[field] ?? '') !== (previous[field] ?? '')) merged[field] = draft[field];
  }
  return merged;
}
