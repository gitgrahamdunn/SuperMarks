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
  blob_url?: string | null;
  content_type?: string;
  size_bytes?: number;
}

export interface SubmissionPage {
  id: number;
  page_number: number;
  image_path: string;
  width: number;
  height: number;
}

export interface ExamParseJobRead {
  id: number;
  exam_id: number;
  status: 'running' | 'done' | 'failed' | string;
  page_count: number;
  pages_done: number;
  created_at: string;
  updated_at: string;
  cost_total: number;
  input_tokens_total: number;
  output_tokens_total: number;
}

export type SubmissionCaptureMode = 'question_level' | 'front_page_totals' | string;

export interface FrontPageObjectiveScore {
  objective_code: string;
  marks_awarded: number;
  max_marks?: number | null;
}

export interface FrontPageTotals {
  overall_marks_awarded: number;
  overall_max_marks?: number | null;
  objective_scores: FrontPageObjectiveScore[];
  teacher_note: string;
  confirmed: boolean;
  reviewed_at?: string | null;
}

export interface FrontPageExtractionEvidence {
  page_number: number;
  quote: string;
  x?: number | null;
  y?: number | null;
  w?: number | null;
  h?: number | null;
}

export interface FrontPageCandidateValue {
  value_text: string;
  confidence: number;
  evidence: FrontPageExtractionEvidence[];
}

export interface FrontPageObjectiveScoreCandidate {
  objective_code: FrontPageCandidateValue;
  marks_awarded: FrontPageCandidateValue;
  max_marks?: FrontPageCandidateValue | null;
}

export interface FrontPageTotalsCandidate {
  student_name?: FrontPageCandidateValue | null;
  overall_marks_awarded?: FrontPageCandidateValue | null;
  overall_max_marks?: FrontPageCandidateValue | null;
  objective_scores: FrontPageObjectiveScoreCandidate[];
  warnings: string[];
  source: string;
}

export interface SubmissionRead {
  id: number;
  exam_id: number;
  student_name: string;
  status: 'UPLOADED' | 'PAGES_READY' | 'CROPS_READY' | 'TRANSCRIBED' | 'GRADED' | string;
  capture_mode: SubmissionCaptureMode;
  front_page_totals?: FrontPageTotals | null;
  created_at: string;
  files: SubmissionFile[];
  pages: SubmissionPage[];
}

export interface ExamDetail {
  exam: ExamRead;
  key_files: StoredFileRead[];
  submissions: SubmissionRead[];
  parse_jobs: ExamParseJobRead[];
}

export interface TranscriptionRead {
  question_id: number;
  text: string;
  confidence: number;
}

export interface GradeResultRead {
  id?: number;
  submission_id?: number;
  question_id: number;
  marks_awarded: number;
  breakdown_json: Record<string, unknown>;
  feedback_json: Record<string, unknown>;
  model_name?: string;
}


export interface SubmissionPrepareQuestionStatus {
  question_id: number;
  question_label: string;
  ready: boolean;
  flagged_reasons: string[];
  blocking_reasons: string[];
  asset_state: string;
  has_regions: boolean;
  has_crop: boolean;
  has_transcription: boolean;
  has_manual_grade: boolean;
  stale_crop: boolean;
  stale_transcription: boolean;
  transcription_confidence?: number | null;
}

export interface SubmissionPrepareStatus {
  submission_id: number;
  ready_for_marking: boolean;
  can_prepare_now: boolean;
  summary_reasons: string[];
  suggested_actions: string[];
  blocked_actions: string[];
  unsafe_to_retry_reasons: string[];
  questions_total: number;
  questions_ready: number;
  manual_marked_questions: number;
  pages_count: number;
  missing_page_numbers: number[];
  actions_run: string[];
  questions: SubmissionPrepareQuestionStatus[];
}

export interface SubmissionResults {
  submission_id: number;
  capture_mode: SubmissionCaptureMode;
  total_score: number;
  total_possible: number;
  objective_totals: ObjectiveTotalRead[];
  front_page_totals?: FrontPageTotals | null;
  transcriptions: TranscriptionRead[];
  grades: GradeResultRead[];
}

export interface ManualGradePayload {
  marks_awarded: number;
  teacher_note: string;
}

export interface ExamKeyPage {
  id: number;
  exam_id: number;
  page_number: number;
  image_path: string;
  blob_pathname?: string | null;
  blob_url?: string | null;
  exists_on_disk: boolean;
  exists_on_storage: boolean;
  width: number;
  height: number;
}


