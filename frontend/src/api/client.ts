import type {
  ExamDetail,
  ExamRead,
  QuestionRead,
  Region,
  ExamKeyPage,
  SubmissionPage,
  SubmissionRead,
  SubmissionResults,
} from '../types/api';

const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() || '';
const BACKEND_API_KEY = import.meta.env.VITE_BACKEND_API_KEY?.trim() || '';
const API_BASE_URL = configuredApiBaseUrl;
const DEFAULT_TIMEOUT_MS = 20_000;
const EXAM_READ_TIMEOUT_MS = 15_000;
const EXAM_CREATE_TIMEOUT_MS = 20_000;
const KEY_UPLOAD_TIMEOUT_MS = 0;
const KEY_PARSE_TIMEOUT_MS = 120_000;
const BUILD_PAGES_TIMEOUT_MS = 60_000;

function validateApiBaseUrl(baseUrl: string): string | null {
  if (!baseUrl) {
    return 'Missing VITE_API_BASE_URL (must be https://.../api).';
  }
  if (!/^https?:\/\//i.test(baseUrl)) {
    return `Invalid VITE_API_BASE_URL: "${baseUrl}" must be an absolute http(s) URL ending in /api.`;
  }
  if (!/\/api\/?$/i.test(baseUrl)) {
    return `Invalid VITE_API_BASE_URL: "${baseUrl}" must end with /api.`;
  }
  return null;
}

const API_CONFIG_ERROR = validateApiBaseUrl(API_BASE_URL);

class ApiError extends Error {
  constructor(
    public status: number,
    public url: string,
    public method: string,
    public responseBodySnippet: string,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const REQUIRED_BACKEND_PATHS = [
  '/api/exams',
  '/api/exams/{exam_id}/key/upload',
  '/api/exams/{exam_id}/key/build-pages',
  '/api/exams/{exam_id}/key/parse',
  '/api/exams/{exam_id}/key/pages',
  '/api/exams/{exam_id}/key/review/complete',
] as const;

const NORMALIZED_REQUIRED_BACKEND_PATHS = REQUIRED_BACKEND_PATHS.map((path) => normalizeOpenApiPath(path));

type ApiContractCheckResult =
  | { ok: true }
  | {
    ok: false;
    missingPaths: string[];
    message: string;
    diagnostics: {
      openApiUrl: string;
      statusCode: number | null;
      responseSnippet: string;
      normalizedPathsFound: string[];
      normalizedRequiredPaths: string[];
    };
  };

let openApiPathCache: Set<string> | null = null;
let openApiFetchErrorMessage: string | null = null;

function withApiKeyHeader(options: RequestInit = {}): RequestInit {
  if (!BACKEND_API_KEY) return options;
  const headers = new Headers(options.headers || {});
  headers.set('X-API-Key', BACKEND_API_KEY);
  return { ...options, headers };
}

function getOpenApiSchemaUrl(): string {
  if (API_CONFIG_ERROR) {
    throw new Error(API_CONFIG_ERROR);
  }
  return `${API_BASE_URL.replace(/\/?api\/?$/i, '')}/openapi.json`;
}

function stripSnippet(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, 200);
}

function joinUrl(base: string, path: string): string {
  const b = base.replace(/\/+$/, '');
  const p = path.replace(/^\/+/, '');
  return `${b}/${p}`;
}

function buildApiUrl(path: string): string {
  if (API_CONFIG_ERROR) {
    throw new Error(API_CONFIG_ERROR);
  }
  return joinUrl(API_BASE_URL, path);
}

type FetchWithTimeoutResult = {
  response: Response;
  clear: () => void;
};

async function fetchWithTimeout(url: string, options: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<FetchWithTimeoutResult> {
  const inputSignal = options.signal;
  const timeoutController = timeoutMs > 0 ? new AbortController() : null;
  const requestController = timeoutController || inputSignal ? new AbortController() : null;
  let timeoutHandle: number | null = null;

  if (requestController && inputSignal) {
    if (inputSignal.aborted) {
      requestController.abort();
    } else {
      inputSignal.addEventListener('abort', () => requestController.abort(), { once: true });
    }
  }

  if (requestController && timeoutController) {
    timeoutHandle = window.setTimeout(() => timeoutController.abort(), timeoutMs);
    timeoutController.signal.addEventListener('abort', () => requestController.abort(), { once: true });
  }

  const fetchOptions: RequestInit = requestController
    ? { ...options, signal: requestController.signal }
    : options;

  const response = await fetch(url, fetchOptions);
  return {
    response,
    clear: () => {
      if (timeoutHandle !== null) {
        window.clearTimeout(timeoutHandle);
      }
    },
  };
}

async function request<T>(path: string, options: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  if (API_CONFIG_ERROR) {
    throw new Error(API_CONFIG_ERROR);
  }

  const normalizedPath = path.replace(/^\/+/, '');
  if (!normalizedPath) {
    throw new Error(`Invalid API path: "${path}"`);
  }

  const url = buildApiUrl(normalizedPath);
  const method = (options.method || 'GET').toUpperCase();
  const requestOptions = withApiKeyHeader(options);
  const { response, clear } = await fetchWithTimeout(url, requestOptions, timeoutMs);

  try {
    if (!response.ok) {
      const responseText = await response.text();
      throw buildErrorDetailsFromResponse(url, method, response.status, responseText);
    }

    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return (await response.json()) as T;
    }
    return {} as T;
  } catch (error) {
    throw error;
  } finally {
    clear();
  }
}

function buildErrorDetailsFromResponse(url: string, method: string, status: number, responseText: string): ApiError {
  const responseBodySnippet = responseText.slice(0, 300);
  let message = status === 401 ? 'Unauthorized (check API key config)' : `Request failed (${status})`;
  try {
    const body = JSON.parse(responseText) as { detail?: string };
    if (body?.detail) {
      message = body.detail;
    }
  } catch {
    // ignore parse error
  }

  return new ApiError(status, url, method, responseBodySnippet, `${message} [${status}] ${url}`);
}

type OpenApiFetchDiagnostics = {
  openApiUrl: string;
  statusCode: number | null;
  responseSnippet: string;
  normalizedPathsFound: string[];
  normalizedRequiredPaths: string[];
};

let openApiFetchDiagnostics: OpenApiFetchDiagnostics = {
  openApiUrl: '',
  statusCode: null,
  responseSnippet: '',
  normalizedPathsFound: [],
  normalizedRequiredPaths: NORMALIZED_REQUIRED_BACKEND_PATHS,
};

function setOpenApiFetchDiagnostics(diagnostics: OpenApiFetchDiagnostics): void {
  openApiFetchDiagnostics = diagnostics;
}

function normalizeOpenApiPath(path: string): string {
  return path.replace(/\{[^}]+\}/g, '{param}');
}

