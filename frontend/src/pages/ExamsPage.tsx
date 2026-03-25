import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ApiError, api, buildApiUrl } from '../api/client';
import { FileUploader } from '../components/FileUploader';
import { Modal } from '../components/Modal';
import { useToast } from '../components/ToastProvider';
import type { ClassListRead, ExamIntakeJobRead, ExamRead } from '../types/api';

type WizardStep = 'creating' | 'uploading' | 'building_pages' | 'parsing' | 'done';
type ParseChecklistStepId =
  | 'creating_exam'
  | 'uploading_key'
  | 'building_key_pages'
  | 'reading_questions'
  | 'detecting_marks'
  | 'drafting_rubric'
  | 'finalizing';
type ParseChecklistStatus = 'pending' | 'active' | 'done' | 'failed';

type WizardError = {
  step: WizardStep;
  summary: string;
  details: unknown;
  attemptedUrl: string;
  method: string;
  status: number | 'network-error' | 'unknown';
  contentType?: string | null;
  bodySnippet?: string;
  isAbort?: boolean;
};

type ParseErrorDetails = {
  stage?: string;
  page_index?: number;
  page_count?: number;
};

type ParseChecklistStep = {
  id: ParseChecklistStepId;
  label: string;
  status: ParseChecklistStatus;
};

type ThinkingLevel = 'off' | 'low' | 'med' | 'high';

const MB = 1024 * 1024;
const LARGE_FILE_BYTES = 8 * MB;
const LARGE_TOTAL_BYTES = 12 * MB;
const CHECKLIST_ORDER: Array<{ id: ParseChecklistStepId; label: string }> = [
  { id: 'creating_exam', label: 'Creating test workspace' },
  { id: 'uploading_key', label: 'Uploading test bundle' },
  { id: 'building_key_pages', label: 'Preparing page images' },
  { id: 'reading_questions', label: 'Extracting student names' },
  { id: 'detecting_marks', label: 'Building totals queue' },
  { id: 'drafting_rubric', label: 'Preparing confirmation handoff' },
  { id: 'finalizing', label: 'Finalizing exam workspace' },
];

const initChecklist = (): ParseChecklistStep[] => CHECKLIST_ORDER.map((step) => ({ ...step, status: 'pending' }));

const stageToChecklistId = (stage?: string): ParseChecklistStepId => {
  if (!stage) return 'finalizing';
  if (stage.includes('call_openai')) return 'reading_questions';
  if (stage.includes('validate')) return 'detecting_marks';
  if (stage.includes('save')) return 'drafting_rubric';
  if (stage.includes('build_key_pages')) return 'building_key_pages';
  if (stage.includes('upload')) return 'uploading_key';
  if (stage.includes('create')) return 'creating_exam';
  return 'finalizing';
};

const formatElapsed = (totalSeconds: number) => {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, '0');
  const seconds = (totalSeconds % 60).toString().padStart(2, '0');
  return `${minutes}:${seconds}`;
};

const formatMb = (bytes: number) => `${(bytes / MB).toFixed(2)} MB`;

const THINKING_LEVEL_OPTIONS: Array<{ value: ThinkingLevel; label: string }> = [
  { value: 'off', label: 'Off' },
  { value: 'low', label: 'Low' },
  { value: 'med', label: 'Med' },
  { value: 'high', label: 'High' },
];
const isNetworkFetchError = (error: unknown) =>
  error instanceof Error && (error instanceof TypeError || /load failed|failed to fetch|network/i.test(error.message));

const isAbortError = (error: unknown) => {
  if (error instanceof DOMException && error.name === 'AbortError') return true;
  return error instanceof Error && (error.name === 'AbortError' || /aborted/i.test(error.message));
};

const formatExamDate = (value: string) => {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Unknown date';
  return parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
};

const formatExamDateTime = (value: string) => {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Unknown time';
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
};

