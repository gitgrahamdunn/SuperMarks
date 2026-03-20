import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { API_BASE_URL, ApiError, api, buildApiUrl, getBackendVersion, maskApiBaseUrl, pingApiHealth } from '../api/client';
import { DebugPanel } from '../components/DebugPanel';
import { FileUploader } from '../components/FileUploader';
import { uploadToBlob } from '../blob/upload';
import { Modal } from '../components/Modal';
import { useToast } from '../components/ToastProvider';
import type { ExamRead, ParseNextResponse, QuestionRead } from '../types/api';

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
type WizardParseStatus = 'idle' | 'running' | 'done' | 'failed';

type StepLog = {
  step: WizardStep;
  endpointUrl: string;
  status: number | 'network-error';
  responseSnippet: string;
  exceptionMessage?: string;
};

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

type ParseTimings = Record<string, number>;
type ParseOutcomeData = { timings?: ParseTimings; page_count?: number; page_index?: number };
type RunningTotals = { cost_total: number; input_tokens_total: number; output_tokens_total: number; model_usage?: Record<string, number> };

type RawFetchProbeResult = {
  attemptedUrl: string;
  responseStatus?: number;
  contentType?: string | null;
  bodySnippet?: string;
  finalUrl?: string;
  errorName?: string;
  errorMessage?: string;
};

type ParseChecklistStep = {
  id: ParseChecklistStepId;
  label: string;
  status: ParseChecklistStatus;
};

interface WizardParseResult {
  questions?: unknown;
  result?: { questions?: unknown };
}

const MB = 1024 * 1024;
const LARGE_FILE_BYTES = 8 * MB;
const LARGE_TOTAL_BYTES = 12 * MB;
const SERVER_UPLOAD_MAX_BYTES = 25 * MB;
const RAW_FETCH_SNIPPET_LENGTH = 300;

