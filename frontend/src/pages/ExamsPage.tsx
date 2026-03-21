import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { API_BASE_URL, ApiError, api, buildApiUrl, getBackendVersion, maskApiBaseUrl, pingApiHealth } from '../api/client';
import { DebugPanel } from '../components/DebugPanel';
import { FileUploader } from '../components/FileUploader';
import { Modal } from '../components/Modal';
import { useToast } from '../components/ToastProvider';
import type { ExamRead } from '../types/api';

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

const MB = 1024 * 1024;
const LARGE_FILE_BYTES = 8 * MB;
const LARGE_TOTAL_BYTES = 12 * MB;
const RAW_FETCH_SNIPPET_LENGTH = 300;

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
  const [failedSummary, setFailedSummary] = useState<string | null>(null);
  const [parsePageCount, setParsePageCount] = useState(0);
  const [checklistSteps, setChecklistSteps] = useState<ParseChecklistStep[]>(() => initChecklist());
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [backendVersion, setBackendVersion] = useState<string>('loading...');

  const requestControllerRef = useRef<AbortController | null>(null);
  const currentStepRef = useRef<WizardStep>('creating');
  const openWizardButtonRef = useRef<HTMLButtonElement>(null);
  const examNameRef = useRef<HTMLInputElement>(null);

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
  const activeExamCount = exams.filter((exam) => !normalizeExamStatus(exam.status).label.toLowerCase().includes('complete')).length;

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

  const markChecklist = (id: ParseChecklistStepId, status: ParseChecklistStatus) => {
    setChecklistSteps((prev) => prev.map((item) => (item.id === id ? { ...item, status } : item)));
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
    setParsedQuestionCount(null);
    setWizardError(null);
    setCurrentExamId(null);
    setStepLogs([]);
    setStep('creating');
    setParseProgress(0);
    setFailedSummary(null);
    setParsePageCount(0);
    setChecklistSteps(initChecklist());
  };

  const resetWizardState = () => {
    setModalName('');
    setModalFiles([]);
    setAllowLargeUpload(false);
    resetWizardProgress();
  };
  const closeModal = () => {
    if (isRunning) {
      requestControllerRef.current?.abort();
    }
    setIsModalOpen(false);
    resetWizardState();
    openWizardButtonRef.current?.focus();
  };

  const ingestWizardTestBundle = async (
    examId: number,
    files: File[],
    requestOptions: RequestInit,
    logStep: (entry: StepLog) => void,
  ) => {
    updateCurrentStep('uploading');
    markChecklist('uploading_key', 'active');
    const preview = await api.uploadBulkSubmissionsFile(examId, files, undefined, requestOptions);
    logStep({
      step: 'uploading',
      endpointUrl: buildApiUrl(`exams/${examId}/submissions/bulk`),
      status: 201,
      responseSnippet: JSON.stringify(preview).slice(0, 500),
    });
    markChecklist('uploading_key', 'done');
    setParseProgress(38);

    updateCurrentStep('building_pages');
    markChecklist('building_key_pages', 'active');
    setParsePageCount(preview.page_count);
    setParsedQuestionCount(preview.candidates.length);
    markChecklist('building_key_pages', 'done');
    setParseProgress(56);

    updateCurrentStep('parsing');
    markChecklist('reading_questions', 'active');
    setParseProgress(72);

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
    logStep({
      step: 'parsing',
      endpointUrl: buildApiUrl(`exams/${examId}/submissions/bulk/${preview.bulk_upload_id}/finalize`),
      status: 200,
      responseSnippet: JSON.stringify(finalized).slice(0, 500),
    });
    markChecklist('reading_questions', 'done');
    markChecklist('detecting_marks', 'done');
    markChecklist('drafting_rubric', 'done');
    markChecklist('finalizing', 'done');
    setParseProgress(100);
    updateCurrentStep('done');

    return {
      pageCount: preview.page_count,
      candidateCount: preview.candidates.length,
      submissionCount: finalized.submissions.length,
    };
  };

  const runCreateAndUpload = async () => {
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Exam name and at least one paper file are required.');
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

      await ingestWizardTestBundle(activeExamId, modalFiles, requestOptions, logStep);

      await loadExams();
      showSuccess('The confirmation queue is ready.');
      closeModal();
      navigate(`/exams/${activeExamId}`);
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

    const activeExamId = currentExamId;
    if (!activeExamId) {
      showError('No active exam id for this wizard session');
      return;
    }

    const controller = new AbortController();
    requestControllerRef.current = controller;
    const requestOptions = { signal: controller.signal };
    const logStep = (entry: StepLog) => setStepLogs((prev) => [...prev, entry]);

    try {
      setIsRunning(true);
      setWizardError(null);

      if (modalFiles.length === 0) {
        showWarning('Upload at least one PDF, PNG, or JPG paper file.');
        return;
      }

      await ingestWizardTestBundle(activeExamId, modalFiles, requestOptions, logStep);
      showSuccess('Intake step retried successfully.');
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
        </div>

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
            <p className="metric-value">{Math.min(sortedExams.length, 3)}</p>
            <p className="metric-meta">Most recent work is surfaced first</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Latest activity</p>
            <p className="metric-value">{sortedExams.length > 0 ? formatExamDate(sortedExams[0].created_at) : '—'}</p>
            <p className="metric-meta">Most recent workspace created</p>
          </article>
        </div>
      </section>

      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">Test library</h2>
            <p className="subtle-text">Search and reopen saved test workspaces.</p>
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
            <div className="actions-row" style={{ marginTop: 0 }}>
              <span className="status-pill status-neutral">Technical</span>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setDiagnosticsOpen((prev) => !prev)}
                aria-expanded={diagnosticsOpen}
                aria-controls="diagnostics-panel"
              >
                {diagnosticsOpen ? 'Hide API diagnostics' : 'API diagnostics'}
              </button>
            </div>
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
        <Modal title="Create exam and intake papers" onClose={closeModal} initialFocusRef={examNameRef}>
          <p className="subtle-text">Name the exam, upload one PDF or multiple paper photos, and let SuperMarks prepare the names-first confirmation queue for totals capture.</p>
          <form onSubmit={onCreateAndUpload} className="stack">
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
              <p className="subtle-text" style={{ margin: 0 }}>
                Upload one PDF or multiple PNG/JPG paper photos to start the queue.
              </p>
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
            <div className="review-readonly-block surface-muted">
              <strong>Next</strong>
              <p className="subtle-text" style={{ marginTop: '.35rem' }}>
                SuperMarks will create the exam, detect student names from the uploaded papers, build the confirmation queue, then send you to the workspace where totals are captured and confirmed.
              </p>
            </div>
            {(isRunning || parseProgress > 0 || wizardError || step === 'done') && (
              <div className="stack" style={{ gap: '.65rem' }}>
                <div className="metric-card">
                  <div className="panel-title-row" style={{ marginBottom: '.45rem' }}>
                    <strong>Exam entry progress</strong>
                    <span className={`status-pill ${step === 'done' ? 'status-complete' : wizardError ? 'status-blocked' : 'status-in-progress'}`}>
                      {wizardError ? 'Needs attention' : step === 'done' ? 'Queue ready' : step.replace(/_/g, ' ')}
                    </span>
                  </div>
                  <progress max={100} value={parseProgress} style={{ width: '100%' }} />
                  <p className="metric-meta" style={{ marginTop: '.5rem' }}>
                    {wizardError ? wizardError.summary : step === 'done' ? `Prepared ${parsedQuestionCount ?? 0} paper${(parsedQuestionCount ?? 0) === 1 ? '' : 's'} for name review and totals capture.` : `Current step: ${step.replace(/_/g, ' ')}`}
                  </p>
                </div>
                <div className="stack" style={{ gap: '.45rem' }}>
                  {checklistSteps.map((item) => (
                    <div key={item.id} className="inline-stat-row">
                      <span className={`status-pill ${item.status === 'done' ? 'status-complete' : item.status === 'failed' ? 'status-blocked' : item.status === 'active' ? 'status-in-progress' : 'status-neutral'}`}>
                        {item.status}
                      </span>
                      <span>{item.label}</span>
                    </div>
                  ))}
                </div>
                {failedSummary && <p className="warning-text" style={{ margin: 0 }}>{failedSummary}</p>}
                {wizardError && (
                  <div className="actions-row" style={{ marginTop: 0 }}>
                    <button type="button" className="btn btn-secondary" onClick={() => void onRetryFailedStep()} disabled={isRunning}>
                      Retry failed step
                    </button>
                  </div>
                )}
                {step === 'done' && currentExamId && (
                  <div className="actions-row" style={{ marginTop: 0 }}>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => {
                        const examId = currentExamId;
                        closeModal();
                        navigate(`/exams/${examId}`);
                      }}
                    >
                      Open confirmation queue
                    </button>
                  </div>
                )}
              </div>
            )}

            <div className="actions-row">
              <button type="submit" className="btn btn-primary" disabled={isRunning}>
                {isRunning ? 'Preparing queue…' : 'Create exam and intake papers'}
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