const normalizeExamStatus = (exam: ExamRead) => {
  const intakeStatus = exam.intake_job?.status?.trim().toLowerCase();
  const initialReviewReady = Boolean(exam.intake_job?.initial_review_ready || exam.intake_job?.review_ready);
  if (intakeStatus === 'queued' || intakeStatus === 'running') {
    if (initialReviewReady) {
      return { label: 'Ready', tone: 'status-ready' };
    }
    return { label: 'Working', tone: 'status-in-progress' };
  }
  if (intakeStatus === 'failed') {
    if (initialReviewReady) {
      return { label: 'Ready', tone: 'status-ready' };
    }
    return { label: 'Failed', tone: 'status-blocked' };
  }

  const normalized = exam.status?.trim().toLowerCase();
  if (!normalized) {
    return { label: 'Ready', tone: 'status-ready' };
  }
  if (normalized === 'draft') {
    if (!exam.intake_job) {
      return { label: 'Upload interrupted', tone: 'status-blocked' };
    }
    return { label: 'Working', tone: 'status-in-progress' };
  }
  if (normalized === 'ready' || normalized.includes('confirm')) {
    return { label: 'Checked', tone: 'status-complete' };
  }
  if (normalized.includes('complete') || normalized.includes('done')) {
    return { label: 'Checked', tone: 'status-complete' };
  }
  if (normalized.includes('progress') || normalized.includes('review')) {
    return { label: 'Ready', tone: 'status-ready' };
  }
  if (normalized.includes('block') || normalized.includes('flag')) {
    return { label: 'Needs review', tone: 'status-blocked' };
  }
  return { label: exam.status || 'Active workspace', tone: 'status-neutral' };
};

const libraryStatusClassName = (label: string) => {
  if (label === 'Working') return 'status-library-working';
  if (label === 'Ready') return 'status-library-ready';
  if (label === 'Checked') return 'status-library-checked';
  return '';
};

const PREPARING_STAGE_LABELS: Record<string, string> = {
  queued: 'Preparing files',
  resuming: 'Resuming',
  extracting_front_pages: 'Reading front pages',
  detecting_names: 'Reading names',
  creating_submissions: 'Building review queue',
  warming_initial_review: 'Preparing first papers',
  warming_remaining_review: 'Preparing remaining papers',
  warming_review: 'Preparing review',
  finalizing_review: 'Finalizing review',
  complete: 'Ready to review',
  partial_ready: 'Review ready',
  review_not_ready: 'Review blocked',
  stalled: 'Needs retry',
};

const formatIntakeStage = (job: ExamIntakeJobRead | null | undefined) => {
  if (!job) return 'Preparing';
  return PREPARING_STAGE_LABELS[job.stage] || job.stage.replace(/_/g, ' ');
};

const formatPreparingSummary = (job: ExamIntakeJobRead | null | undefined) => {
  if (!job) return 'Preparing review';
  const pageCount = Math.max(job.page_count || 0, 0);
  const pagesBuilt = Math.min(Math.max(job.pages_built || 0, 0), pageCount || 0);
  const submissionsCreated = Math.max(job.submissions_created || 0, 0);
  const candidatesReady = Math.min(Math.max(job.candidates_ready || 0, 0), submissionsCreated || 0);
  const reviewOpenThreshold = Math.min(Math.max(job.review_open_threshold || 0, 0), submissionsCreated || 0);
  const parts = [`${pagesBuilt}/${pageCount || 0} pages built`];
  if (submissionsCreated > 0 && reviewOpenThreshold > 0 && !job.initial_review_ready) {
    parts.push(`${Math.min(candidatesReady, reviewOpenThreshold)}/${reviewOpenThreshold} papers ready to open`);
  } else if (submissionsCreated > 0 && reviewOpenThreshold > 0 && !job.fully_warmed) {
    parts.push(`${reviewOpenThreshold}/${reviewOpenThreshold} papers ready to open`);
    parts.push(`${candidatesReady}/${submissionsCreated} total papers ready`);
  } else if (submissionsCreated > 0) {
    parts.push(`${candidatesReady}/${submissionsCreated} review items ready`);
  }
  return parts.join(' · ');
};

