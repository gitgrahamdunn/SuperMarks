import { api } from '../api/client';
import type {
  ClassListRead,
  ExamIntakeJobRead,
  ExamRead,
  ExamWorkspaceBootstrapResponse,
  FrontPageUsageReport,
} from '../types/api';

type HomeDataCacheEntry = {
  exams: ExamRead[];
  classLists: ClassListRead[];
  cachedAt: number;
};

type ExamWorkspaceCacheEntry = {
  bootstrap: ExamWorkspaceBootstrapResponse;
  intakeJob: ExamIntakeJobRead | null;
  usageReport: FrontPageUsageReport | null;
  cachedAt: number;
};

const HOME_CACHE_TTL_MS = 60_000;
const EXAM_WORKSPACE_CACHE_TTL_MS = 120_000;

let homeDataCache: HomeDataCacheEntry | null = null;
const examWorkspaceCache = new Map<number, ExamWorkspaceCacheEntry>();
const examWorkspacePrefetches = new Map<number, Promise<void>>();

function isFresh(cachedAt: number, ttlMs: number): boolean {
  return Date.now() - cachedAt < ttlMs;
}

export function readHomeDataCache(): HomeDataCacheEntry | null {
  if (!homeDataCache) return null;
  if (!isFresh(homeDataCache.cachedAt, HOME_CACHE_TTL_MS)) return null;
  return homeDataCache;
}

export function writeHomeDataCache(exams: ExamRead[], classLists: ClassListRead[]): void {
  homeDataCache = {
    exams,
    classLists,
    cachedAt: Date.now(),
  };
}

export function readExamWorkspaceCache(examId: number): ExamWorkspaceCacheEntry | null {
  const entry = examWorkspaceCache.get(examId);
  if (!entry) return null;
  if (!isFresh(entry.cachedAt, EXAM_WORKSPACE_CACHE_TTL_MS)) return null;
  return entry;
}

export function writeExamWorkspaceCache(
  examId: number,
  bootstrap: ExamWorkspaceBootstrapResponse,
  intakeJob: ExamIntakeJobRead | null,
  usageReport: FrontPageUsageReport | null,
): void {
  examWorkspaceCache.set(examId, {
    bootstrap,
    intakeJob,
    usageReport,
    cachedAt: Date.now(),
  });
}

export function prefetchExamWorkspace(examId: number): Promise<void> {
  const cached = readExamWorkspaceCache(examId);
  if (cached) return Promise.resolve();

  const pending = examWorkspacePrefetches.get(examId);
  if (pending) return pending;

  const request = Promise.all([
    api.getExamWorkspaceBootstrap(examId),
    api.getLatestExamIntakeJob(examId).catch(() => null),
    api.getExamFrontPageUsage(examId).catch(() => null),
  ])
    .then(([bootstrap, intakeJob, usageReport]) => {
      writeExamWorkspaceCache(examId, bootstrap, intakeJob, usageReport);
    })
    .finally(() => {
      examWorkspacePrefetches.delete(examId);
    });

  examWorkspacePrefetches.set(examId, request);
  return request;
}
