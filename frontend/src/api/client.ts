import type {
  ExamDetail,
  ExamRead,
  QuestionRead,
  Region,
  SubmissionPage,
  SubmissionRead,
  SubmissionResults,
} from '../types/api';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) {
        message = body.detail;
      }
    } catch {
      // ignore parse error
    }
    throw new ApiError(response.status, message);
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return (await response.json()) as T;
  }
  return {} as T;
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
};

export { ApiError };