async function getOpenApiPaths(): Promise<Set<string>> {
  if (openApiPathCache) {
    return openApiPathCache;
  }

  const openApiUrl = getOpenApiSchemaUrl();
  setOpenApiFetchDiagnostics({
    openApiUrl,
    statusCode: null,
    responseSnippet: '',
    normalizedPathsFound: [],
    normalizedRequiredPaths: NORMALIZED_REQUIRED_BACKEND_PATHS,
  });

  try {
    const response = await fetch(openApiUrl, withApiKeyHeader());
    const responseText = await response.text();
    const responseSnippet = stripSnippet(responseText);
    setOpenApiFetchDiagnostics({
      openApiUrl,
      statusCode: response.status,
      responseSnippet,
      normalizedPathsFound: [],
      normalizedRequiredPaths: NORMALIZED_REQUIRED_BACKEND_PATHS,
    });

    if (!response.ok) {
      throw new Error(`Could not fetch backend OpenAPI at ${openApiUrl} (HTTP ${response.status})`);
    }

    const data = JSON.parse(responseText) as { paths?: Record<string, unknown> };
    const pathKeys = Object.keys(data.paths || {});
    openApiPathCache = new Set(pathKeys);
    const normalizedPathsFound = [...new Set(pathKeys.map((path) => normalizeOpenApiPath(path)))].sort().slice(0, 20);
    setOpenApiFetchDiagnostics({
      openApiUrl,
      statusCode: response.status,
      responseSnippet,
      normalizedPathsFound,
      normalizedRequiredPaths: NORMALIZED_REQUIRED_BACKEND_PATHS,
    });
    openApiFetchErrorMessage = null;
  } catch (error) {
    openApiFetchErrorMessage = `Could not fetch backend OpenAPI at ${openApiUrl}`;
    console.error(`[SuperMarks] ${openApiFetchErrorMessage}`, error);
    openApiPathCache = new Set<string>();
  }

  return openApiPathCache;
}

