const LOG_STORAGE_KEY = 'supermarks:clientLogs';
const LOG_UPDATE_EVENT = 'supermarks:clientLogs:updated';
const MAX_STACK_LENGTH = 4000;
const MAX_LOG_ENTRIES = 200;

export type ClientLogType = 'window.error' | 'unhandledrejection' | 'app.info';

export interface ClientLogEntry {
  timestamp: string;
  type: ClientLogType;
  message: string;
  stack?: string;
  filename?: string;
  lineno?: number;
  colno?: number;
  count?: number;
}

export interface LogEventInput {
  type: ClientLogType;
  message?: string;
  stack?: string;
  filename?: string;
  lineno?: number;
  colno?: number;
}

function sanitizeStack(stack?: string): string | undefined {
  if (!stack) {
    return undefined;
  }

  const normalized = stack.trim();
  return normalized.length > MAX_STACK_LENGTH
    ? `${normalized.slice(0, MAX_STACK_LENGTH)}…`
    : normalized;
}

function safeReadEntries(): ClientLogEntry[] {
  try {
    const raw = localStorage.getItem(LOG_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function safeWriteEntries(entries: ClientLogEntry[]) {
  try {
    localStorage.setItem(LOG_STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // best effort persistence only
  }
}

function getFingerprint(entry: Pick<ClientLogEntry, 'type' | 'message' | 'stack' | 'filename' | 'lineno' | 'colno'>): string {
  return JSON.stringify({
    type: entry.type,
    message: entry.message.trim() || 'Unknown client error',
    stack: sanitizeStack(entry.stack) || '',
    filename: entry.filename || '',
    lineno: entry.lineno || 0,
    colno: entry.colno || 0,
  });
}

function toLogEntry(input: LogEventInput): ClientLogEntry {
  return {
    timestamp: new Date().toISOString(),
    type: input.type,
    message: input.message?.trim() || 'Unknown client error',
    filename: input.filename,
    lineno: input.lineno,
    colno: input.colno,
    stack: sanitizeStack(input.stack),
    count: 1,
  };
}

function notifyLogUpdate() {
  window.dispatchEvent(new CustomEvent(LOG_UPDATE_EVENT));
}

export function getClientLogs(): ClientLogEntry[] {
  return safeReadEntries();
}

export function clearClientLogs() {
  safeWriteEntries([]);
  notifyLogUpdate();
}

export function hasCapturedClientErrors(): boolean {
  return safeReadEntries().some((entry) => entry.type === 'window.error' || entry.type === 'unhandledrejection');
}

export function logEvent(event: LogEventInput) {
  const nextEntry = toLogEntry(event);
  const fingerprint = getFingerprint(nextEntry);
  const entries = safeReadEntries();
  const existingIndex = entries.findIndex((entry) => getFingerprint(entry) === fingerprint);

  if (existingIndex >= 0) {
    const existing = entries[existingIndex];
    entries[existingIndex] = {
      ...existing,
      timestamp: nextEntry.timestamp,
      count: (existing.count || 1) + 1,
    };
  } else {
    entries.unshift(nextEntry);
  }

  safeWriteEntries(entries.slice(0, MAX_LOG_ENTRIES));
  notifyLogUpdate();
}

export function subscribeToClientLogs(listener: () => void): () => void {
  window.addEventListener(LOG_UPDATE_EVENT, listener);
  return () => {
    window.removeEventListener(LOG_UPDATE_EVENT, listener);
  };
}
