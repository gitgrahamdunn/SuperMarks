export function formatStudentName(value: string | null | undefined): string {
  const collapsed = String(value || '').trim().split(/\s+/).filter(Boolean).join(' ');
  if (!collapsed) return '';
  return collapsed
    .split(' ')
    .map(formatToken)
    .join(' ');
}

export function splitStudentName(value: string | null | undefined): { firstName: string; lastName: string } {
  const normalized = formatStudentName(value);
  if (!normalized) return { firstName: '', lastName: '' };
  const parts = normalized.split(' ').filter(Boolean);
  if (parts.length === 1) return { firstName: parts[0], lastName: '' };
  return {
    firstName: parts.slice(0, -1).join(' '),
    lastName: parts[parts.length - 1],
  };
}

export function formatStudentNameParts(firstName: string | null | undefined, lastName: string | null | undefined): string {
  return [formatStudentName(firstName), formatStudentName(lastName)].filter(Boolean).join(' ').trim();
}

export function compareStudentNamesByLastName(left: string, right: string): number {
  const leftParts = formatStudentName(left).split(' ').filter(Boolean);
  const rightParts = formatStudentName(right).split(' ').filter(Boolean);
  const leftLast = (leftParts[leftParts.length - 1] || '').toLocaleLowerCase();
  const rightLast = (rightParts[rightParts.length - 1] || '').toLocaleLowerCase();
  const leftRest = leftParts.slice(0, -1).join(' ').toLocaleLowerCase();
  const rightRest = rightParts.slice(0, -1).join(' ').toLocaleLowerCase();
  return leftLast.localeCompare(rightLast) || leftRest.localeCompare(rightRest);
}

function formatToken(token: string): string {
  return token
    .split(/([-'])/g)
    .map((part) => (part === '-' || part === "'" ? part : part.slice(0, 1).toUpperCase() + part.slice(1).toLowerCase()))
    .join('');
}