const intakeProgressPercent = (job: ExamIntakeJobRead | null | undefined) => {
  if (!job) return 8;
  if (job.fully_warmed || (job.status === 'complete' && job.fully_warmed)) return 100;

  const pageCount = Math.max(job.page_count || 0, 1);
  const pagesBuiltProgress = Math.min((job.pages_built || 0) / pageCount, 1);
  const pagesProcessedProgress = Math.min((job.pages_processed || 0) / pageCount, 1);
  const submissionsCreatedProgress = job.submissions_created > 0 ? 1 : 0;
  const reviewOpenThreshold = Math.max(job.review_open_threshold || 0, 1);
  const initialReviewProgress = job.submissions_created > 0
    ? Math.min((job.candidates_ready || 0) / reviewOpenThreshold, 1)
    : 0;
  const candidatesReadyProgress = job.submissions_created > 0
    ? Math.min((job.candidates_ready || 0) / job.submissions_created, 1)
    : 0;

  if (job.initial_review_ready || job.review_ready) {
    const weightedPostOpen = 0.7 + (candidatesReadyProgress * 0.26);
    return Math.max(72, Math.min(98, Math.round(weightedPostOpen * 100)));
  }

  const weighted = (
    pagesBuiltProgress * 0.25
    + pagesProcessedProgress * 0.2
    + submissionsCreatedProgress * 0.1
    + initialReviewProgress * 0.45
  );
  return Math.max(8, Math.min(96, Math.round(weighted * 100)));
};

const formatUsd = (value: number) => {
  if (!Number.isFinite(value)) return '$0.00';
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
};

const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));


