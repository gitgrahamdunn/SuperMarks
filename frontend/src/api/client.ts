import type {
  ExamCostResponse,
  ExamDetail,
  ExamRead,
  QuestionRead,
  QuestionMergeResponse,
  QuestionSplitResponse,
  Region,
  ExamKeyPage,
  SubmissionPage,
  SubmissionRead,
  SubmissionResults,
} from '../types/api';

const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() || '';
const API_BASE_URL = configuredApiBaseUrl.replace(/\/+$/, '');
const IS_PROD_ABSOLUTE_API_BASE_CONFIGURED = /^https?:\/\//i.test(API_BASE_URL);
const API_KEY = import.meta.env.VITE_BACKEND_API_KEY;

if (!API_BASE_URL) {
  throw new Error('Missing VITE_API_BASE_URL. Set it to your backend API base (for example, https://your-backend.example.com/api).');
}

if (import.meta.env.PROD && !IS_PROD_ABSOLUTE_API_BASE_CONFIGURED) {
  throw new Error(`Invalid VITE_API_BASE_URL for production: "${API_BASE_URL}". Use an absolute URL that already includes /api.`);
}

const BASE = API_BASE_URL;
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

class ApiInvalidJsonError extends Error {
  constructor(
    public url: string,
    public method: string,
    public contentType: string,
    public responseBodySnippet: string,
    message: string,
  ) {
    super(message);
    this.name = 'ApiInvalidJsonError';
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

function withAuthHeaders(headers?: HeadersInit): HeadersInit {
  const h = new Headers(headers || {});
  if (API_KEY) h.set('X-API-Key', API_KEY);
  return h;
}

function getOpenApiSchemaUrl(): string {
  return joinUrl(BASE, '/openapi.json');
}

function stripSnippet(text: string): string {
  return text.replace(/\s+/g, ' ').trim().slice(0, 200);
}

function joinUrl(base: string, path: string): string {
  const normalizedBase = base.trim().replace(/\/+$/, '');
  const normalizedPath = path.trim().replace(/^\/+/, '');

  if (!normalizedBase) {
    return normalizedPath ? `/${normalizedPath}` : '/';
  }

  if (!normalizedPath) {
    return normalizedBase;
  }

  return `${normalizedBase}/${normalizedPath}`;
}

function buildApiUrl(path: string): string {
  return `${BASE}${joinUrl('', path)}`;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const normalizedPath = path.trim().replace(/^\/+/, '');
  if (!normalizedPath) {
    throw new Error(`Invalid API path: "${path}"`);
  }

  const requestPath = `/${normalizedPath}`;
  const url = joinUrl(BASE, requestPath);
  const method = (options.method || 'GET').toUpperCase();
  options.headers = withAuthHeaders(options.headers);
  const response = await fetch(url, options);
  const responseText = await response.text();
  let parsedJson: unknown = null;

  try {
    parsedJson = responseText ? JSON.parse(responseText) : null;
  } catch {
    parsedJson = null;
  }

  if (!response.ok) {
    const responseBodySnippet = responseText.slice(0, 300);
    let message = response.status === 401 ? 'Unauthorized (check API key config)' : `Request failed (${response.status})`;
    if (parsedJson && typeof parsedJson === 'object' && 'detail' in parsedJson) {
      const detail = (parsedJson as { detail?: unknown }).detail;
      if (typeof detail === 'string' && detail.trim()) {
        message = detail;
      }
    }
    throw new ApiError(response.status, url, method, responseBodySnippet, `${message} [${response.status}] ${url}`);
  }

  if (parsedJson === null) {
    const contentType = response.headers.get('content-type') || '<missing>';
    const responseBodySnippet = responseText.slice(0, 300);
    throw new ApiInvalidJsonError(
      url,
      method,
      contentType,
      responseBodySnippet,
      `Expected JSON response from ${method} ${url}, but received empty or invalid JSON (Content-Type: ${contentType}).`,
    );
  }

  return parsedJson as T;
}


async function createExamRequest(name: string): Promise<ExamRead> {
  if (!API_KEY) {
    throw new Error('Missing VITE_BACKEND_API_KEY');
  }

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 20_000);

  try {
    const examName = name.trim() || `Exam ${Date.now()}`;
    const url = `/api/exams-create?name=${encodeURIComponent(examName)}`;
    const response = await fetch(url, {
      method: 'GET',
      headers: withAuthHeaders(),
      signal: controller.signal,
    });

    const responseText = await response.text();
    let parsedJson: unknown = null;

    try {
      parsedJson = responseText ? JSON.parse(responseText) : null;
    } catch {
      parsedJson = null;
    }

    if (!response.ok) {
      throw buildErrorDetailsFromResponse(url, 'GET', response.status, responseText);
    }

    if (parsedJson === null) {
      const contentType = response.headers.get('content-type') || '<missing>';
      throw new ApiInvalidJsonError(
        url,
        'GET',
        contentType,
        responseText.slice(0, 300),
        `Expected JSON response from GET ${url}, but received empty or invalid JSON (Content-Type: ${contentType}).`,
      );
    }

    return parsedJson as ExamRead;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('Create exam request timed out after 20 seconds. Please retry.');
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
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
  console.log(`[SuperMarks] Fetching backend OpenAPI schema from ${openApiUrl}`);

  try {
    const response = await fetch(openApiUrl, { headers: withAuthHeaders() });
    const responseText = await response.text();
    const responseSnippet = stripSnippet(responseText);
    setOpenApiFetchDiagnostics({
      openApiUrl,
      statusCode: response.status,
      responseSnippet,
      normalizedPathsFound: [],
      normalizedRequiredPaths: NORMALIZED_REQUIRED_BACKEND_PATHS,
    });
    console.log('[SuperMarks] OpenAPI fetch diagnostics', {
      openApiUrl,
      statusCode: response.status,
      responseSnippet,
    });

    if (!response.ok) {
      throw new Error(`Could not fetch backend OpenAPI at ${openApiUrl} (HTTP ${response.status})`);
    }

    const snippetLower = responseSnippet.toLowerCase();
    if (snippetLower.startsWith('<!doctype') || snippetLower.startsWith('<html')) {
      openApiFetchErrorMessage = `Backend OpenAPI endpoint returned HTML: ${openApiUrl}`;
      console.error(`[SuperMarks] ${openApiFetchErrorMessage}`);
      openApiPathCache = new Set<string>();
      return openApiPathCache;
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
    if (!openApiFetchErrorMessage) {
      openApiFetchErrorMessage = `Could not fetch backend OpenAPI at ${openApiUrl}`;
    }
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

  console.error('[SuperMarks] Backend API contract mismatches detected', {
    missingPaths,
    availablePaths: [...paths].sort(),
  });

  const firstMissing = missingPaths[0];
  return {
    ok: false,
    missingPaths,
    message: `Backend API contract mismatch: missing endpoint ${firstMissing}. Please sync backend and frontend.`,
    diagnostics: openApiFetchDiagnostics,
  };
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
  getExams: () => request<unknown>('exams'),
  createExam: (name: string) => createExamRequest(name),
  getExamDetail: (examId: number) => request<ExamDetail>(`exams/${examId}`),
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
    return request<SubmissionRead>(`exams/${examId}/submissions`, {
      method: 'POST',
      body: formData,
    });
  },
  uploadExamKey: async (examId: number, files: File[]) => {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    return request<Record<string, unknown>>(`exams/${examId}/key/upload`, {
      method: 'POST',
      body: formData,
    });
  },
  getExamQuestionsForReview: async (examId: number) => {
    const paths = await getOpenApiPaths();

    if (paths.has('/api/exams/{exam_id}/questions')) {
      return request<QuestionRead[]>(`exams/${examId}/questions`);
    }

    const examDetail = await request<ExamDetail>(`exams/${examId}`);
    return examDetail.questions || [];
  },

  buildExamKeyPages: (examId: number) => request<ExamKeyPage[]>(`exams/${examId}/key/build-pages`, { method: 'POST' }),
  listExamKeyPages: (examId: number) => request<ExamKeyPage[]>(`exams/${examId}/key/pages`),
  getExamKeyPageUrl: (examId: number, pageNumber: number) => buildApiUrl(`exams/${examId}/key/page/${pageNumber}`),
  completeExamKeyReview: (examId: number) => request<{ exam_id: number; status: string; warnings: string[] }>(`exams/${examId}/key/review/complete`, { method: 'POST' }),
  getExamCost: (examId: number) => request<ExamCostResponse>(`exams/${examId}/cost`),
  parseExamKey: (examId: number) => request<Record<string, unknown>>(`exams/${examId}/key/parse`, { method: 'POST' }),
  parseExamKeyRaw: async (examId: number) => {
    const path = `exams/${examId}/key/parse`;
    const url = buildApiUrl(path);
    const method = 'POST';
    const response = await fetch(url, { method, headers: withAuthHeaders() });
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
  },
  getSubmission: (submissionId: number) => request<SubmissionRead>(`submissions/${submissionId}`),
  buildPages: (submissionId: number) => request<SubmissionPage[]>(`submissions/${submissionId}/build-pages`, { method: 'POST' }),
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
      });
    }

    if (paths.has('/api/exams/{exam_id}/questions/{question_id}')) {
      return request<QuestionRead>(`exams/${examId}/questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }

    if (paths.has('/api/exams/{exam_id}/wizard/questions/{question_id}')) {
      return request<QuestionRead>(`exams/${examId}/wizard/questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }

    throw new ApiError(404, buildApiUrl(`questions/${questionId}`), 'PATCH', '', 'Save endpoint is not available. [404] dynamic endpoint discovery');
  },

  mergeQuestionWithNext: (examId: number, questionId: number) => request<QuestionMergeResponse>(`exams/${examId}/questions/${questionId}/merge-next`, { method: 'POST' }),
  splitQuestionByCriteria: (examId: number, questionId: number, criteriaSplitIndex: number) => request<QuestionSplitResponse>(`exams/${examId}/questions/${questionId}/split`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: 'criteria_index', criteria_split_index: criteriaSplitIndex }),
  }),
};

export {
  API_BASE_URL,
  API_KEY,
  ApiError,
  ApiInvalidJsonError,
  buildApiUrl,
  checkBackendApiContract,
  getOpenApiPaths,
  joinUrl,
};
