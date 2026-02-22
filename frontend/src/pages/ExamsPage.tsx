import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { API_BASE_URL, ApiError, api, buildApiUrl, IS_PROD_ABSOLUTE_API_BASE_CONFIGURED } from '../api/client';
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
  bodySnippet: string;
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

  const endpointMap = {
    create: buildApiUrl('exams'),
    upload: createdExamId ? buildApiUrl(`exams/${createdExamId}/key/upload`) : buildApiUrl('exams/{exam_id}/key/upload'),
    parse: createdExamId ? buildApiUrl(`exams/${createdExamId}/key/parse`) : buildApiUrl('exams/{exam_id}/key/parse'),
  };

  const pingApi = async () => {
    setPinging(true);
    try {
      const response = await fetch('/api/health');
      const responseText = await response.text();
      setPingResult({
        status: response.status,
        bodySnippet: responseText.slice(0, 200),
      });
    } catch (error) {
      setPingResult({
        status: 'network-error',
        bodySnippet: '',
        message: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
      });
    } finally {
      setPinging(false);
    }
  };

  const loadExams = async () => {
    try {
      setLoading(true);
      const fetchedExams = await api.getExams();
      setExams(fetchedExams);
      const costEntries = await Promise.all(fetchedExams.map(async (exam) => {
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
      const createEndpoint = buildApiUrl('exams');
      const exam = await api.createExam(modalName.trim());
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
          ? buildApiUrl('exams')
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
          <div className="card modal stack">
            <h2>Enter Exam Key</h2>
            <p className="subtle-text wizard-step-banner">Current step: {isRunning ? step : 'ready'}</p>
            {IS_PROD_ABSOLUTE_API_BASE_CONFIGURED && (
              <p className="warning-text">Warning: production API base should be relative (/api), not an absolute URL.</p>
            )}
            <form onSubmit={onCreateAndUpload} className="stack" encType="multipart/form-data">
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
                  <p><strong>Create endpoint:</strong> {endpointMap.create}</p>
                  <p><strong>Upload endpoint:</strong> {endpointMap.upload}</p>
                  <p><strong>Parse endpoint:</strong> {endpointMap.parse}</p>
                  <div className="actions-row">
                    <button type="button" onClick={() => void pingApi()} disabled={pinging || isRunning}>
                      {pinging ? 'Pinging…' : 'Ping API'}
                    </button>
                  </div>
                  {pingResult && (
                    <div className="wizard-detail-block">
                      <p><strong>Ping status:</strong> {pingResult.status}</p>
                      <p><strong>Ping response:</strong> {(pingResult.bodySnippet || '<empty>').slice(0, 200)}</p>
                      {pingResult.message && <p><strong>Ping error:</strong> {pingResult.message}</p>}
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

              <div className="actions-row">
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
        {!loading && exams.length === 0 && <p>No exams yet.</p>}
        <ul>
          {exams.map((exam) => (
            <li key={exam.id}>
              <Link to={`/exams/${exam.id}`}>{exam.name}</Link>
              {examCosts[exam.id] && <span className="subtle-text"> (${examCosts[exam.id].total_cost.toFixed(3)})</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