async function checkBackendApiContract(): Promise<ApiContractCheckResult> {
  const paths = await getOpenApiPaths();

  if (openApiFetchErrorMessage) {
    return {
      ok: false,
      missingPaths: [],
      message: openApiFetchErrorMessage,
      diagnostics: openApiFetchDiagnostics,
    };
  }

  const normalizedPaths = new Set([...paths].map((path) => normalizeOpenApiPath(path)));
  const missingPaths = REQUIRED_BACKEND_PATHS.filter((path) => !normalizedPaths.has(normalizeOpenApiPath(path)));

  if (missingPaths.length === 0) {
    return { ok: true };
  }

  const firstMissing = missingPaths[0];
  return {
    ok: false,
    missingPaths,
    message: `Backend API contract mismatch: missing endpoint ${firstMissing}. Please sync backend and frontend.`,
    diagnostics: openApiFetchDiagnostics,
  };
}

export function maskApiBaseUrl(baseUrl: string): string {
  if (!baseUrl) return '<missing>';
  try {
    const url = new URL(baseUrl);
    const host = url.host.replace(/(^.).*?(\..*$)/, '$1***$2');
    return `${url.protocol}//${host}${url.pathname}`;
  } catch {
    return '<invalid>';
  }
}

export function getHealthPingUrl(): string {
  if (API_CONFIG_ERROR) {
    throw new Error(API_CONFIG_ERROR);
  }
  return `${API_BASE_URL.replace(/\/?api\/?$/i, '')}/health`;
}

export async function pingApiHealth(): Promise<{ status: number; body: string }> {
  const url = getHealthPingUrl();
  const { response, clear } = await fetchWithTimeout(url, withApiKeyHeader(), DEFAULT_TIMEOUT_MS);
  try {
    const body = await response.text();
    return { status: response.status, body: body.slice(0, 300) };
  } finally {
    clear();
  }
}

export function getApiConfigError(): string | null {
  return API_CONFIG_ERROR;
}

export function resetApiContractCheckCache(): void {
  openApiPathCache = null;
  openApiFetchErrorMessage = null;
  openApiFetchDiagnostics = {
    openApiUrl: '',
    statusCode: null,
    responseSnippet: '',
    normalizedPathsFound: [],
    normalizedRequiredPaths: NORMALIZED_REQUIRED_BACKEND_PATHS,
  };
}

