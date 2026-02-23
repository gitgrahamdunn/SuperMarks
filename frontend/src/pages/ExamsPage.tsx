import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  API_BASE_URL,
  API_KEY,
  ApiError,
  ApiInvalidJsonError,
  api,
  buildApiUrl,
} from '../api/client';
import { DebugPanel } from '../components/DebugPanel';
import { useToast } from '../components/ToastProvider';
import type { ExamCostResponse, ExamRead } from '../types/api';

type WizardStep = 'creating' | 'uploading' | 'building_pages' | 'parsing' | 'done';

type StepLog = {
  step: WizardStep;
  endpointUrl: string;
  status: number | 'network-error';
  responseSnippet: string;
  exceptionMessage?: string;
};

type WizardError = {
  summary: string;
  details: unknown;
};

type PingResult = {
  status: number | 'network-error';
  body: string;
  message?: string;
};

type CreateExamGetTestResult = {
  status: number | 'network-error';
  body: string;
  message?: string;
  loadFailedHint?: string;
};

type ProxySelfCheckResult = {
  status: number | 'network-error';
  headers: Record<string, string>;
  body: string;
  message?: string;
};

type PreflightTestResult = {
  status: number | 'network-error';
  proxyHeader: string | null;
  message?: string;
};

type ParseErrorDetails = {
  stage?: string;
  page_index?: number;
  page_count?: number;
  detail?: string;
};

type ParseChecklistStepId =
  | 'creating_exam'
  | 'uploading_key'
  | 'building_key_pages'
  | 'reading_questions'
  | 'detecting_marks'
  | 'drafting_rubric'
  | 'finalizing';

type ParseChecklistStatus = 'pending' | 'active' | 'done' | 'failed';

type ParseChecklistStep = {
  id: ParseChecklistStepId;
  label: string;
  status: ParseChecklistStatus;
};

const CHECKLIST_ORDER: Array<{ id: ParseChecklistStepId; label: string }> = [
  { id: 'creating_exam', label: 'Creating exam' },
  { id: 'uploading_key', label: 'Uploading key' },
  { id: 'building_key_pages', label: 'Building key pages' },
  { id: 'reading_questions', label: 'Reading questions' },
  { id: 'detecting_marks', label: 'Detecting marks' },
  { id: 'drafting_rubric', label: 'Drafting rubric' },
  { id: 'finalizing', label: 'Finalizing' },
];

function initChecklist(): ParseChecklistStep[] {
  return CHECKLIST_ORDER.map((step) => ({ ...step, status: 'pending' }));
}

function stageToChecklistId(stage?: string): ParseChecklistStepId {
  if (!stage) return 'finalizing';
  if (stage.includes('call_openai')) return 'reading_questions';
  if (stage.includes('validate')) return 'detecting_marks';
  if (stage.includes('save')) return 'drafting_rubric';
  if (stage.includes('build_key_pages')) return 'building_key_pages';
  if (stage.includes('upload')) return 'uploading_key';
  if (stage.includes('create')) return 'creating_exam';
  return 'finalizing';
}

function formatElapsed(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, '0');
  const seconds = (totalSeconds % 60).toString().padStart(2, '0');
  return `${minutes}:${seconds}`;
}

interface WizardParseResult {
  questions?: unknown;
  result?: {
    questions?: unknown;
  };
}

const MB = 1024 * 1024;
const LARGE_FILE_BYTES = 8 * MB;
const LARGE_TOTAL_BYTES = 12 * MB;

function formatMb(bytes: number): string {
  return `${(bytes / MB).toFixed(2)} MB`;
}

function extractParsedQuestionCount(parseResult: unknown): number {
  if (Array.isArray(parseResult)) {
    return parseResult.length;
  }

  if (typeof parseResult !== 'object' || !parseResult) {
    return 0;
  }

  const shaped = parseResult as WizardParseResult & { question_count?: unknown };
  if (Array.isArray(shaped.questions)) {
    return shaped.questions.length;
  }

  if (Array.isArray(shaped.result?.questions)) {
    return shaped.result.questions.length;
  }

  if (typeof shaped.question_count === 'number') {
    return shaped.question_count;
  }

  return 0;
}


