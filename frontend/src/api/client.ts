import type {
  ExamDetail,
  ExamRead,
  QuestionRead,
  Region,
  SubmissionPage,
  SubmissionRead,
  SubmissionResults,
} from '../types/api';

const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const API_BASE_URL = configuredApiBaseUrl || '/api';
const BACKEND_API_KEY = import.meta.env.VITE_BACKEND_API_KEY?.trim() || '';

if (import.meta.env.PROD && API_BASE_URL !== '/api') {
  console.info(`[SuperMarks] Production API base override detected: ${API_BASE_URL}`);
}

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

let openApiPathCache: Set<string> | null = null;

function withApiKeyHeader(options: RequestInit = {}): RequestInit {
  if (!BACKEND_API_KEY) return options;
  const headers = new Headers(options.headers || {});
  headers.set('X-API-Key', BACKEND_API_KEY);
  return { ...options, headers };
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (!path.startsWith('/') || path === '/') {
    throw new Error(`Invalid API path: "${path}"`);
  }

  const url = `${API_BASE_URL}${path}`;
  const method = (options.method || 'GET').toUpperCase();
  const response = await fetch(url, withApiKeyHeader(options));
  if (!response.ok) {
    const responseText = await response.text();
    const responseBodySnippet = responseText.slice(0, 300);
    let message = response.status === 401 ? 'Unauthorized (check API key config)' : `Request failed (${response.status})`;
    try {
      const body = JSON.parse(responseText) as { detail?: string };
      if (body?.detail) {
        message = body.detail;
      }
    } catch {
      // ignore parse error
    }
    throw new ApiError(response.status, url, method, responseBodySnippet, `${message} [${response.status}] ${url}`);
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return (await response.json()) as T;
  }
  return {} as T;
}

async function getOpenApiPaths(): Promise<Set<string>> {
  if (openApiPathCache) {
    return openApiPathCache;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/openapi.json`, withApiKeyHeader());
    if (!response.ok) {
      openApiPathCache = new Set<string>();
      return openApiPathCache;
    }
    const data = await response.json() as { paths?: Record<string, unknown> };
    openApiPathCache = new Set(Object.keys(data.paths || {}));
  } catch {
    openApiPathCache = new Set<string>();
  }

  return openApiPathCache;
}

export const api = {
  getExams: () => request<ExamRead[]>('/exams'),
  createExam: (name: string) => request<ExamRead>('/exams', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  }),
  getExamDetail: (examId: number) => request<ExamDetail>(`/exams/${examId}`),
  addQuestion: (examId: number, label: string, max_marks: number) => request<QuestionRead>(`/exams/${examId}/questions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label, max_marks }),
  }),
  listQuestions: (examId: number) => request<QuestionRead[]>(`/exams/${examId}/questions`),
  uploadSubmission: async (examId: number, studentName: string, files: File[]) => {
    const formData = new FormData();
    formData.append('student_name', studentName);
    files.forEach((file) => formData.append('files', file));
    return request<SubmissionRead>(`/exams/${examId}/submissions`, {
      method: 'POST',
      body: formData,
    });
  },
  uploadExamKey: async (examId: number, files: File[]) => {
    const paths = await getOpenApiPaths();
    const candidates = [
      '/exams/{exam_id}/key/upload',
      '/exams/{exam_id}/key',
      '/exams/{exam_id}/wizard/key',
      '/exams/{exam_id}/answer-key',
    ];
    const selectedPath = candidates.find((path) => paths.has(path));

    if (!selectedPath) {
      throw new ApiError(
        404,
        `${API_BASE_URL}/exams/${examId}/key/upload`,
        'POST',
        '',
        'No exam-key upload endpoint available. [404] dynamic endpoint discovery',
      );
    }

    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    return request<Record<string, unknown>>(selectedPath.replace('{exam_id}', String(examId)), {
      method: 'POST',
      body: formData,
    });
  },
  getExamQuestionsForReview: async (examId: number) => {
    const paths = await getOpenApiPaths();

    if (paths.has('/exams/{exam_id}/questions')) {
      return request<QuestionRead[]>(`/exams/${examId}/questions`);
    }

    const examDetail = await request<ExamDetail>(`/exams/${examId}`);
    return examDetail.questions || [];
  },
  parseExamKey: (examId: number) => request<Record<string, unknown>>(`/exams/${examId}/key/parse`, { method: 'POST' }),
  getSubmission: (submissionId: number) => request<SubmissionRead>(`/submissions/${submissionId}`),
  buildPages: (submissionId: number) => request<SubmissionPage[]>(`/submissions/${submissionId}/build-pages`, { method: 'POST' }),
  buildCrops: (submissionId: number) => request<{ message: string }>(`/submissions/${submissionId}/build-crops`, { method: 'POST' }),
  transcribe: (submissionId: number) => request<{ message: string }>(`/submissions/${submissionId}/transcribe?provider=stub`, { method: 'POST' }),
  grade: (submissionId: number) => request<{ message: string }>(`/submissions/${submissionId}/grade?grader=rule_based`, { method: 'POST' }),
  getResults: (submissionId: number) => request<SubmissionResults>(`/submissions/${submissionId}/results`),
  getPageImageUrl: (submissionId: number, pageNumber: number) => `${API_BASE_URL}/submissions/${submissionId}/page/${pageNumber}`,
  getCropImageUrl: (submissionId: number, questionId: number) => `${API_BASE_URL}/submissions/${submissionId}/crop/${questionId}`,
  saveRegions: (questionId: number, regions: Region[]) => request<Region[]>(`/questions/${questionId}/regions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(regions),
  }),
  updateQuestion: async (examId: number, questionId: number, payload: { label: string; max_marks: number; rubric_json: Record<string, unknown> }) => {
    const paths = await getOpenApiPaths();
    if (paths.has('/questions/{question_id}')) {
      return request<QuestionRead>(`/questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }

    if (paths.has('/exams/{exam_id}/questions/{question_id}')) {
      return request<QuestionRead>(`/exams/${examId}/questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }

    if (paths.has('/exams/{exam_id}/wizard/questions/{question_id}')) {
      return request<QuestionRead>(`/exams/${examId}/wizard/questions/${questionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }

    throw new ApiError(404, `${API_BASE_URL}/questions/${questionId}`, 'PATCH', '', 'Save endpoint is not available. [404] dynamic endpoint discovery');
  },
};

export { API_BASE_URL, ApiError, getOpenApiPaths };