const CHECKLIST_ORDER: Array<{ id: ParseChecklistStepId; label: string }> = [
  { id: 'creating_exam', label: 'Creating exam' },
  { id: 'uploading_key', label: 'Uploading key' },
  { id: 'building_key_pages', label: 'Building key pages' },
  { id: 'reading_questions', label: 'Reading questions' },
  { id: 'detecting_marks', label: 'Detecting marks' },
  { id: 'drafting_rubric', label: 'Drafting rubric' },
  { id: 'finalizing', label: 'Finalizing' },
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

function extractParsedQuestionCount(parseResult: unknown): number {
  if (Array.isArray(parseResult)) return parseResult.length;
  if (typeof parseResult !== 'object' || !parseResult) return 0;

  const shaped = parseResult as WizardParseResult & { question_count?: unknown };
  if (Array.isArray(shaped.questions)) return shaped.questions.length;
  if (Array.isArray(shaped.result?.questions)) return shaped.result.questions.length;
  if (typeof shaped.question_count === 'number') return shaped.question_count;
  return 0;
}


const toParsePageUiStatus = (status: 'pending' | 'running' | 'done' | 'failed' | undefined): ParseChecklistStatus => {
  if (status === 'running') return 'active';
  if (status === 'done') return 'done';
  if (status === 'failed') return 'failed';
  return 'pending';
};

const isNetworkFetchError = (error: unknown) =>
  error instanceof Error && (error instanceof TypeError || /load failed|failed to fetch|network/i.test(error.message));

const isAbortError = (error: unknown) => {
  if (error instanceof DOMException && error.name === 'AbortError') return true;
  return error instanceof Error && (error.name === 'AbortError' || /aborted/i.test(error.message));
};

const PARSE_BATCH_SIZE = 3;

const hasMeaningfulTotals = (totals: RunningTotals | null | undefined): totals is RunningTotals => Boolean(
  totals
  && (totals.cost_total > 0 || totals.input_tokens_total > 0 || totals.output_tokens_total > 0),
);

const pickPreferredTotals = (
  previous: RunningTotals | null,
  incoming: RunningTotals | null | undefined,
): RunningTotals | null => {
  if (!incoming) return previous;
  if (!previous) return incoming;
  if (hasMeaningfulTotals(incoming)) return incoming;
  if (hasMeaningfulTotals(previous) && !hasMeaningfulTotals(incoming)) return previous;
  return incoming;
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

const normalizeExamStatus = (status?: string | null) => {
  const normalized = status?.trim().toLowerCase();
  if (!normalized) {
    return { label: 'Active workspace', tone: 'status-ready' };
  }
  if (normalized.includes('complete') || normalized.includes('done')) {
    return { label: 'Complete', tone: 'status-complete' };
  }
  if (normalized.includes('progress') || normalized.includes('review')) {
    return { label: 'In progress', tone: 'status-in-progress' };
  }
  if (normalized.includes('block') || normalized.includes('flag')) {
    return { label: 'Needs review', tone: 'status-blocked' };
  }
  return { label: status || 'Active workspace', tone: 'status-neutral' };
};


export function ExamsPage() {
  const showDevTools = import.meta.env.DEV;
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingExamId, setDeletingExamId] = useState<number | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalName, setModalName] = useState('');
  const [modalFiles, setModalFiles] = useState<File[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<WizardStep>('creating');
  const [wizardError, setWizardError] = useState<WizardError | null>(null);
  const [currentExamId, setCurrentExamId] = useState<number | null>(null);
  const [parsedQuestionCount, setParsedQuestionCount] = useState<number | null>(null);
  const [stepLogs, setStepLogs] = useState<StepLog[]>([]);
  const [pingResult, setPingResult] = useState<string>('');
  const [rawFetchProbeResult, setRawFetchProbeResult] = useState<RawFetchProbeResult | null>(null);
  const [isRunningRawProbe, setIsRunningRawProbe] = useState(false);
  const [isPingingApi, setIsPingingApi] = useState(false);
  const [allowLargeUpload, setAllowLargeUpload] = useState(false);
  const [parseProgress, setParseProgress] = useState(0);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [failedSummary, setFailedSummary] = useState<string | null>(null);
  const [parsePageCount, setParsePageCount] = useState(0);
  const [parsePageIndex, setParsePageIndex] = useState(0);
  const [currentParsingPageNumber, setCurrentParsingPageNumber] = useState(0);
  const [parseJobStatus, setParseJobStatus] = useState<WizardParseStatus>('idle');
  const [pageStatuses, setPageStatuses] = useState<Record<number, ParseChecklistStatus>>({});
  const [liveParsedQuestions, setLiveParsedQuestions] = useState<QuestionRead[]>([]);
  const [emptyPageNotes, setEmptyPageNotes] = useState<number[]>([]);
  const [activityMessageIndex, setActivityMessageIndex] = useState(0);
  const [parseTimings, setParseTimings] = useState<ParseTimings | null>(null);
  const [runningTotals, setRunningTotals] = useState<RunningTotals | null>(null);
  const [failedPage, setFailedPage] = useState<number | null>(null);
  const [parseJobId, setParseJobId] = useState<number | null>(null);
  const [latestBackendParseJobId, setLatestBackendParseJobId] = useState<number | null>(null);
  const [parseStartReusedJob, setParseStartReusedJob] = useState<boolean | null>(null);
  const [lastProcessedPages, setLastProcessedPages] = useState<number[]>([]);
  const [lastFailedPages, setLastFailedPages] = useState<number[]>([]);
  const [checklistSteps, setChecklistSteps] = useState<ParseChecklistStep[]>(() => initChecklist());
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [backendVersion, setBackendVersion] = useState<string>('loading...');
  const [examUnavailable, setExamUnavailable] = useState(false);

  const parseProgressIntervalRef = useRef<number | null>(null);
  const elapsedIntervalRef = useRef<number | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const currentStepRef = useRef<WizardStep>('creating');
  const openWizardButtonRef = useRef<HTMLButtonElement>(null);
  const examNameRef = useRef<HTMLInputElement>(null);
  const liveQuestionsBottomRef = useRef<HTMLDivElement | null>(null);

  const { showError, showSuccess, showWarning } = useToast();
  const navigate = useNavigate();

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
  const recentExams = useMemo(() => sortedExams.slice(0, 3), [sortedExams]);
  const estimatedParsingPage = parsePageCount > 0 ? Math.min(parsePageCount, Math.max(1, Math.floor(elapsedSeconds / 3) + 1)) : 0;
  const parseActivityMessages = [
    `Parsing up to 3 pages at a time (near page ${Math.max(1, currentParsingPageNumber)})…`,
    'Extracting questions…',
    'Drafting rubric…',
  ];
  const currentParsePageForDisplay = currentParsingPageNumber > 0
    ? currentParsingPageNumber
    : Math.max(1, parsePageIndex || estimatedParsingPage || 1);
  const pagesDone = Math.max(0, parsePageIndex);
  const failedPageCount = Object.values(pageStatuses).filter((status) => status === 'failed').length;
  const processedStart = lastProcessedPages.length > 0 ? Math.min(...lastProcessedPages) : null;
  const processedEnd = lastProcessedPages.length > 0 ? Math.max(...lastProcessedPages) : null;
  const keyPageImageUrl = currentExamId
    ? `${api.getExamKeyPageUrl(currentExamId, currentParsePageForDisplay)}?v=${currentExamId}-${currentParsePageForDisplay}-${pagesDone}`
    : '';
  const parseStartUrl = currentExamId ? buildApiUrl(`exams/${currentExamId}/key/parse/start`) : 'n/a';
  const parseHasFailedPages = lastFailedPages.length > 0 || failedPage !== null;
  const parseIsDone = step === 'done' || parseJobStatus === 'done';
  const canReviewCriteria = Boolean(currentExamId && parseIsDone && (parsedQuestionCount || liveParsedQuestions.length) > 0);
  const activeExamCount = exams.filter((exam) => !normalizeExamStatus(exam.status).label.toLowerCase().includes('complete')).length;

  const applyParseBatchResult = (next: ParseNextResponse) => {
    setParsePageIndex(next.pages_done);
    setParsePageCount(next.page_count);
    const processed = Array.isArray(next.pages_processed) ? next.pages_processed : [];
    setLastProcessedPages(processed);
    if (processed.length > 0) {
      setCurrentParsingPageNumber(Math.max(...processed));
    }
    const failedInBatch: number[] = [];
    (next.page_results || []).forEach((result) => {
      if (result.page_number > 0) {
        const status = toParsePageUiStatus(result.status || 'done');
        markPageStatus(result.page_number, status);
        if (status === 'failed') failedInBatch.push(result.page_number);
      }
    });
    setLastFailedPages(failedInBatch);
    if (failedInBatch.length > 0) {
      setFailedPage(failedInBatch[0]);
    }
    setParseJobStatus(next.status === 'running' ? 'running' : next.status);
    setRunningTotals((prev) => pickPreferredTotals(prev, next.totals));
    const pct = Math.min(98, 50 + Math.round((next.pages_done / Math.max(1, next.page_count)) * 45));
    setParseProgress(pct);
    return processed;
  };

  const getQuestionPageNumber = (question: QuestionRead) => {
    const pageFromRubric = Number(question.rubric_json?.key_page_number || 0);
    if (Number.isFinite(pageFromRubric) && pageFromRubric > 0) return pageFromRubric;
    const firstRegion = Array.isArray(question.regions) ? question.regions[0] : null;
    const pageFromRegion = Number(firstRegion?.page_number || 0);
    if (Number.isFinite(pageFromRegion) && pageFromRegion > 0) return pageFromRegion;
    return 1;
  };

  const syncLiveQuestions = async (examId: number) => {
    const fetched = await api.getExamQuestionsForReview(examId);
    const mapped = fetched.map((item) => ({ ...item, rubric_json: item.rubric_json || {} }));
    const previousIds = new Set(liveParsedQuestions.map((item) => item.id));
    const fresh = mapped.filter((item) => !previousIds.has(item.id));
    setLiveParsedQuestions(mapped.sort((a, b) => a.id - b.id));
    return { total: mapped.length, newCount: fresh.length };
  };

  const markPageStatus = (pageNumber: number, status: ParseChecklistStatus) => {
    setPageStatuses((prev) => ({ ...prev, [pageNumber]: status }));
  };

  const initializePageStatuses = (count: number) => {
    if (!count) {
      setPageStatuses({});
      return;
    }
    const initial: Record<number, ParseChecklistStatus> = {};
    for (let page = 1; page <= count; page += 1) {
      initial[page] = 'pending';
    }
    setPageStatuses(initial);
  };

  const loadExams = async () => {
    try {
      setLoading(true);
      setExams(await api.getExams());
    } catch (loadError) {
      showError(loadError instanceof Error ? loadError.message : 'Failed to load exams');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadExams();
  }, []);

  const onCreateExamWorkspace = async (event: FormEvent) => {
    event.preventDefault();
    if (!modalName.trim()) {
      showWarning('Enter an exam name');
      return;
    }

    try {
      setIsRunning(true);
      const exam = await api.createExam(modalName.trim());
      showSuccess('Exam workspace created.');
      closeModal();
      navigate(`/exams/${exam.id}`);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to create exam workspace');
    } finally {
      setIsRunning(false);
    }
  };

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

  useEffect(() => {
    const loadBackendVersion = async () => {
      try {
        const result = await getBackendVersion();
        if (result.version) {
          setBackendVersion(result.version);
          return;
        }
        setBackendVersion(`unavailable (status ${result.status}) ${result.bodySnippet || ''}`.trim());
      } catch (error) {
        setBackendVersion(`unavailable (${error instanceof Error ? error.message : String(error)})`);
      }
    };

    void loadBackendVersion();
  }, []);

  useEffect(() => {
    if (step !== 'parsing' || !isRunning) return;
    const interval = window.setInterval(() => setActivityMessageIndex((prev) => (prev + 1) % parseActivityMessages.length), 1200);
    return () => window.clearInterval(interval);
  }, [isRunning, step, parseActivityMessages.length]);

  useEffect(() => {
    liveQuestionsBottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [liveParsedQuestions.length]);

  useEffect(() => {
    if (!currentExamId || step !== 'parsing' || parsePageCount <= 0) return;
    const nextPage = currentParsePageForDisplay + 1;
    if (nextPage > parsePageCount) return;
    const preloadImage = new Image();
    preloadImage.src = `${api.getExamKeyPageUrl(currentExamId, nextPage)}?v=${currentExamId}-${nextPage}-${parsePageIndex}`;
  }, [currentExamId, currentParsePageForDisplay, parsePageCount, parsePageIndex, step]);

  const clearIntervals = () => {
    if (parseProgressIntervalRef.current !== null) {
      window.clearInterval(parseProgressIntervalRef.current);
      parseProgressIntervalRef.current = null;
    }
    if (elapsedIntervalRef.current !== null) {
      window.clearInterval(elapsedIntervalRef.current);
      elapsedIntervalRef.current = null;
    }
  };

  useEffect(() => () => clearIntervals(), []);

  const markChecklist = (id: ParseChecklistStepId, status: ParseChecklistStatus) => {
    setChecklistSteps((prev) => prev.map((item) => (item.id === id ? { ...item, status } : item)));
  };

  const updateCurrentStep = (nextStep: WizardStep) => {
    currentStepRef.current = nextStep;
    setStep(nextStep);
  };

  const endpointForStep = (stepName: WizardStep, examId: number | null) => {
    if (stepName === 'creating') return buildApiUrl('exams');
    if (stepName === 'uploading' && examId) return buildApiUrl(`exams/${examId}/key/register`);
    if (stepName === 'building_pages' && examId) return buildApiUrl(`exams/${examId}/key/build-pages`);
    if (stepName === 'parsing' && examId) return buildApiUrl(`exams/${examId}/key/parse/start`);
    return buildApiUrl('unknown');
  };

  const assertWizardExamId = () => {
    if (currentExamId) return currentExamId;
    showError('No active exam id for this wizard session');
    throw new Error('No active exam id for this wizard session');
  };

  const requireValidParseContext = () => {
    const examId = currentExamId;
    const jobId = parseJobId;
    if (!examId || !jobId) {
      showError('Missing parse context for this wizard session.');
      return null;
    }
    return { examId, jobId };
  };

  const isExamUnavailableError = (error: unknown) => {
    if (!(error instanceof ApiError) || error.status !== 404) return false;
    if (!/\/key\/parse\/(start|status)/.test(error.url) && !/\/questions/.test(error.url)) return false;
    return /Exam not found/i.test(error.responseBodySnippet || '');
  };

  const handleExamUnavailable = () => {
    showError('This exam record is unavailable.');
    setExamUnavailable(true);
    setWizardError({
      step: currentStepRef.current,
      summary: 'This exam record is unavailable.',
      details: 'The selected exam or parse context could not be found.',
      attemptedUrl: endpointForStep(currentStepRef.current, currentExamId),
      method: 'UNKNOWN',
      status: 404,
      bodySnippet: 'Exam not found',
    });
    setIsRunning(false);
    clearIntervals();
  };

  const startParseProgress = () => {
    clearIntervals();
    elapsedIntervalRef.current = window.setInterval(() => setElapsedSeconds((prev) => prev + 1), 1000);
    parseProgressIntervalRef.current = window.setInterval(() => setParseProgress((prev) => (prev < 95 ? prev + 1 : prev)), 700);
  };

  const resetWizardProgress = () => {
    setParsedQuestionCount(null);
    setWizardError(null);
    setCurrentExamId(null);
    setStepLogs([]);
    setStep('creating');
    setParseProgress(0);
    setElapsedSeconds(0);
    setFailedSummary(null);
    setParsePageCount(0);
    setParsePageIndex(0);
    setCurrentParsingPageNumber(0);
    setParseJobStatus('idle');
    setPageStatuses({});
    setLiveParsedQuestions([]);
    setEmptyPageNotes([]);
    setActivityMessageIndex(0);
    setParseTimings(null);
    setRunningTotals(null);
    setFailedPage(null);
    setParseJobId(null);
    setLatestBackendParseJobId(null);
    setParseStartReusedJob(null);
    setLastProcessedPages([]);
    setLastFailedPages([]);
    setChecklistSteps(initChecklist());
    setExamUnavailable(false);
  };

  const resetWizardState = () => {
    setModalName('');
    setModalFiles([]);
    setAllowLargeUpload(false);
    resetWizardProgress();
  };



  const resumeParseJob = async () => {
    let examId: number;
    try {
      examId = assertWizardExamId();
    } catch {
      return;
    }

    try {
      setIsRunning(true);
      setExamUnavailable(false);
      updateCurrentStep('parsing');
      startParseProgress();
      const latest = await api.getExamKeyParseLatest(examId);
      if (!latest.exam_exists) {
        showError('This exam record is unavailable.');
        return;
      }
      if (!latest.job) {
        showWarning('No resumable parse job found');
        return;
      }
      const activeJobId = latest.job.job_id;
      setLatestBackendParseJobId(activeJobId);
      setParseJobId(activeJobId);

      let status = await api.getExamKeyParseStatus(examId, activeJobId);
      setParsePageCount(status.page_count);
      setParsePageIndex(status.pages_done);
      setParseJobStatus(status.status === 'running' ? 'running' : status.status);
      initializePageStatuses(status.page_count);
      status.pages.forEach((page) => markPageStatus(page.page_number, toParsePageUiStatus(page.status)));
      while (status.status === 'running') {
        const nextPageNumber = Math.min(status.page_count, status.pages_done + 1);
        setCurrentParsingPageNumber(nextPageNumber);
        markPageStatus(nextPageNumber, 'active');
        const next = await api.parseExamKeyNext(examId, activeJobId, PARSE_BATCH_SIZE);
        const processed = applyParseBatchResult(next);
        await syncLiveQuestions(examId);
        const failedBatchPages = (next.page_results || []).filter((result) => result.status === 'failed').map((result) => result.page_number);
        if (failedBatchPages.length > 0) {
          setFailedPage(failedBatchPages[0]);
          break;
        }
        if (processed.length === 0) {
          break;
        }
        status = await api.getExamKeyParseStatus(examId, activeJobId);
        status.pages.forEach((page) => markPageStatus(page.page_number, toParsePageUiStatus(page.status)));
      }
      if (status.status === 'done') {
        const latestQuestions = await syncLiveQuestions(examId);
        setParsedQuestionCount(latestQuestions.total);
        setParseProgress(100);
        updateCurrentStep('done');
      }
    } catch (error) {
      if (isExamUnavailableError(error)) {
        handleExamUnavailable();
        return;
      }
      showError(error instanceof Error ? error.message : 'Failed to resume parse job');
    } finally {
      setIsRunning(false);
      clearIntervals();
    }
  };

  const retryFailedParsePage = async () => {
    const parseContext = requireValidParseContext();
    if (!parseContext || !failedPage) return;
    const { examId, jobId } = parseContext;
    try {
      await api.retryExamKeyParsePage(examId, jobId, failedPage);
      const next = await api.parseExamKeyNext(examId, jobId, PARSE_BATCH_SIZE);
      applyParseBatchResult(next);
      await syncLiveQuestions(examId);
      if ((next.page_results || []).some((result) => result.status === 'failed')) {
        showWarning(`Page ${failedPage} failed again. Retry when ready.`);
        return;
      }
      setFailedPage(null);
      const status = await api.getExamKeyParseStatus(examId, jobId);
      setParseJobStatus(status.status === 'running' ? 'running' : status.status);
      if (status.status === 'done') {
        const latestQuestions = await syncLiveQuestions(examId);
        setParsedQuestionCount(latestQuestions.total);
        setParseProgress(100);
        updateCurrentStep('done');
      }
    } catch (error) {
      if (isExamUnavailableError(error)) {
        handleExamUnavailable();
        return;
      }
      showError(error instanceof Error ? error.message : 'Failed to retry parse page');
    }
  };

  const closeModal = () => {
    if (isRunning) {
      requestControllerRef.current?.abort();
    }
    setIsModalOpen(false);
    resetWizardState();
    openWizardButtonRef.current?.focus();
  };

  const runCreateAndUpload = async () => {
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Exam name and at least one key file are required.');
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

    const logStep = (entry: StepLog) => setStepLogs((prev) => [...prev, entry]);

    try {
      setIsRunning(true);
      updateCurrentStep('creating');
      markChecklist('creating_exam', 'active');
      const exam = await api.createExam(modalName.trim(), requestOptions);
      const activeExamId = exam.id;
      examId = activeExamId;
      setCurrentExamId(activeExamId);
      logStep({ step: 'creating', endpointUrl: buildApiUrl('exams'), status: 200, responseSnippet: JSON.stringify(exam).slice(0, 500) });
      markChecklist('creating_exam', 'done');
      setParseProgress(14);

      updateCurrentStep('uploading');
      markChecklist('uploading_key', 'active');
      const { token } = await api.getBlobUploadToken();
      const uploaded = await Promise.all(
        modalFiles.map((file) => uploadToBlob(file, `exams/${activeExamId}/key/${crypto.randomUUID()}-${file.name}`, token)),
      );
      const registerPayload = uploaded.map((file, index) => ({
        original_filename: modalFiles[index].name,
        blob_pathname: file.pathname,
        content_type: file.contentType,
        size_bytes: file.size,
      }));
      const uploadResult = await api.registerExamKeyFiles(activeExamId, registerPayload);
      logStep({ step: 'uploading', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/register`), status: 200, responseSnippet: JSON.stringify(uploadResult).slice(0, 500) });
      markChecklist('uploading_key', 'done');
      setParseProgress(28);

      updateCurrentStep('building_pages');
      markChecklist('building_key_pages', 'active');
      const buildPages = await api.buildExamKeyPages(activeExamId, requestOptions);
      logStep({ step: 'building_pages', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/build-pages`), status: 200, responseSnippet: JSON.stringify(buildPages).slice(0, 500) });
      markChecklist('building_key_pages', 'done');
      setParseProgress(42);
      setParsePageCount(buildPages.length);

      updateCurrentStep('parsing');
      markChecklist('reading_questions', 'active');
      setParseProgress(50);
      startParseProgress();
      const started = await api.startExamKeyParse(activeExamId, requestOptions);
      setParseJobId(started.job_id);
      setLatestBackendParseJobId(started.job_id);
      setParseStartReusedJob(Boolean(started.reused));
      setParsePageCount(started.page_count);
      setCurrentParsingPageNumber(0);
      initializePageStatuses(started.page_count);
      setParseJobStatus('running');
      logStep({ step: 'parsing', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/parse/start`), status: 200, responseSnippet: JSON.stringify(started).slice(0, 500) });

      let loopPagesDone = 0;
      while (true) {
        const nextPageNumber = Math.min(started.page_count, Math.max(1, loopPagesDone + 1));
        setCurrentParsingPageNumber(nextPageNumber);
        markPageStatus(nextPageNumber, 'active');
        const next = await api.parseExamKeyNext(activeExamId, started.job_id, PARSE_BATCH_SIZE, requestOptions);
        const processed = applyParseBatchResult(next);
        loopPagesDone = next.pages_done;
        const liveSync = await syncLiveQuestions(activeExamId);
        const donePages = (next.page_results || []).filter((result) => result.status === 'done').map((result) => result.page_number);
        donePages.forEach((pageNumber) => {
          if (liveSync.newCount === 0) {
            setEmptyPageNotes((prev) => (prev.includes(pageNumber) ? prev : [...prev, pageNumber]));
          }
        });
        logStep({ step: 'parsing', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/parse/next?job_id=${started.job_id}&batch_size=${PARSE_BATCH_SIZE}`), status: 200, responseSnippet: JSON.stringify(next).slice(0, 500) });
        if (processed.length === 0 || next.status !== 'running') {
          break;
        }

        const failedBatchPages = (next.page_results || []).filter((result) => result.status === 'failed').map((result) => result.page_number);
        if (failedBatchPages.length > 0) {
          setFailedPage(failedBatchPages[0]);
          showWarning(`Failed pages: ${failedBatchPages.join(', ')} — retry this parse job from the wizard.`);
        }
      }

      const status = await api.getExamKeyParseStatus(activeExamId, started.job_id, requestOptions);
      setRunningTotals((prev) => pickPreferredTotals(prev, status.totals));
      const finished = await api.finishExamKeyParse(activeExamId, started.job_id, requestOptions);
      setRunningTotals((prev) => pickPreferredTotals(prev, finished.totals));
      logStep({ step: 'parsing', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/parse/finish?job_id=${started.job_id}`), status: 200, responseSnippet: JSON.stringify(finished).slice(0, 500) });

      markChecklist('reading_questions', 'done');
      markChecklist('detecting_marks', 'done');
      markChecklist('drafting_rubric', 'done');
      markChecklist('finalizing', 'done');
      setParseProgress(100);
      const questionCount = extractParsedQuestionCount(finished.questions);
      setParsedQuestionCount(questionCount);
      updateCurrentStep('done');
      localStorage.setItem(`supermarks:lastParse:${activeExamId}`, JSON.stringify(finished));

      await loadExams();
      showSuccess('This exam is saved. You can safely leave and come back later.');
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
        if (isExamUnavailableError(err)) {
          handleExamUnavailable();
          return;
        }
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
      clearIntervals();
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

    if (wizardError.step === 'creating') {
      await runCreateAndUpload();
      return;
    }

    let activeExamId: number;
    try {
      activeExamId = assertWizardExamId();
    } catch {
      return;
    }

    const controller = new AbortController();
    requestControllerRef.current = controller;
    const requestOptions = { signal: controller.signal };
    const logStep = (entry: StepLog) => setStepLogs((prev) => [...prev, entry]);

    try {
      setIsRunning(true);
      setWizardError(null);

      if (wizardError.step === 'uploading') {
        updateCurrentStep('uploading');
        markChecklist('uploading_key', 'active');
        const { token } = await api.getBlobUploadToken();
        const uploaded = await Promise.all(
          modalFiles.map((file) => uploadToBlob(file, `exams/${activeExamId}/key/${crypto.randomUUID()}-${file.name}`, token)),
        );
        const registerPayload = uploaded.map((file, index) => ({
          original_filename: modalFiles[index].name,
          blob_pathname: file.pathname,
          content_type: file.contentType,
          size_bytes: file.size,
        }));
        const uploadResult = await api.registerExamKeyFiles(activeExamId, registerPayload);
        logStep({ step: 'uploading', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/register`), status: 200, responseSnippet: JSON.stringify(uploadResult).slice(0, 500) });
        markChecklist('uploading_key', 'done');
        showSuccess('Upload step retried successfully.');
        return;
      }

      if (wizardError.step === 'building_pages') {
        updateCurrentStep('building_pages');
        markChecklist('building_key_pages', 'active');
        const buildPages = await api.buildExamKeyPages(activeExamId, requestOptions);
        logStep({ step: 'building_pages', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/build-pages`), status: 200, responseSnippet: JSON.stringify(buildPages).slice(0, 500) });
        markChecklist('building_key_pages', 'done');
        setParsePageCount(buildPages.length);
        showSuccess('Build pages step retried successfully.');
        return;
      }

      if (wizardError.step === 'parsing') {
        updateCurrentStep('parsing');
        markChecklist('reading_questions', 'active');
        startParseProgress();

        let activeJobId = parseJobId;
        if (!activeJobId) {
          const started = await api.startExamKeyParse(activeExamId, requestOptions);
          activeJobId = started.job_id;
          setParseJobId(started.job_id);
          setLatestBackendParseJobId(started.job_id);
          setParseStartReusedJob(Boolean(started.reused));
          setParsePageCount(started.page_count);
          setCurrentParsingPageNumber(0);
          initializePageStatuses(started.page_count);
          setParseJobStatus('running');
          logStep({ step: 'parsing', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/parse/start`), status: 200, responseSnippet: JSON.stringify(started).slice(0, 500) });
        }

        const statusBeforeRetry = await api.getExamKeyParseStatus(activeExamId, activeJobId, requestOptions);
        const failedPages = statusBeforeRetry.pages.filter((page) => page.status === 'failed').map((page) => page.page_number);
        for (const pageNumber of failedPages) {
          await api.retryExamKeyParsePage(activeExamId, activeJobId, pageNumber, requestOptions);
          markPageStatus(pageNumber, 'pending');
        }

        let retryPagesDone = statusBeforeRetry.pages_done;
        while (true) {
          const pageNumber = Math.min(statusBeforeRetry.page_count, Math.max(1, retryPagesDone + 1));
          setCurrentParsingPageNumber(pageNumber);
          markPageStatus(pageNumber, 'active');
          const next = await api.parseExamKeyNext(activeExamId, activeJobId, PARSE_BATCH_SIZE, requestOptions);
          const processed = applyParseBatchResult(next);
          retryPagesDone = next.pages_done;
          await syncLiveQuestions(activeExamId);
          logStep({ step: 'parsing', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/parse/next?job_id=${activeJobId}&batch_size=${PARSE_BATCH_SIZE}`), status: 200, responseSnippet: JSON.stringify(next).slice(0, 500) });
          if (processed.length === 0 || next.status !== 'running') break;
        }

        const status = await api.getExamKeyParseStatus(activeExamId, activeJobId, requestOptions);
        setRunningTotals((prev) => pickPreferredTotals(prev, status.totals));
        const finished = await api.finishExamKeyParse(activeExamId, activeJobId, requestOptions);
        setRunningTotals((prev) => pickPreferredTotals(prev, finished.totals));
        logStep({ step: 'parsing', endpointUrl: buildApiUrl(`exams/${activeExamId}/key/parse/finish?job_id=${activeJobId}`), status: 200, responseSnippet: JSON.stringify(finished).slice(0, 500) });

        markChecklist('reading_questions', 'done');
        markChecklist('detecting_marks', 'done');
        markChecklist('drafting_rubric', 'done');
        markChecklist('finalizing', 'done');
        setParseProgress(100);
        setParsedQuestionCount(extractParsedQuestionCount(finished.questions));
        updateCurrentStep('done');
        showSuccess('Parsing step retried successfully.');
      }
    } catch (err) {
      const stepName = currentStepRef.current;
      const stepEndpoint = endpointForStep(stepName, currentExamId);
      if (isNetworkFetchError(err) || isAbortError(err)) {
        setWizardError({
          step: stepName,
          summary: `Step: ${stepName} | Status: network-error`,
          details: err instanceof Error ? err.stack || err.message : String(err),
          attemptedUrl: stepEndpoint,
          method: 'UNKNOWN',
          status: 'network-error',
          bodySnippet: err instanceof Error ? err.message : String(err),
          isAbort: isAbortError(err),
        });
      } else if (err instanceof ApiError) {
        if (isExamUnavailableError(err)) {
          handleExamUnavailable();
          return;
        }
        setWizardError({
          step: stepName,
          summary: `Step: ${stepName} | ${err.method} ${err.url} | Status: ${err.status}`,
          details: JSON.stringify({ method: err.method, url: err.url, status: err.status, bodySnippet: err.responseBodySnippet || '<empty>' }, null, 2),
          attemptedUrl: err.url,
          method: err.method,
          status: err.status,
          contentType: undefined,
          bodySnippet: err.responseBodySnippet,
        });
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
      }
    } finally {
      clearIntervals();
      setIsRunning(false);
      requestControllerRef.current = null;
    }
  };

  const onPingApi = async () => {
    try {
      setIsPingingApi(true);
      const result = await pingApiHealth();
      setPingResult(`status=${result.status} body=${result.body || '<empty>'}`);
    } catch (error) {
      setPingResult(`error=${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsPingingApi(false);
    }
  };

  const runRawFetchProbe = async (method: 'GET' | 'POST', attemptedUrl: string, options: RequestInit = {}) => {
    try {
      setIsRunningRawProbe(true);
      const response = await fetch(attemptedUrl, { method, ...options });
      const body = await response.text();
      setRawFetchProbeResult({
        attemptedUrl,
        responseStatus: response.status,
        contentType: response.headers.get('content-type'),
        bodySnippet: body.slice(0, RAW_FETCH_SNIPPET_LENGTH),
        finalUrl: response.url,
      });
    } catch (error) {
      setRawFetchProbeResult({
        attemptedUrl,
        errorName: error instanceof Error ? error.name : 'UnknownError',
        errorMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setIsRunningRawProbe(false);
    }
  };

  const onRawHealthProbe = async () => {
    const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim() || '';
    const url = `${configuredBase.replace(/\/api\/?$/, '')}/health`;
    await runRawFetchProbe('GET', url);
  };

  const onRawGetExamsProbe = async () => {
    const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim() || '';
    const url = `${configuredBase}/exams`;
    await runRawFetchProbe('GET', url);
  };

  const onRawPostExamsProbe = async () => {
    const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim() || '';
    const url = `${configuredBase}/exams`;
    await runRawFetchProbe('POST', url, {
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: `Probe ${Date.now()}` }),
    });
  };

  return (
    <div className="page-stack">
      <section className="card card--hero stack">
        <div className="page-header">
          <div>
            <p className="page-eyebrow">Teacher workspace</p>
            <h1 className="page-title">Exams</h1>
            <p className="page-subtitle">Set up an exam, upload marked papers, confirm the extracted totals, and export the class table. The interface should feel calm enough to use during real school admin, not like a debug console.</p>
          </div>
          <div className="page-toolbar">
            {showDevTools && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setDiagnosticsOpen((prev) => !prev)}
                aria-expanded={diagnosticsOpen}
                aria-controls="diagnostics-panel"
              >
                {diagnosticsOpen ? 'Hide API diagnostics' : 'API diagnostics'}
              </button>
            )}
          </div>
        </div>

        <div className="metric-grid">
          <article className="metric-card">
            <p className="metric-label">Open workspaces</p>
            <p className="metric-value">{exams.length}</p>
            <p className="metric-meta">Saved exams ready to reopen</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Needs attention</p>
            <p className="metric-value">{activeExamCount}</p>
            <p className="metric-meta">Workspaces still moving toward export</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Recent</p>
            <p className="metric-value">{recentExams.length}</p>
            <p className="metric-meta">Most recent work is surfaced first</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Latest activity</p>
            <p className="metric-value">{recentExams.length > 0 ? formatExamDate(recentExams[0].created_at) : '—'}</p>
            <p className="metric-meta">Most recent workspace created</p>
          </article>
        </div>
      </section>

      <div className="workflow-grid">
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Start a new exam</h2>
              <p className="subtle-text">Start with the exam shell. Bulk paper upload and totals confirmation happen inside the workspace.</p>
            </div>
            <span className="status-pill status-ready">Primary action</span>
          </div>
          <div className="review-readonly-block surface-muted callout-card">
            <strong>Minimal first step</strong>
            <p className="subtle-text" style={{ marginTop: '.35rem' }}>Create the exam first. Then upload graded papers and confirm the totals table for export.</p>
          </div>
          <div className="actions-row" style={{ marginTop: 0 }}>
            <button type="button" className="btn btn-primary" onClick={() => setIsModalOpen(true)}>
              Create exam
            </button>
          </div>
        </section>

        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Recent workspaces</h2>
              <p className="subtle-text">Jump back into the exams you touched most recently.</p>
            </div>
            <span className="status-pill status-neutral">{recentExams.length} shown</span>
          </div>
          {recentExams.length === 0 ? (
            <p className="subtle-text">No exams yet. Create the first one above.</p>
          ) : (
            <div className="stack" style={{ gap: '.7rem' }}>
              {recentExams.map((exam) => {
                const status = normalizeExamStatus(exam.status);
                const isDeleting = deletingExamId === exam.id;
                return (
                  <article key={`recent-${exam.id}`} className="workspace-card">
                    <div className="workspace-card-header">
                      <div>
                        <Link className="workspace-card-title" to={`/exams/${exam.id}`}>{exam.name}</Link>
                        <p className="subtle-text" style={{ marginTop: '.25rem' }}>Created {formatExamDateTime(exam.created_at)}</p>
                      </div>
                      <span className={`status-pill ${status.tone}`}>{status.label}</span>
                    </div>
                    <div className="actions-row" style={{ marginTop: 0 }}>
                      <Link className="btn btn-secondary btn-sm" to={`/exams/${exam.id}`}>Open workspace</Link>
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
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
      </div>

      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">Exam library</h2>
            <p className="subtle-text">Search and reopen saved exam workspaces.</p>
          </div>
          <span className="status-pill status-neutral">{filteredExams.length} match{filteredExams.length === 1 ? '' : 'es'}</span>
        </div>

        <label htmlFor="exam-search">Search exams</label>
        <input
          id="exam-search"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder="Search by exam name"
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
            <p className="subtle-text" style={{ marginTop: '.35rem' }}>No saved exam matches that search yet. Start a new exam above to create one.</p>
          </div>
        )}

        {!loading && filteredExams.length > 0 && (
          <div className="workspace-card-grid">
            {filteredExams.map((exam) => {
              const status = normalizeExamStatus(exam.status);
              const isDeleting = deletingExamId === exam.id;
              return (
                <article key={exam.id} className="workspace-card">
                  <div className="workspace-card-header">
                    <div>
                      <p className="workspace-card-kicker">Exam workspace</p>
                      <Link className="workspace-card-title" to={`/exams/${exam.id}`}>{exam.name}</Link>
                    </div>
                    <span className={`status-pill ${status.tone}`}>{status.label}</span>
                  </div>
                  <div className="workspace-card-meta">
                    <span>Created {formatExamDate(exam.created_at)}</span>
                    <span>Exam ID {exam.id}</span>
                  </div>
                  <div className="actions-row" style={{ marginTop: 0 }}>
                    <Link className="btn btn-secondary btn-sm" to={`/exams/${exam.id}`}>Open workspace</Link>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
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

      {showDevTools && (
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Diagnostics</h2>
              <p className="subtle-text">Available when needed, visually out of the way when you are just trying to run the workflow.</p>
            </div>
            <span className="status-pill status-neutral">Technical</span>
          </div>
          <div className="review-readonly-block surface-muted">
            <div className="inline-stat-row">
              <span className="status-pill status-neutral">API base: {maskApiBaseUrl(API_BASE_URL)}</span>
              <span className="status-pill status-neutral">Backend: {backendVersion}</span>
            </div>
          </div>
          {diagnosticsOpen && (
            <div id="diagnostics-panel" className="stack" style={{ marginTop: '0.25rem' }}>
              <p className="subtle-text">Configured API base: {maskApiBaseUrl(API_BASE_URL)}</p>
              <button type="button" className="btn btn-secondary" onClick={onPingApi} disabled={isPingingApi}>
                {isPingingApi ? 'Pinging...' : 'Ping API'}
              </button>
              {pingResult && <pre className="code-box">{pingResult}</pre>}

              <div className="stack" style={{ marginTop: '0.75rem' }}>
                <h3 style={{ marginBottom: 0 }}>Raw Fetch Probe</h3>
                <button type="button" className="btn btn-secondary" onClick={onRawHealthProbe} disabled={isRunningRawProbe}>
                  Raw GET /health (no headers)
                </button>
                <button type="button" className="btn btn-secondary" onClick={onRawGetExamsProbe} disabled={isRunningRawProbe}>
                  Raw GET /api/exams (no headers)
                </button>
                <button type="button" className="btn btn-secondary" onClick={onRawPostExamsProbe} disabled={isRunningRawProbe}>
                  Raw POST /api/exams (no headers)
                </button>
                {rawFetchProbeResult && (
                  <pre className="code-box">{[
                    `attempted URL: ${rawFetchProbeResult.attemptedUrl}`,
                    `response.status: ${rawFetchProbeResult.responseStatus ?? 'n/a'}`,
                    `response content-type: ${rawFetchProbeResult.contentType ?? 'n/a'}`,
                    `final URL: ${rawFetchProbeResult.finalUrl ?? 'n/a'}`,
                    `body (first ${RAW_FETCH_SNIPPET_LENGTH} chars): ${rawFetchProbeResult.bodySnippet ?? '<empty>'}`,
                    rawFetchProbeResult.errorName ? `fetch error: ${rawFetchProbeResult.errorName} ${rawFetchProbeResult.errorMessage || ''}`.trim() : null,
                  ].filter(Boolean).join('\n')}</pre>
                )}
              </div>
            </div>
          )}
        </section>
      )}

      {isModalOpen && (
        <Modal title="Create exam" onClose={closeModal} initialFocusRef={examNameRef}>
          <p className="subtle-text">Give the exam a clear name. The upload and confirmation flow lives in the next screen.</p>
          <form onSubmit={onCreateExamWorkspace} className="stack">
            <label htmlFor="exam-name">Exam name</label>
            <input
              id="exam-name"
              ref={examNameRef}
              value={modalName}
              onChange={(event) => setModalName(event.target.value)}
              placeholder="e.g. Midterm 1"
              required
              disabled={isRunning}
            />
            <div className="review-readonly-block surface-muted">
              <strong>Next</strong>
              <p className="subtle-text" style={{ marginTop: '.35rem' }}>
                Open the exam workspace, upload graded student papers, confirm extracted totals, then export the class table as CSV.
              </p>
            </div>

            <div className="actions-row">
              <button type="submit" className="btn btn-primary" disabled={isRunning}>
                {isRunning ? 'Creating…' : 'Create exam'}
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