export function ExamsPage() {
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [classLists, setClassLists] = useState<ClassListRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingExamId, setDeletingExamId] = useState<number | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalFiles, setModalFiles] = useState<File[]>([]);
  const [selectedClassListId, setSelectedClassListId] = useState<string>('');
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<WizardStep>('creating');
  const [wizardError, setWizardError] = useState<WizardError | null>(null);
  const [currentExamId, setCurrentExamId] = useState<number | null>(null);
  const [parsedQuestionCount, setParsedQuestionCount] = useState<number | null>(null);
  const [allowLargeUpload, setAllowLargeUpload] = useState(false);
  const [parseProgress, setParseProgress] = useState(0);
  const [failedSummary, setFailedSummary] = useState<string | null>(null);
  const [parsePageCount, setParsePageCount] = useState(0);
  const [checklistSteps, setChecklistSteps] = useState<ParseChecklistStep[]>(() => initChecklist());
  const [frontPageThinkingLevel, setFrontPageThinkingLevel] = useState<ThinkingLevel>('low');
  const requestControllerRef = useRef<AbortController | null>(null);
  const currentStepRef = useRef<WizardStep>('creating');
  const openWizardButtonRef = useRef<HTMLButtonElement>(null);
  const parseProgressRef = useRef(0);
  const parseProgressTimerRef = useRef<number | null>(null);

  const { showError, showSuccess, showWarning } = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  const totalFileBytes = useMemo(() => modalFiles.reduce((sum, file) => sum + file.size, 0), [modalFiles]);
  const totalTooLarge = totalFileBytes > LARGE_TOTAL_BYTES;
  const sortedExams = useMemo(
    () => [...exams].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [exams],
  );
  const filteredExams = useMemo(
    () => sortedExams.filter((exam) => exam.name.toLowerCase().includes(searchTerm.toLowerCase().trim())),
    [sortedExams, searchTerm],
  );
  const activeIntakeExamIds = useMemo(
    () => exams
      .filter((exam) => {
        const jobStatus = exam.intake_job?.status?.trim().toLowerCase();
        return jobStatus === 'queued' || jobStatus === 'running';
      })
      .map((exam) => exam.id)
      .sort((a, b) => a - b),
    [exams],
  );
  const showCostDebug = useMemo(() => {
    if (import.meta.env.VITE_ENABLE_COST_DEBUG !== '1') return false;
    const params = new URLSearchParams(location.search);
    return params.get('debug') === 'cost';
  }, [location.search]);

  const loadHomeData = async () => {
    try {
      setLoading(true);
      const [examRows, classListRows] = await Promise.all([
        api.getExams(),
        api.getClassLists(),
      ]);
      setExams(examRows);
      setClassLists(classListRows);
    } catch (loadError) {
      showError(loadError instanceof Error ? loadError.message : 'Failed to load Home');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadHomeData();
  }, []);

  useEffect(() => {
    if (activeIntakeExamIds.length === 0) return;

    let cancelled = false;

    const pollActiveJobs = async () => {
      const results = await Promise.allSettled(activeIntakeExamIds.map((examId) => api.getLatestExamIntakeJob(examId)));
      if (cancelled) return;

      let shouldRefreshExams = false;
      const nextJobsByExamId = new Map<number, ExamRead['intake_job']>();
      activeIntakeExamIds.forEach((examId, index) => {
        const result = results[index];
        if (result.status !== 'fulfilled') return;
        nextJobsByExamId.set(examId, result.value);
        const status = result.value?.status?.trim().toLowerCase();
        if (status === 'complete' || status === 'failed') {
          shouldRefreshExams = true;
        }
      });

      setExams((prev) => prev.map((exam) => {
        if (!nextJobsByExamId.has(exam.id)) return exam;
        const nextJob = nextJobsByExamId.get(exam.id) ?? null;
        const nextStatus = nextJob?.status?.trim().toLowerCase();
        const patchedStatus = nextJob?.initial_review_ready
          ? 'REVIEWING'
          : nextStatus === 'failed'
            ? 'FAILED'
            : exam.status;
        return {
          ...exam,
          status: patchedStatus,
          intake_job: nextJob,
        };
      }));

      if (shouldRefreshExams) {
        void loadHomeData();
      }
    };

    void pollActiveJobs();
    const id = window.setInterval(() => {
      void pollActiveJobs();
    }, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [activeIntakeExamIds.join(','), showError]);

  const handleDeleteExam = async (exam: ExamRead) => {
    const confirmed = window.confirm(`Delete "${exam.name}" and all of its submissions, parsing jobs, and results? This cannot be undone.`);
    if (!confirmed) return;

    try {
      setDeletingExamId(exam.id);
      await api.deleteExam(exam.id);
      setExams((prev) => prev.filter((item) => item.id !== exam.id));
      showSuccess(`Deleted "${exam.name}"`);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to delete exam');
    } finally {
      setDeletingExamId((current) => (current === exam.id ? null : current));
    }
  };

  const markChecklist = (id: ParseChecklistStepId, status: ParseChecklistStatus) => {
    setChecklistSteps((prev) => prev.map((item) => (item.id === id ? { ...item, status } : item)));
  };

  const setProgressValue = (value: number) => {
    parseProgressRef.current = value;
    setParseProgress(value);
  };

  const stopProgressAnimation = () => {
    if (parseProgressTimerRef.current !== null) {
      window.clearInterval(parseProgressTimerRef.current);
      parseProgressTimerRef.current = null;
    }
  };

  const startProgressAnimation = (start: number, ceiling: number) => {
    stopProgressAnimation();
    setProgressValue(start);
    parseProgressTimerRef.current = window.setInterval(() => {
      setParseProgress((prev) => {
        const next = Math.min(ceiling, prev + Math.max(1, Math.ceil((ceiling - prev) * 0.14)));
        parseProgressRef.current = next;
        if (next >= ceiling) {
          stopProgressAnimation();
        }
        return next;
      });
    }, 140);
  };

  const animateProgressTo = async (target: number, durationMs = 650) => {
    stopProgressAnimation();
    const start = parseProgressRef.current;
    if (target <= start) {
      setProgressValue(target);
      return;
    }
    const steps = Math.max(5, Math.round(durationMs / 75));
    for (let index = 1; index <= steps; index += 1) {
      await delay(Math.round(durationMs / steps));
      const next = Math.round(start + ((target - start) * index) / steps);
      setProgressValue(next);
    }
  };

  const updateCurrentStep = (nextStep: WizardStep) => {
    currentStepRef.current = nextStep;
    setStep(nextStep);
  };

  const endpointForStep = (stepName: WizardStep, examId: number | null) => {
    if (stepName === 'creating') return buildApiUrl('exams');
    if (stepName === 'uploading' && examId) return buildApiUrl(`exams/${examId}/submissions/bulk`);
    if (stepName === 'building_pages' && examId) return buildApiUrl(`exams/${examId}/submissions/bulk`);
    if (stepName === 'parsing' && examId) return buildApiUrl(`exams/${examId}/submissions/bulk`);
    return buildApiUrl('unknown');
  };

  const resetWizardProgress = () => {
    stopProgressAnimation();
    setParsedQuestionCount(null);
    setWizardError(null);
    setCurrentExamId(null);
    setStep('creating');
    setProgressValue(0);
    setFailedSummary(null);
    setParsePageCount(0);
    setChecklistSteps(initChecklist());
  };

  const resetWizardState = () => {
    setModalFiles([]);
    setSelectedClassListId('');
    setAllowLargeUpload(false);
    setFrontPageThinkingLevel('low');
    resetWizardProgress();
  };
  const dismissModal = () => {
    setIsModalOpen(false);
    resetWizardState();
    openWizardButtonRef.current?.focus();
  };

  const closeModal = () => {
    if (isRunning) {
      requestControllerRef.current?.abort();
    }
    stopProgressAnimation();
    dismissModal();
  };

  const ingestWizardTestBundle = async (
    examId: number,
    files: File[],
    requestOptions: RequestInit,
    logStep: () => void,
  ) => {
    updateCurrentStep('uploading');
    markChecklist('uploading_key', 'active');
    const preview = await api.uploadBulkSubmissionsFile(examId, files, undefined, requestOptions);
    logStep();
    markChecklist('uploading_key', 'done');
    setProgressValue(38);

    updateCurrentStep('building_pages');
    markChecklist('building_key_pages', 'active');
    setParsePageCount(preview.page_count);
    setParsedQuestionCount(preview.candidates.length);
    markChecklist('building_key_pages', 'done');
    setProgressValue(56);

    updateCurrentStep('parsing');
    markChecklist('reading_questions', 'active');
    setProgressValue(72);

    const finalized = await api.finalizeBulkSubmissions(
      examId,
      preview.bulk_upload_id,
      preview.candidates.map((candidate) => ({
        student_name: candidate.student_name,
        page_start: Number(candidate.page_start),
        page_end: Number(candidate.page_end),
      })),
      requestOptions,
    );
    logStep();
    markChecklist('reading_questions', 'done');
    markChecklist('detecting_marks', 'done');
    markChecklist('drafting_rubric', 'done');
    markChecklist('finalizing', 'done');
    setProgressValue(100);
    updateCurrentStep('done');

    return {
      pageCount: preview.page_count,
      candidateCount: preview.candidates.length,
      submissionCount: finalized.submissions.length,
    };
  };

  const runCreateAndUpload = async () => {
    if (modalFiles.length === 0) {
      showError('Add at least one paper file.');
      return;
    }

    if (totalTooLarge && !allowLargeUpload) {
      showWarning('Total files exceed 12 MB. Confirm to continue with upload.');
      return;
    }

    resetWizardProgress();

    let examId: number | null = null;
    const controller = new AbortController();
    requestControllerRef.current = controller;
    const requestOptions = { signal: controller.signal };

    const logStep = () => {};

    try {
      setIsRunning(true);
      updateCurrentStep('creating');
      markChecklist('creating_exam', 'active');
      startProgressAnimation(14, 78);
      const selectedId = selectedClassListId ? Number(selectedClassListId) : null;
      const exam = await api.createExamWithIntake(
        modalFiles,
        frontPageThinkingLevel,
        '',
        Number.isFinite(selectedId) ? selectedId : null,
        requestOptions,
      );
      const activeExamId = exam.id;
      examId = activeExamId;
      setCurrentExamId(activeExamId);
      setExams((prev) => [exam, ...prev.filter((item) => item.id !== exam.id)]);
      logStep();
      markChecklist('creating_exam', 'done');
      markChecklist('uploading_key', 'done');
      setExams((prev) => prev.map((item) => (item.id === activeExamId ? exam : item)));
      await animateProgressTo(100, 720);
      updateCurrentStep('done');
      await delay(260);
      showSuccess('Workspace created. Papers are preparing in Home.');
      requestControllerRef.current = null;
      dismissModal();
      void loadHomeData();
      return;
    } catch (err) {
      const stepName = currentStepRef.current;
      const stepEndpoint = endpointForStep(stepName, examId);

      if (isNetworkFetchError(err) || isAbortError(err)) {
        const details = err instanceof Error ? err.stack || err.message : String(err);
        setWizardError({
          step: stepName,
          summary: `Step: ${stepName} | Status: network-error`,
          details,
          attemptedUrl: stepEndpoint,
          method: 'UNKNOWN',
          status: 'network-error',
          bodySnippet: err instanceof Error ? err.message : String(err),
          isAbort: isAbortError(err),
        });
        showError(`Network request failed. Step: ${stepName}. URL: ${stepEndpoint}`);
      } else if (err instanceof ApiError) {
        const details = JSON.stringify({
          method: err.method,
          url: err.url,
          status: err.status,
          bodySnippet: err.responseBodySnippet || '<empty>',
        }, null, 2);
        try {
          const parseDetails = JSON.parse(err.responseBodySnippet || '{}') as ParseErrorDetails;
          if (stepName === 'parsing') {
            markChecklist(stageToChecklistId(parseDetails.stage), 'failed');
            if (parseDetails.page_index && parseDetails.page_count) {
              setFailedSummary(`Failed at: ${parseDetails.stage || 'unknown'} (page ${parseDetails.page_index}/${parseDetails.page_count})`);
            }
          }
        } catch {
          // no-op
        }
        setWizardError({
          step: stepName,
          summary: `Step: ${stepName} | ${err.method} ${err.url} | Status: ${err.status}`,
          details,
          attemptedUrl: err.url,
          method: err.method,
          status: err.status,
          contentType: undefined,
          bodySnippet: err.responseBodySnippet,
        });
        showError(`${stepName} failed (status ${err.status}) via ${err.method}`);
      } else {
        setWizardError({
          step: stepName,
          summary: `Step: ${stepName} | Status: unknown`,
          details: err instanceof Error ? err.message : 'Unknown error',
          attemptedUrl: stepEndpoint,
          method: 'UNKNOWN',
          status: 'unknown',
          bodySnippet: err instanceof Error ? err.message : String(err),
        });
        showError(`${stepName} failed (status unknown)`);
      }
    } finally {
      stopProgressAnimation();
      setIsRunning(false);
      requestControllerRef.current = null;
    }
  };

  const onCreateAndUpload = async (event: FormEvent) => {
    event.preventDefault();
    await runCreateAndUpload();
  };

  const onRetryFailedStep = async () => {
    if (!wizardError) return;
    await runCreateAndUpload();
  };

  return (
    <div className="page-stack">
      <section className="card card--hero stack">
        <div className="page-header">
          <div>
            <h1 className="page-title">Home</h1>
            <p className="page-subtitle">Upload papers, confirm totals, and export the class table.</p>
          </div>
        </div>

        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Start a new exam</h2>
              <p className="subtle-text">Upload graded papers to create a workspace.</p>
            </div>
          </div>
          <div className="actions-row" style={{ marginTop: 0 }}>
            <button type="button" className="btn btn-primary" onClick={() => setIsModalOpen(true)}>
              Create exam
            </button>
          </div>
        </section>
      </section>

      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">Test library</h2>
            <p className="subtle-text">Search saved workspaces.</p>
          </div>
          <span className="status-pill status-neutral">{filteredExams.length} match{filteredExams.length === 1 ? '' : 'es'}</span>
        </div>

        <label htmlFor="exam-search">Search tests</label>
        <input
          id="exam-search"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder="Search by test name"
        />

        {loading && (
          <div className="workspace-card-grid" aria-label="Loading exams">
            {[1, 2, 3].map((item) => (
              <div key={item} className="workspace-card skeleton" style={{ minHeight: 132 }} />
            ))}
          </div>
        )}

        {!loading && filteredExams.length === 0 && (
          <div className="review-readonly-block">
            <strong>No matching exams</strong>
            <p className="subtle-text" style={{ marginTop: '.35rem' }}>Create a workspace to get started.</p>
          </div>
        )}

        {!loading && filteredExams.length > 0 && (
          <div className="workspace-card-grid">
            {filteredExams.map((exam) => {
              const status = normalizeExamStatus(exam);
              const isDeleting = deletingExamId === exam.id;
              const intakeJob = exam.intake_job;
              const isOrphanDraft = !intakeJob && exam.status?.trim().toLowerCase() === 'draft';
              const isPreparing = status.tone === 'status-in-progress';
              const isFailed = status.label === 'Failed';
              const initialReviewReady = Boolean(intakeJob?.initial_review_ready || intakeJob?.review_ready);
              const fullyWarmed = Boolean(intakeJob?.fully_warmed);
              const canOpenWorkspace = initialReviewReady || (!isPreparing && !isFailed);
              const preparingStageLabel = formatIntakeStage(exam.intake_job);
              const preparingSummary = formatPreparingSummary(exam.intake_job);
              const preparingPercent = intakeProgressPercent(exam.intake_job);
              const frontPageMetrics = intakeJob?.metrics ?? null;
              const debugCostPerPage = Number(frontPageMetrics?.front_page_avg_cost_per_page_usd ?? 0);
              const debugAverageImageBytes = Number(frontPageMetrics?.front_page_avg_image_bytes ?? 0);
              const debugProvider = String(frontPageMetrics?.front_page_provider ?? '').trim();
              const debugModel = String(frontPageMetrics?.front_page_model ?? '').trim();
              const debugThinkingLevel = String(intakeJob?.thinking_level || frontPageMetrics?.front_page_thinking_level || 'low').trim().toUpperCase();
              return (
                <article key={exam.id} className="workspace-card">
                  <div className="workspace-card-header">
                    <div>
                      <Link className="workspace-card-title" to={`/exams/${exam.id}`}>{exam.name}</Link>
                    </div>
                    <span className={`status-pill ${status.tone}${status.label === 'Working' ? ' status-pill-working' : ''} ${libraryStatusClassName(status.label)}`.trim()}>{status.label}</span>
                  </div>
                  <div className="workspace-card-meta">
                    <span>Created {formatExamDate(exam.created_at)}</span>
                  </div>
                  {(isPreparing || (canOpenWorkspace && intakeJob && !fullyWarmed)) && (
                    <div className="library-card-progress" aria-label="Preparing test">
                      <div className="library-card-progress-meta">
                        <strong>{preparingStageLabel}</strong>
                        <span>{preparingPercent}%</span>
                      </div>
                      <div className="library-card-progress-bar" aria-hidden="true">
                        <span style={{ width: `${preparingPercent}%` }} />
                      </div>
                    </div>
                  )}
                  {exam.intake_job && (isPreparing || (canOpenWorkspace && !fullyWarmed)) && (
                    <p className="subtle-text" style={{ margin: 0 }}>
                      {preparingSummary}
                    </p>
                  )}
                  {canOpenWorkspace && intakeJob && !fullyWarmed && (
                    <p className="subtle-text" style={{ margin: 0 }}>
                      You can open the workspace now. Remaining papers will keep preparing in the background.
                    </p>
                  )}
                  {isOrphanDraft && (
                    <p className="warning-text" style={{ margin: 0 }}>
                      This workspace was created, but the upload did not start. Delete it and upload again.
                    </p>
                  )}
                  {intakeJob?.status === 'failed' && exam.intake_job?.error_message && (
                    <p className="warning-text" style={{ margin: 0 }}>{exam.intake_job.error_message}</p>
                  )}
                  {showCostDebug && frontPageMetrics && Number(frontPageMetrics.front_page_calls ?? 0) > 0 && (
                    <div className="review-readonly-block surface-muted" style={{ margin: 0 }}>
                      <strong>Front-page cost</strong>
                      <p className="subtle-text" style={{ margin: '.35rem 0 0' }}>
                        {debugProvider || 'provider'} · {debugModel || 'model'} · {debugThinkingLevel}
                      </p>
                      <p className="subtle-text" style={{ margin: '.2rem 0 0' }}>
                        {formatUsd(debugCostPerPage)}/page · {(frontPageMetrics.front_page_prompt_tokens as number) || 0} prompt · {(frontPageMetrics.front_page_output_tokens as number) || 0} output · {(frontPageMetrics.front_page_thought_tokens as number) || 0} thought
                      </p>
                      <p className="subtle-text" style={{ margin: '.2rem 0 0' }}>
                        {(frontPageMetrics.front_page_calls as number) || 0} call{Number(frontPageMetrics.front_page_calls || 0) === 1 ? '' : 's'} · {debugAverageImageBytes > 0 ? `${Math.round(debugAverageImageBytes).toLocaleString()} avg bytes` : 'image bytes unavailable'}
                      </p>
                    </div>
                  )}
                  <div className="actions-row workspace-card-actions" style={{ marginTop: 0 }}>
                    {canOpenWorkspace ? (
                      <Link className="btn btn-secondary btn-sm" to={`/exams/${exam.id}`}>Open workspace</Link>
                    ) : (
                      <button type="button" className="btn btn-secondary btn-sm" disabled>
                        {isPreparing ? 'Preparing…' : 'Unavailable'}
                      </button>
                    )}
                    {(isFailed || (canOpenWorkspace && intakeJob && !fullyWarmed && intakeJob.status === 'failed')) && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={async () => {
                          try {
                            const job = await api.retryExamIntakeJob(exam.id);
                            setExams((prev) => prev.map((item) => (item.id === exam.id ? { ...item, intake_job: job, status: 'DRAFT' } : item)));
                            showSuccess(`Retry started for "${exam.name}"`);
                          } catch (error) {
                            showError(error instanceof Error ? error.message : 'Failed to retry intake');
                          }
                        }}
                        disabled={isDeleting}
                      >
                        Retry
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => void handleDeleteExam(exam)}
                      disabled={isDeleting}
                    >
                      {isDeleting ? 'Deleting…' : 'Delete'}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      {isModalOpen && (
        <Modal title="Create exam" onClose={closeModal}>
          <p className="subtle-text">Upload papers to start a workspace.</p>
          <form onSubmit={onCreateAndUpload} className="stack">
            <div className="stack" style={{ gap: '.6rem' }}>
              <label>Paper files</label>
              <FileUploader
                files={modalFiles}
                disabled={isRunning}
                onChange={setModalFiles}
                maxBytesPerFile={LARGE_FILE_BYTES}
                onReject={(message) => showWarning(message)}
                multiple
                singularLabel="paper photo"
              />
              {modalFiles.length > 0 && (
                <p className="subtle-text" style={{ margin: 0 }}>
                  {modalFiles.length} file{modalFiles.length === 1 ? '' : 's'} selected · {formatMb(totalFileBytes)}
                </p>
              )}
              {totalTooLarge && (
                <label className="review-readonly-block surface-muted" style={{ display: 'block' }}>
                  <input
                    type="checkbox"
                    checked={allowLargeUpload}
                    onChange={(event) => setAllowLargeUpload(event.target.checked)}
                    disabled={isRunning}
                    style={{ marginRight: '.55rem' }}
                  />
                  Total upload exceeds 12 MB. Confirm to continue anyway.
                </label>
              )}
            </div>
            <div className="stack" style={{ gap: '.6rem' }}>
              <label htmlFor="class-list-select">Class list</label>
              <select
                id="class-list-select"
                value={selectedClassListId}
                onChange={(event) => setSelectedClassListId(event.target.value)}
                disabled={isRunning}
              >
                <option value="">Skip</option>
                {classLists.map((classList) => (
                  <option key={classList.id ?? `${classList.name}-${classList.created_at}`} value={classList.id ?? ''}>
                    {(classList.name || 'Untitled class list')} · {classList.entry_count} name{classList.entry_count === 1 ? '' : 's'}
                  </option>
                ))}
              </select>
              <p className="subtle-text" style={{ margin: 0 }}>
                {classLists.length > 0 ? 'Optional. Use a saved class list to improve name reads.' : 'No saved class lists yet.'}
                {' '}
                <Link to="/class-lists">Manage class lists</Link>
              </p>
            </div>
            <div className="stack" style={{ gap: '.45rem' }}>
              <label>Thinking</label>
              <div className="actions-row" role="radiogroup" aria-label="Front-page thinking level" style={{ marginTop: 0 }}>
                {THINKING_LEVEL_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={frontPageThinkingLevel === option.value}
                    className={frontPageThinkingLevel === option.value ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
                    onClick={() => setFrontPageThinkingLevel(option.value)}
                    disabled={isRunning}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            {(isRunning || wizardError) && (
              <div className="stack" style={{ gap: '.65rem' }}>
                <div className="metric-card">
                  <div className="panel-title-row" style={{ marginBottom: '.45rem' }}>
                    <strong>Creating workspace</strong>
                    <span className={`status-pill ${wizardError ? 'status-blocked' : 'status-in-progress'}`}>
                      {wizardError ? 'Needs attention' : 'Working'}
                    </span>
                  </div>
                  {isRunning && <progress max={100} value={Math.max(parseProgress, 12)} style={{ width: '100%' }} />}
                  <p className="metric-meta" style={{ marginTop: '.5rem' }}>
                    {wizardError
                      ? wizardError.summary
                      : parseProgress >= 100
                        ? 'Workspace created. Moving this to Home.'
                        : 'Creating the workspace and handing it off to Home.'}
                  </p>
                </div>
                {failedSummary && <p className="warning-text" style={{ margin: 0 }}>{failedSummary}</p>}
                {wizardError && (
                  <div className="actions-row" style={{ marginTop: 0 }}>
                    <button type="button" className="btn btn-secondary" onClick={() => void onRetryFailedStep()} disabled={isRunning}>
                      Retry failed step
                    </button>
                  </div>
                )}
              </div>
            )}

            <div className="actions-row">
              <button type="submit" className="btn btn-primary" disabled={isRunning}>
                {isRunning ? 'Preparing…' : 'Create exam'}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  closeModal();
                }}
              >
                Close
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