export const api = {
  getExams: (options?: RequestInit) => request<ExamRead[]>('exams', options, EXAM_READ_TIMEOUT_MS),
  createExam: (name: string, options?: RequestInit) => request<ExamRead>('exams', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
    ...options,
  }, EXAM_CREATE_TIMEOUT_MS),
  getExamDetail: (examId: number, options?: RequestInit) => request<ExamDetail>(`exams/${examId}`, options, EXAM_READ_TIMEOUT_MS),
  addQuestion: (examId: number, label: string, max_marks: number) => request<QuestionRead>(`exams/${examId}/questions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label, max_marks }),
  }),
  listQuestions: (examId: number) => request<QuestionRead[]>(`exams/${examId}/questions`),
  uploadSubmission: async (examId: number, studentName: string, files: File[]) => {
    const formData = new FormData();
    formData.append('student_name', studentName);
    files.forEach((file) => formData.append('files', file));
    return request<SubmissionRead>(`exams/${examId}/submissions`, { method: 'POST', body: formData }, DEFAULT_TIMEOUT_MS);
  },
  uploadExamKey: async (examId: number, files: File[], options?: RequestInit) => {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    return request<Record<string, unknown>>(`exams/${examId}/key/upload`, { method: 'POST', body: formData, ...options }, KEY_UPLOAD_TIMEOUT_MS);
  },
  getExamQuestionsForReview: async (examId: number) => {
    const paths = await getOpenApiPaths();

    if (paths.has('/api/exams/{exam_id}/questions')) {
      return request<QuestionRead[]>(`exams/${examId}/questions`);
    }

    const examDetail = await request<ExamDetail>(`exams/${examId}`);
    return examDetail.questions || [];
  },

  buildExamKeyPages: (examId: number, options?: RequestInit) => request<ExamKeyPage[]>(`exams/${examId}/key/build-pages`, { method: 'POST', ...options }, BUILD_PAGES_TIMEOUT_MS),
  listExamKeyPages: (examId: number) => request<ExamKeyPage[]>(`exams/${examId}/key/pages`),
  getExamKeyPageUrl: (examId: number, pageNumber: number) => buildApiUrl(`exams/${examId}/key/page/${pageNumber}`),
  completeExamKeyReview: (examId: number) => request<{ exam_id: number; status: string; warnings: string[] }>(`exams/${examId}/key/review/complete`, { method: 'POST' }),
  parseExamKey: (examId: number, options?: RequestInit) => request<Record<string, unknown>>(`exams/${examId}/key/parse`, { method: 'POST', ...options }, KEY_PARSE_TIMEOUT_MS),
  parseExamKeyRaw: async (examId: number, options?: RequestInit) => {
    const path = `exams/${examId}/key/parse`;
    const url = buildApiUrl(path);
    const method = 'POST';
    const requestOptions = withApiKeyHeader({ method, ...options });
    const { response, clear } = await fetchWithTimeout(url, requestOptions, KEY_PARSE_TIMEOUT_MS);
    try {
      const responseText = await response.text();

      if (!response.ok) {
        throw buildErrorDetailsFromResponse(url, method, response.status, responseText);
      }

      try {
        const data = JSON.parse(responseText) as unknown;
        return {
          data,
          responseText,
          status: response.status,
          url,
        };
      } catch {
        return {
          data: null,
          responseText,
          status: response.status,
          url,
        };
      }
    } finally {
      clear();
    }
  },
  getSubmission: (submissionId: number) => request<SubmissionRead>(`submissions/${submissionId}`),
  buildPages: (submissionId: number) => request<SubmissionPage[]>(`submissions/${submissionId}/build-pages`, { method: 'POST' }, BUILD_PAGES_TIMEOUT_MS),
  buildCrops: (submissionId: number) => request<{ message: string }>(`submissions/${submissionId}/build-crops`, { method: 'POST' }),
  transcribe: (submissionId: number) => request<{ message: string }>(`submissions/${submissionId}/transcribe?provider=stub`, { method: 'POST' }),
  grade: (submissionId: number) => request<{ message: string }>(`submissions/${submissionId}/grade?grader=rule_based`, { method: 'POST' }),
  getResults: (submissionId: number) => request<SubmissionResults>(`submissions/${submissionId}/results`),
  getPageImageUrl: (submissionId: number, pageNumber: number) => buildApiUrl(`submissions/${submissionId}/page/${pageNumber}`),
  getCropImageUrl: (submissionId: number, questionId: number) => buildApiUrl(`submissions/${submissionId}/crop/${questionId}`),
  getQuestionKeyVisualUrl: (examId: number, questionId: number) => buildApiUrl(`exams/${examId}/questions/${questionId}/key-visual`),
  saveRegions: (questionId: number, regions: Region[]) => request<Region[]>(`questions/${questionId}/regions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(regions),
  }),
  updateQuestion: async (examId: number, questionId: number, payload: { label: string; max_marks: number; rubric_json: Record<string, unknown> }) => {
    const paths = await getOpenApiPaths();
    if (paths.has('/api/questions/{question_id}')) {
      return request<QuestionRead>(`questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }, EXAM_READ_TIMEOUT_MS);
    }

    if (paths.has('/api/exams/{exam_id}/questions/{question_id}')) {
      return request<QuestionRead>(`exams/${examId}/questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }, EXAM_READ_TIMEOUT_MS);
    }

    if (paths.has('/api/exams/{exam_id}/wizard/questions/{question_id}')) {
      return request<QuestionRead>(`exams/${examId}/wizard/questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }, EXAM_READ_TIMEOUT_MS);
    }

    throw new ApiError(404, buildApiUrl(`questions/${questionId}`), 'PATCH', '', 'Save endpoint is not available. [404] dynamic endpoint discovery');
  },
};

export { API_BASE_URL, ApiError, buildApiUrl, checkBackendApiContract, getOpenApiPaths, joinUrl };