function normalizeExamListResponse(response: unknown): { exams: ExamRead[]; usedFallback: boolean } {
  if (Array.isArray(response)) {
    return { exams: response as ExamRead[], usedFallback: false };
  }

  if (response && typeof response === 'object') {
    const shaped = response as { exams?: unknown; items?: unknown };
    if (Array.isArray(shaped.exams)) {
      return { exams: shaped.exams as ExamRead[], usedFallback: false };
    }
    if (Array.isArray(shaped.items)) {
      return { exams: shaped.items as ExamRead[], usedFallback: false };
    }
  }

  return { exams: [], usedFallback: true };
}

function isNetworkFetchError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  return error instanceof TypeError || /load failed|failed to fetch|network/i.test(error.message);
}

export function ExamsPage() {
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalName, setModalName] = useState('');
  const [modalFiles, setModalFiles] = useState<File[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<WizardStep>('creating');
  const [wizardError, setWizardError] = useState<WizardError | null>(null);
  const [examCosts, setExamCosts] = useState<Record<number, ExamCostResponse>>({});
  const [parseSummaryMeta, setParseSummaryMeta] = useState<{ model: string; tokens: number; cost: number } | null>(null);
  const [createdExamId, setCreatedExamId] = useState<number | null>(null);
  const [parsedQuestionCount, setParsedQuestionCount] = useState<number | null>(null);
  const [stepLogs, setStepLogs] = useState<StepLog[]>([]);
  const [allowLargeUpload, setAllowLargeUpload] = useState(false);
  const [parseProgress, setParseProgress] = useState(0);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [failedSummary, setFailedSummary] = useState<string | null>(null);
  const [checklistSteps, setChecklistSteps] = useState<ParseChecklistStep[]>(() => initChecklist());
  const parseProgressIntervalRef = useRef<number | null>(null);
  const elapsedIntervalRef = useRef<number | null>(null);
  const { showError, showSuccess, showWarning } = useToast();
  const navigate = useNavigate();

  const totalFileBytes = useMemo(() => modalFiles.reduce((sum, file) => sum + file.size, 0), [modalFiles]);
  const hasSingleLargeFile = useMemo(() => modalFiles.some((file) => file.size > LARGE_FILE_BYTES), [modalFiles]);
  const totalTooLarge = totalFileBytes > LARGE_TOTAL_BYTES;

  const [pingResult, setPingResult] = useState<PingResult | null>(null);
  const [pinging, setPinging] = useState(false);
  const [createExamGetTestResult, setCreateExamGetTestResult] = useState<CreateExamGetTestResult | null>(null);
  const [createExamGetTesting, setCreateExamGetTesting] = useState(false);
  const [hasApiKeyForCreateRequest, setHasApiKeyForCreateRequest] = useState<boolean>(Boolean(API_KEY));
  const [proxySelfCheckResult, setProxySelfCheckResult] = useState<ProxySelfCheckResult | null>(null);
  const [proxySelfCheckLoading, setProxySelfCheckLoading] = useState(false);
  const [preflightTestResult, setPreflightTestResult] = useState<PreflightTestResult | null>(null);
  const [preflightTesting, setPreflightTesting] = useState(false);

  const endpointMap = {
    create: '/api/exams-create',
    upload: createdExamId ? `/api/exams/${createdExamId}/key/upload` : '/api/exams/{exam_id}/key/upload',
    parse: createdExamId ? `/api/exams/${createdExamId}/key/parse` : '/api/exams/{exam_id}/key/parse',
  };

  const pingApi = async () => {
    setPinging(true);
    try {
      const response = await fetch(buildApiUrl('health'));
      const responseJson = await response.json();
      setPingResult({
        status: response.status,
        body: JSON.stringify(responseJson),
      });
    } catch (error) {
      setPingResult({
        status: 'network-error',
        body: '',
        message: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
      });
    } finally {
      setPinging(false);
    }
  };

  const testCreateExamGet = async () => {
    if (!API_KEY) {
      setCreateExamGetTestResult({
        status: 'network-error',
        body: '',
        message: 'Missing VITE_BACKEND_API_KEY',
      });
      return;
    }

    setCreateExamGetTesting(true);
    try {
      const response = await fetch(`/api/exams-create?name=${encodeURIComponent('Ping')}&key=${encodeURIComponent(API_KEY)}`, {
        method: 'GET',
      });
      const responseText = await response.text();
      setCreateExamGetTestResult({
        status: response.status,
        body: responseText,
      });
    } catch (error) {
      const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      const isLoadFailed = /load failed/i.test(message);
      setCreateExamGetTestResult({
        status: 'network-error',
        body: '',
        message,
        loadFailedHint: isLoadFailed
          ? 'This suggests the frontend function route is unavailable or blocked before reaching Vercel serverless runtime.'
          : undefined,
      });
    } finally {
      setCreateExamGetTesting(false);
    }
  };


  const runProxySelfCheck = async () => {
    setProxySelfCheckLoading(true);
    try {
      const response = await fetch('/api/whoami');
      const responseText = await response.text();
      const headers = Object.fromEntries(response.headers.entries());
      setProxySelfCheckResult({
        status: response.status,
        headers,
        body: responseText,
      });
    } catch (error) {
      setProxySelfCheckResult({
        status: 'network-error',
        headers: {},
        body: '',
        message: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
      });
    } finally {
      setProxySelfCheckLoading(false);
    }
  };

  const runPreflightTest = async () => {
    setPreflightTesting(true);
    try {
      const response = await fetch('/api/exams', { method: 'OPTIONS' });
      setPreflightTestResult({
        status: response.status,
        proxyHeader: response.headers.get('x-supermarks-proxy'),
      });
    } catch (error) {
      setPreflightTestResult({
        status: 'network-error',
        proxyHeader: null,
        message: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
      });
    } finally {
      setPreflightTesting(false);
    }
  };

  const loadExams = async () => {
    try {
      setLoading(true);
      const fetchedExams = await api.getExams();
      const normalized = normalizeExamListResponse(fetchedExams);
      setExams(normalized.exams);

      if (normalized.usedFallback) {
        const shapePreview = JSON.stringify(fetchedExams).slice(0, 200);
        console.error('Unexpected exams response shape', fetchedExams);
        showError(`Unexpected exams response shape. Showing empty list. ${shapePreview}`);
      }

      const costEntries = await Promise.all(normalized.exams.map(async (exam) => {
        try {
          return [exam.id, await api.getExamCost(exam.id)] as const;
        } catch {
          return [exam.id, { total_cost: 0, total_tokens: 0, model_breakdown: {} }] as const;
        }
      }));
      setExamCosts(Object.fromEntries(costEntries));
    } catch (loadError) {
      console.error('Failed to load exams', loadError);
      showError(loadError instanceof Error ? loadError.message : 'Failed to load exams');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadExams();
  }, []);

  useEffect(() => {
    if (!isModalOpen) return;
    void pingApi();
    void runProxySelfCheck();
  }, [isModalOpen]);

  const closeModal = () => {
    if (isRunning) {
      return;
    }
    setIsModalOpen(false);
    setModalName('');
    setModalFiles([]);
    setParsedQuestionCount(null);
    setWizardError(null);
    setCreatedExamId(null);
    setStepLogs([]);
    setAllowLargeUpload(false);
    setStep('creating');
    setParseProgress(0);
    setElapsedSeconds(0);
    setFailedSummary(null);
    setChecklistSteps(initChecklist());
    setHasApiKeyForCreateRequest(Boolean(API_KEY));
    setProxySelfCheckResult(null);
    setPreflightTestResult(null);
  };

  const logStep = (entry: StepLog) => {
    setStepLogs((prev) => [...prev, entry]);
  };


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

  const markChecklist = (id: ParseChecklistStepId, status: ParseChecklistStatus) => {
    setChecklistSteps((prev) => prev.map((step) => (step.id === id ? { ...step, status } : step)));
  };

  const startParseProgress = () => {
    clearIntervals();
    setElapsedSeconds(0);
    elapsedIntervalRef.current = window.setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);

    parseProgressIntervalRef.current = window.setInterval(() => {
      setParseProgress((prev) => (prev < 92 ? prev + 2 : prev));
    }, 2000);
  };

  useEffect(() => () => clearIntervals(), []);

  const onCreateAndUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Exam name and at least one key file are required.');
      return;
    }

    if (totalTooLarge && !allowLargeUpload) {
      showWarning('Total files exceed 12 MB. Confirm to continue with upload.');
      return;
    }

    const hasApiKey = Boolean(API_KEY);
    setHasApiKeyForCreateRequest(hasApiKey);
    if (!hasApiKey) {
      setWizardError({
        summary: 'Step: creating | Status: missing-api-key',
        details: {
          step: 'creating',
          message: 'Missing VITE_BACKEND_API_KEY',
          hasApiKey: false,
        },
      });
      showError('Missing VITE_BACKEND_API_KEY');
      return;
    }

    setWizardError(null);
    setParsedQuestionCount(null);
    setCreatedExamId(null);
    setStepLogs([]);
    setParseProgress(0);
    setElapsedSeconds(0);
    setFailedSummary(null);
    setChecklistSteps(initChecklist());

    let examId: number | null = null;

    try {
      setIsRunning(true);

      setStep('creating');
      markChecklist('creating_exam', 'active');
      const createEndpoint = '/api/exams-create';
      const createName = modalName.trim() || `Untitled ${Date.now()}`;
      const exam = await api.createExam(createName);
      examId = exam.id;
      setCreatedExamId(exam.id);
      logStep({
        step: 'creating',
        endpointUrl: createEndpoint,
        status: 200,
        responseSnippet: JSON.stringify(exam).slice(0, 500),
      });
      markChecklist('creating_exam', 'done');
      setParseProgress(14);
      showSuccess('Create step succeeded.');

      setStep('uploading');
      markChecklist('uploading_key', 'active');
      const uploadEndpoint = buildApiUrl(`exams/${exam.id}/key/upload`);
      const uploadResult = await api.uploadExamKey(exam.id, modalFiles);
      logStep({
        step: 'uploading',
        endpointUrl: uploadEndpoint,
        status: 200,
        responseSnippet: JSON.stringify(uploadResult).slice(0, 500),
      });
      markChecklist('uploading_key', 'done');
      setParseProgress(28);
      showSuccess('Upload step succeeded.');

      setStep('building_pages');
      markChecklist('building_key_pages', 'active');
      const buildEndpoint = buildApiUrl(`exams/${exam.id}/key/build-pages`);
      const buildPages = await api.buildExamKeyPages(exam.id);
      logStep({
        step: 'building_pages',
        endpointUrl: buildEndpoint,
        status: 200,
        responseSnippet: JSON.stringify(buildPages).slice(0, 500),
      });
      markChecklist('building_key_pages', 'done');
      setParseProgress(42);
      showSuccess('Pages preview is ready.');

      setStep('parsing');
      markChecklist('reading_questions', 'active');
      setParseProgress(50);
      startParseProgress();
      const parseOutcome = await api.parseExamKeyRaw(exam.id);
      const parseSnippet = (parseOutcome.responseText || JSON.stringify(parseOutcome.data)).slice(0, 500);
      logStep({
        step: 'parsing',
        endpointUrl: parseOutcome.url,
        status: parseOutcome.status,
        responseSnippet: parseSnippet,
      });

      if (parseOutcome.data === null) {
        clearIntervals();
        const parseSummary = `Step: parsing | Status: ${parseOutcome.status}`;
        setWizardError({
          summary: parseSummary,
          details: parseOutcome.data ?? parseOutcome.responseText ?? '<empty>',
        });
        showError(`parsing failed (status ${parseOutcome.status})`);
        return;
      }

      clearIntervals();
      markChecklist('reading_questions', 'done');
      markChecklist('detecting_marks', 'done');
      markChecklist('drafting_rubric', 'done');
      markChecklist('finalizing', 'done');
      setParseProgress(100);
      showSuccess('Parse step succeeded.');
      const questionCount = extractParsedQuestionCount(parseOutcome.data);
      setParsedQuestionCount(questionCount);
      const parsed = parseOutcome.data as { model_used?: string; usage?: { total_tokens?: number }; cost?: { total_cost?: number } };
      setParseSummaryMeta({
        model: parsed.model_used || "unknown",
        tokens: parsed.usage?.total_tokens || 0,
        cost: parsed.cost?.total_cost || 0,
      });
      setStep('done');
      localStorage.setItem(`supermarks:lastParse:${exam.id}`, JSON.stringify(parseOutcome.data));

      if (questionCount === 0) {
        showWarning('Parse completed but returned 0 questions. Opening review anyway.');
      }

      setModalName('');
      setModalFiles([]);
      setIsModalOpen(false);
      await loadExams();
      navigate(`/exams/${exam.id}/review`);
    } catch (err) {
      clearIntervals();
      console.error('Create exam wizard failed', err);
      const stepName = step;
      const stepEndpoint =
        stepName === 'creating'
          ? '/api/exams-create'
          : stepName === 'uploading' && examId
            ? buildApiUrl(`exams/${examId}/key/upload`)
            : stepName === 'building_pages' && examId
              ? buildApiUrl(`exams/${examId}/key/build-pages`)
              : stepName === 'parsing' && examId
                ? buildApiUrl(`exams/${examId}/key/parse`)
                : buildApiUrl('unknown');

      if (isNetworkFetchError(err)) {
        const note = 'No backend logs implies request never sent (bad URL or browser abort)';
        const networkMessage = `Network request failed (browser blocked/aborted). Step: ${stepName}. URL: ${stepEndpoint}`;
        setWizardError({
          summary: `Step: ${stepName} | Status: network-error`,
          details: {
            step: stepName,
            attemptedUrl: stepEndpoint,
            errorName: err instanceof Error ? err.name : 'UnknownError',
            errorMessage: err instanceof Error ? err.message : String(err),
            note,
          },
        });
        logStep({
          step: stepName,
          endpointUrl: stepEndpoint,
          status: 'network-error',
          responseSnippet: '',
          exceptionMessage: err instanceof Error ? err.stack || err.message : String(err),
        });
        showError(networkMessage);
      } else if (err instanceof ApiError) {
        const details = err.responseBodySnippet || '<empty>';
        let parseDetails: ParseErrorDetails | null = null;
        try {
          parseDetails = JSON.parse(details) as ParseErrorDetails;
        } catch {
          parseDetails = null;
        }
        if (stepName === 'parsing') {
          const failedStepId = stageToChecklistId(parseDetails?.stage);
          markChecklist(failedStepId, 'failed');
          const stageLabel = parseDetails?.stage || 'unknown';
          if (parseDetails?.page_index && parseDetails?.page_count) {
            setFailedSummary(`Failed at: ${stageLabel} (page ${parseDetails.page_index}/${parseDetails.page_count})`);
          } else {
            setFailedSummary(`Failed at: ${stageLabel}`);
          }
        }
        setWizardError({
          summary: `Step: ${stepName} | Status: ${err.status}`,
          details: {
            step: stepName,
            attemptedUrl: err.url,
            errorName: err.name,
            errorMessage: err.message,
            responseStatus: err.status,
            responseBodySnippet: details,
            note: 'No backend logs implies request never sent (bad URL or browser abort)',
          },
        });
        logStep({
          step: stepName,
          endpointUrl: err.url,
          status: err.status,
          responseSnippet: err.responseBodySnippet || '<empty>',
          exceptionMessage: err.stack || err.message,
        });
        showError(`${stepName} failed (status ${err.status})`);
      } else if (err instanceof ApiInvalidJsonError) {
        setWizardError({
          summary: `Step: ${stepName} | Status: invalid-json`,
          details: {
            step: stepName,
            attemptedUrl: err.url,
            errorName: err.name,
            errorMessage: err.message,
            responseContentType: err.contentType,
            responseBodySnippet: err.responseBodySnippet || '<empty>',
            note: 'JSON parse failed for create-exam response. Check backend response Content-Type and API base URL.',
          },
        });
        logStep({
          step: stepName,
          endpointUrl: err.url,
          status: 0,
          responseSnippet: err.responseBodySnippet || '<empty>',
          exceptionMessage: `${err.message} (Content-Type: ${err.contentType})`,
        });
        showError(`${stepName} failed due to invalid JSON response (Content-Type: ${err.contentType})`);
      } else {
        const details = err instanceof Error ? err.stack || err.message : 'Unknown error';
        setWizardError({
          summary: `Step: ${stepName} | Status: unknown`,
          details: {
            step: stepName,
            attemptedUrl: stepEndpoint,
            errorName: err instanceof Error ? err.name : 'UnknownError',
            errorMessage: err instanceof Error ? err.message : String(err),
            stackOrDetails: details,
            note: 'No backend logs implies request never sent (bad URL or browser abort)',
          },
        });
        logStep({
          step: stepName,
          endpointUrl: stepEndpoint,
          status: 0,
          responseSnippet: '',
          exceptionMessage: details,
        });
        showError(`${stepName} failed (status unknown)`);
      }
    } finally {
      clearIntervals();
      setIsRunning(false);
    }
  };


  return (
    <div>
      <h1>Exams</h1>
      <div className="card actions-row">
        <button type="button" onClick={() => setIsModalOpen(true)} disabled={isRunning}>Enter Exam Key</button>
      </div>

      {isModalOpen && (
        <div className="modal-backdrop">
          <div className="card modal wizard-modal stack">
            <h2>Enter Exam Key</h2>
            <p className="subtle-text wizard-step-banner">Current step: {isRunning ? step : 'ready'}</p>
            <form onSubmit={onCreateAndUpload} className="stack wizard-modal-form" encType="multipart/form-data">
              <label className="stack">
                Exam name
                <input
                  value={modalName}
                  onChange={(e) => setModalName(e.target.value)}
                  placeholder="e.g. Midterm 1"
                  required
                  disabled={isRunning}
                />
              </label>
              <label className="stack">
                Key files (PDF or image)
                <input
                  type="file"
                  accept="application/pdf,image/png,image/jpeg,image/jpg"
                  onChange={(e) => {
                    setModalFiles(Array.from(e.target.files || []));
                    setAllowLargeUpload(false);
                  }}
                  multiple
                  required
                  disabled={isRunning}
                />
              </label>

              {modalFiles.length > 0 && (
                <div className="file-list-block subtle-text">
                  <strong>Selected files</strong>
                  <ul>
                    {modalFiles.map((file) => (
                      <li key={`${file.name}-${file.size}`}>{file.name} — {formatMb(file.size)}</li>
                    ))}
                  </ul>
                  <p>Total: {formatMb(totalFileBytes)}</p>
                </div>
              )}

              {hasSingleLargeFile && (
                <p className="warning-text">This file may be too large for serverless upload. Try images or a smaller PDF.</p>
              )}

              {totalTooLarge && (
                <div className="warning-strong">
                  <p>Total selection exceeds 12 MB and may fail in serverless environments.</p>
                  <label>
                    <input
                      type="checkbox"
                      checked={allowLargeUpload}
                      onChange={(e) => setAllowLargeUpload(e.target.checked)}
                      disabled={isRunning}
                    />{' '}
                    I understand and want to continue anyway.
                  </label>
                </div>
              )}

              <div className="wizard-progress-block">
                <div className="wizard-progress-header subtle-text">
                  <span>Progress: {parseProgress}%</span>
                  <span>Elapsed: {formatElapsed(elapsedSeconds)}</span>
                </div>
                <div className="wizard-progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={parseProgress}>
                  <div className="wizard-progress-fill" style={{ width: `${parseProgress}%` }} />
                </div>
                <ul className="wizard-checklist subtle-text">
                  {checklistSteps.map((item) => {
                    const marker = item.status === 'done' ? '✓' : item.status === 'active' ? '…' : item.status === 'failed' ? '✕' : '○';
                    return (
                      <li key={item.id} className={`wizard-checklist-item status-${item.status}`}>
                        <span>{marker}</span> {item.label}
                      </li>
                    );
                  })}
                </ul>
              </div>

              {createdExamId && <p className="subtle-text">Exam ID: {createdExamId}</p>}
              {parseSummaryMeta && (
                <div className="subtle-text">
                  <p>Model used: {parseSummaryMeta.model}</p>
                  <p>Tokens: {parseSummaryMeta.tokens.toLocaleString()}</p>
                  <p>Cost: ${parseSummaryMeta.cost.toFixed(4)}</p>
                  {parseSummaryMeta.cost > 0.02 && <p className="warning-text">This key required higher model usage.</p>}
                </div>
              )}
              {failedSummary && <p className="warning-text">{failedSummary}</p>}
              {wizardError && <DebugPanel summary={wizardError.summary} details={wizardError.details} />}

              <details>
                <summary>Network diagnostics</summary>
                <div className="subtle-text stack">
                  <p><strong>window.location.origin:</strong> {window.location.origin}</p>
                  <p><strong>Computed API_BASE:</strong> {API_BASE_URL}</p>
                  <p><strong>API key present:</strong> {Boolean(API_KEY) ? 'true' : 'false'}</p>
                  <p><strong>Create endpoint:</strong> {endpointMap.create}</p>
                  <p><strong>hasApiKey:</strong> {hasApiKeyForCreateRequest ? 'true' : 'false'}</p>
                  <p><strong>Upload endpoint:</strong> {endpointMap.upload}</p>
                  <p><strong>Parse endpoint:</strong> {endpointMap.parse}</p>
                  <div className="actions-row">
                    <button type="button" onClick={() => void pingApi()} disabled={pinging || isRunning}>
                      {pinging ? 'Testing…' : 'Ping API'}
                    </button>
                    <button type="button" onClick={() => void testCreateExamGet()} disabled={createExamGetTesting || isRunning}>
                      {createExamGetTesting ? 'Testing…' : 'Test create'}
                    </button>
                  </div>
                  <div className="proxy-self-check stack">
                    <h4>Proxy self-check</h4>
                    <div className="actions-row">
                      <button type="button" onClick={() => void runProxySelfCheck()} disabled={proxySelfCheckLoading || isRunning}>
                        {proxySelfCheckLoading ? 'Checking…' : 'GET /api/whoami'}
                      </button>
                      <button type="button" onClick={() => void runPreflightTest()} disabled={preflightTesting || isRunning}>
                        {preflightTesting ? 'Testing…' : 'Preflight test (OPTIONS /api/exams)'}
                      </button>
                    </div>
                    {proxySelfCheckResult && (
                      <div className="wizard-detail-block">
                        <p><strong>whoami status:</strong> {proxySelfCheckResult.status}</p>
                        <p><strong>whoami x-supermarks-proxy:</strong> {proxySelfCheckResult.headers['x-supermarks-proxy'] || '<missing>'}</p>
                        <p><strong>whoami headers:</strong> {JSON.stringify(proxySelfCheckResult.headers)}</p>
                        <p><strong>whoami body:</strong> {proxySelfCheckResult.body || '<empty>'}</p>
                        {proxySelfCheckResult.message && <p><strong>whoami error:</strong> {proxySelfCheckResult.message}</p>}
                      </div>
                    )}
                    {preflightTestResult && (
                      <div className="wizard-detail-block">
                        <p><strong>preflight status:</strong> {preflightTestResult.status}</p>
                        <p><strong>preflight x-supermarks-proxy:</strong> {preflightTestResult.proxyHeader || '<missing>'}</p>
                        {preflightTestResult.message && <p><strong>preflight error:</strong> {preflightTestResult.message}</p>}
                      </div>
                    )}
                  </div>
                  {pingResult && (
                    <div className="wizard-detail-block">
                      <p><strong>GET status:</strong> {pingResult.status}</p>
                      <p><strong>GET response:</strong> {pingResult.body || '<empty>'}</p>
                      {pingResult.message && <p><strong>GET error:</strong> {pingResult.message}</p>}
                    </div>
                  )}
                  {createExamGetTestResult && (
                    <div className="wizard-detail-block">
                      <p><strong>GET create status:</strong> {createExamGetTestResult.status}</p>
                      <p><strong>GET create response:</strong> {createExamGetTestResult.body || '<empty>'}</p>
                      {createExamGetTestResult.message && <p><strong>GET create error:</strong> {createExamGetTestResult.message}</p>}
                      {createExamGetTestResult.loadFailedHint && <p><strong>Hint:</strong> {createExamGetTestResult.loadFailedHint}</p>}
                    </div>
                  )}
                  {stepLogs.length === 0 && <p>No step details yet.</p>}
                  {stepLogs.map((entry, index) => (
                    <div key={`${entry.step}-${index}`} className="wizard-detail-block">
                      <p><strong>Step:</strong> {entry.step}</p>
                      <p><strong>Endpoint:</strong> {entry.endpointUrl}</p>
                      <p><strong>Status:</strong> {entry.status}</p>
                      <p><strong>Response snippet:</strong> {(entry.responseSnippet || '<empty>').slice(0, 500)}</p>
                      {entry.exceptionMessage && <p><strong>Exception:</strong> {entry.exceptionMessage}</p>}
                    </div>
                  ))}
                </div>
              </details>

              <div className="actions-row wizard-modal-footer">
                <button type="submit" disabled={isRunning || (totalTooLarge && !allowLargeUpload)}>
                  {isRunning ? 'Working...' : 'Enter exam & parse'}
                </button>
                <button type="button" onClick={closeModal} disabled={isRunning}>Cancel</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="card">
        <h2>Exam List</h2>
        {loading && <p>Loading...</p>}
        {!loading && exams.length === 0 && (
          <div className="empty-state stack">
            <p>No exams yet.</p>
            <button type="button" onClick={() => setIsModalOpen(true)} disabled={isRunning}>Create your first exam</button>
          </div>
        )}
        <ul>
          {Array.isArray(exams) ? exams.map((exam) => (
            <li key={exam.id}>
              <Link to={`/exams/${exam.id}`}>{exam.name}</Link>
              {examCosts[exam.id] && <span className="subtle-text"> (${examCosts[exam.id].total_cost.toFixed(3)})</span>}
            </li>
          )) : null}
        </ul>
      </div>
    </div>
  );
}
