export interface ExamRead {
  id: number;
  name: string;
  created_at: string;
  teacher_style_profile_json: string | null;
  status?: string;
}

export interface Region {
  id?: number;
  page_number: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface QuestionRead {
  id: number;
  exam_id: number;
  label: string;
  max_marks: number;
  rubric_json: Record<string, unknown>;
  regions: Region[];
}

export interface SubmissionFile {
  id: number;
  file_kind: string;
  original_filename: string;
  stored_path: string;
}

export interface SubmissionPage {
  id: number;
  page_number: number;
  image_path: string;
  width: number;
  height: number;
}

export interface SubmissionRead {
  id: number;
  exam_id: number;
  student_name: string;
  status: 'UPLOADED' | 'PAGES_READY' | 'CROPS_READY' | 'TRANSCRIBED' | 'GRADED' | string;
  created_at: string;
  files: SubmissionFile[];
  pages: SubmissionPage[];
}

export interface ExamDetail {
  exam: ExamRead;
  submissions: SubmissionRead[];
  questions: QuestionRead[];
}

export interface TranscriptionRead {
  question_id: number;
  text: string;
  confidence: number;
}

export interface GradeResultRead {
  question_id: number;
  marks_awarded: number;
  breakdown_json: Record<string, unknown>;
  feedback_json: Record<string, unknown>;
}

export interface SubmissionResults {
  submission_id: number;
  transcriptions: TranscriptionRead[];
  grades: GradeResultRead[];
}


export interface ExamKeyPage {
  id: number;
  exam_id: number;
  page_number: number;
  image_path: string;
  width: number;
  height: number;
}

export interface QuestionMergeResponse {
  question: QuestionRead;
  questions_count: number;
}

export interface QuestionSplitResponse {
  original: QuestionRead;
  created: QuestionRead;
  questions_count: number;
}


export interface ParseUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface ParseCost {
  input_cost: number;
  output_cost: number;
  total_cost: number;
}

export interface ExamCostBreakdown {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  total_cost: number;
}

export interface ExamCostResponse {
  total_cost: number;
  total_tokens: number;
  model_breakdown: Record<string, ExamCostBreakdown>;
}