export interface NameEvidence {
  page_number: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface BulkUploadCandidate {
  candidate_id: string;
  student_name: string;
  confidence: number;
  page_start: number;
  page_end: number;
  needs_review: boolean;
  name_evidence: NameEvidence | null;
}

export interface BulkUploadPreview {
  bulk_upload_id: number;
  page_count: number;
  candidates: BulkUploadCandidate[];
  warnings: string[];
}

export interface BulkFinalizePayloadCandidate {
  student_name: string;
  page_start: number;
  page_end: number;
}

export interface BulkFinalizeResponse {
  submissions: SubmissionRead[];
  warnings: string[];
}


export interface ParseStartResponse {
  job_id: number;
  page_count: number;
  pages_done: number;
  request_id?: string;
  reused?: boolean;
}

export interface ParseNextResponse {
  job_id: number;
  pages_processed: number[];
  page_count: number;
  pages_done: number;
  status: 'running' | 'done' | 'failed';
  page_results?: Array<{
    page_number: number;
    status?: 'pending' | 'running' | 'done' | 'failed';
    tried_models?: string[];
    first_attempt_confidence?: number;
    confidence?: number;
    model_used?: string;
  }>;
  totals?: { cost_total: number; input_tokens_total: number; output_tokens_total: number }
}

export interface ParseStatusPage {
  page_number: number;
  status: 'pending' | 'running' | 'done' | 'failed';
  model_used?: string | null;
  confidence?: number | null;
  elapsed_ms?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  input_tokens?: number;
  output_tokens?: number;
  cost?: number;
  should_escalate?: boolean | null;
  escalation_reasons?: string[];
  tried_models?: string[];
}

export interface ParseStatusResponse {
  job_id: number;
  exam_exists: boolean;
  job_exists: boolean;
  status: 'running' | 'done' | 'failed';
  page_count: number;
  pages_done: number;
  pages: ParseStatusPage[];
  totals?: { cost_total: number; input_tokens_total: number; output_tokens_total: number };
  warnings?: string[];
}

export interface ParseFinishResponse {
  job_id: number;
  status: string;
  pages_done?: number;
  page_count?: number;
  totals?: { cost_total: number; input_tokens_total: number; output_tokens_total: number };
  questions: unknown[];
}

export interface ParseRetryResponse {
  job_id: number;
  request_id: string;
  page_number: number;
  status: 'pending' | 'running' | 'done' | 'failed';
  page?: ParseStatusPage;
  pages_done: number;
  page_count: number;
  job_status: 'running' | 'done' | 'failed';
  totals?: { cost_total: number; input_tokens_total: number; output_tokens_total: number };
  questions: QuestionRead[];
}

export interface ParseLatestResponse {
  exam_exists: boolean;
  job: {
    job_id: number;
    request_id: string;
    status: 'running' | 'done' | 'failed';
    page_count: number;
    pages_done: number;
    has_remaining_work: boolean;
    failed_pages: number[];
    pending_pages: number[];
    totals: { cost_total: number; input_tokens_total: number; output_tokens_total: number };
    created_at: string;
    updated_at: string;
  } | null;
}


export interface StoredFileRead {
  id: number;
  original_filename: string;
  stored_path: string;
  content_type: string;
  size_bytes: number;
  signed_url: string;
  blob_url?: string | null;
}

export interface ObjectiveTotalRead {
  objective_code: string;
  marks_awarded: number;
  max_marks: number;
  questions_count: number;
}

export interface SubmissionDashboardRow {
  submission_id: number;
  student_name: string;
  capture_mode: SubmissionCaptureMode;
  workflow_status: 'ready' | 'blocked' | 'in_progress' | 'complete' | string;
  flagged_count: number;
  questions_total: number;
  teacher_marked_questions: number;
  marking_progress: string;
  running_total: number;
  total_possible: number;
  objective_totals: ObjectiveTotalRead[];
  ready_for_marking: boolean;
  can_prepare_now: boolean;
  summary_reasons: string[];
  next_question_id?: number | null;
  next_question_label?: string | null;
  next_action_text?: string | null;
  export_ready?: boolean;
  reporting_attention?: string;
  next_return_point?: string;
  next_action?: string;
}

export interface ObjectiveAttentionSubmissionRead {
  submission_id: number;
  student_name: string;
  capture_mode: 'question_level' | 'front_page_totals';
  workflow_status: string;
  objective_percent: number | '';
  next_return_point: string;
  next_action: string;
}

export interface ObjectiveCompleteSubmissionRead {
  submission_id: number;
  student_name: string;
  capture_mode: 'question_level' | 'front_page_totals';
  objective_percent: number | '';
}

export interface ExamObjectiveRead {
  objective_code: string;
  marks_awarded: number;
  max_marks: number;
  questions_count: number;
  submissions_with_objective: number;
  complete_submissions_with_objective: number;
  incomplete_submissions_with_objective: number;
  total_awarded_complete: number;
  total_max_complete: number;
  average_awarded_complete: number | '';
  average_percent_complete: number | '';
  total_awarded_all_current: number;
  total_max_all_current: number;
  average_percent_all_current: number | '';
  strongest_complete_student: string;
  strongest_complete_percent: number | '';
  weakest_complete_student: string;
  weakest_complete_percent: number | '';
  weakest_complete_submission?: ObjectiveCompleteSubmissionRead | null;
  teacher_summary: string;
  attention_submissions: ObjectiveAttentionSubmissionRead[];
}

export interface ExamCompletionSummary {
  total_submissions: number;
  ready_count: number;
  blocked_count: number;
  in_progress_count: number;
  complete_count: number;
  completion_percent: number;
}

export interface ExamMarkingDashboardResponse {
  exam_id: number;
  exam_name: string;
  total_possible: number;
  objectives: ExamObjectiveRead[];
  submissions: SubmissionDashboardRow[];
  completion: ExamCompletionSummary;
}
