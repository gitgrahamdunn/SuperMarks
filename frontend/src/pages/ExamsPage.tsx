import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { API_BASE_URL, ApiError, api, buildApiUrl, getClientDiagnostics, maskApiBaseUrl, pingApiHealth } from '../api/client';
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
  summary: string;
  details: unknown;
  isAbort?: boolean;
};

type ParseErrorDetails = {
  stage?: string;
  page_index?: number;
  page_count?: number;
};

type ParseTimings = Record<string, number>;
type ParseOutcomeData = { timings?: ParseTimings; page_count?: number; page_index?: number };

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

const isNetworkFetchError = (error: unknown) =>
  error instanceof Error && (error instanceof TypeError || /load failed|failed to fetch|network/i.test(error.message));

const isAbortError = (error: unknown) => {
  if (error instanceof DOMException && error.name === 'AbortError') return true;
  return error instanceof Error && (error.name === 'AbortError' || /aborted/i.test(error.message));
};

export function ExamsPage() {
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalName, setModalName] = useState('');
  const [modalFiles, setModalFiles] = useState<File[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<WizardStep>('creating');
  const [wizardError, setWizardError] = useState<WizardError | null>(null);
  const [createdExamId, setCreatedExamId] = useState<number | null>(null);
  const [parsedQuestionCount, setParsedQuestionCount] = useState<number | null>(null);
  const [stepLogs, setStepLogs] = useState<StepLog[]>([]);
  const [pingResult, setPingResult] = useState<string>('');
  const [isPingingApi, setIsPingingApi] = useState(false);
  const [allowLargeUpload, setAllowLargeUpload] = useState(false);
  const [parseProgress, setParseProgress] = useState(0);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [failedSummary, setFailedSummary] = useState<string | null>(null);
  const [parsePageCount, setParsePageCount] = useState(0);
  const [parsePageIndex, setParsePageIndex] = useState(0);
  const [parseTimings, setParseTimings] = useState<ParseTimings | null>(null);
  const [checklistSteps, setChecklistSteps] = useState<ParseChecklistStep[]>(() => initChecklist());
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);

  const parseProgressIntervalRef = useRef<number | null>(null);
  const elapsedIntervalRef = useRef<number | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const openWizardButtonRef = useRef<HTMLButtonElement>(null);
  const examNameRef = useRef<HTMLInputElement>(null);

  const { showError, showSuccess, showWarning } = useToast();
  const navigate = useNavigate();

  const totalFileBytes = useMemo(() => modalFiles.reduce((sum, file) => sum + file.size, 0), [modalFiles]);
  const totalTooLarge = totalFileBytes > LARGE_TOTAL_BYTES;
  const filteredExams = useMemo(
    () => exams.filter((exam) => exam.name.toLowerCase().includes(searchTerm.toLowerCase().trim())),
    [exams, searchTerm],
  );
  const diagnostics = getClientDiagnostics();
  const estimatedParsingPage = parsePageCount > 0 ? Math.min(parsePageCount, Math.max(1, Math.floor(elapsedSeconds / 3) + 1)) : 0;

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

  const startParseProgress = () => {
    clearIntervals();
    elapsedIntervalRef.current = window.setInterval(() => setElapsedSeconds((prev) => prev + 1), 1000);
    parseProgressIntervalRef.current = window.setInterval(() => setParseProgress((prev) => (prev < 95 ? prev + 1 : prev)), 700);
  };

  const resetWizardProgress = () => {
    setParsedQuestionCount(null);
    setWizardError(null);
    setCreatedExamId(null);
    setStepLogs([]);
    setStep('creating');
    setParseProgress(0);
    setElapsedSeconds(0);
    setFailedSummary(null);
    setParsePageCount(0);
    setParsePageIndex(0);
    setParseTimings(null);
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
      setStep('creating');
      markChecklist('creating_exam', 'active');
      const exam = await api.createExam(modalName.trim(), requestOptions);
      examId = exam.id;
      setCreatedExamId(exam.id);
      logStep({ step: 'creating', endpointUrl: buildApiUrl('exams'), status: 200, responseSnippet: JSON.stringify(exam).slice(0, 500) });
      markChecklist('creating_exam', 'done');
      setParseProgress(14);

      setStep('uploading');
      markChecklist('uploading_key', 'active');
      const uploadResult = await api.uploadExamKey(exam.id, modalFiles, requestOptions);
      logStep({ step: 'uploading', endpointUrl: buildApiUrl(`exams/${exam.id}/key/upload`), status: 200, responseSnippet: JSON.stringify(uploadResult).slice(0, 500) });
      markChecklist('uploading_key', 'done');
      setParseProgress(28);

      setStep('building_pages');
      markChecklist('building_key_pages', 'active');
      const buildPages = await api.buildExamKeyPages(exam.id, requestOptions);
      logStep({ step: 'building_pages', endpointUrl: buildApiUrl(`exams/${exam.id}/key/build-pages`), status: 200, responseSnippet: JSON.stringify(buildPages).slice(0, 500) });
      markChecklist('building_key_pages', 'done');
      setParseProgress(42);
      setParsePageCount(buildPages.length);

      setStep('parsing');
      markChecklist('reading_questions', 'active');
      setParseProgress(50);
      startParseProgress();
      const parseOutcome = await api.parseExamKeyRaw(exam.id, requestOptions);
      logStep({ step: 'parsing', endpointUrl: parseOutcome.url, status: parseOutcome.status, responseSnippet: (parseOutcome.responseText || JSON.stringify(parseOutcome.data)).slice(0, 500) });

      if (!parseOutcome.data) {
        setWizardError({ summary: `Step: parsing | Status: ${parseOutcome.status}`, details: parseOutcome.responseText ?? '<empty>' });
        showError(`parsing failed (status ${parseOutcome.status})`);
        return;
      }

      const parseData = parseOutcome.data as ParseOutcomeData;
      if (typeof parseData.page_count === 'number') setParsePageCount(parseData.page_count);
      if (typeof parseData.page_index === 'number') setParsePageIndex(parseData.page_index);
      if (parseData.timings) setParseTimings(parseData.timings);

      markChecklist('reading_questions', 'done');
      markChecklist('detecting_marks', 'done');
      markChecklist('drafting_rubric', 'done');
      markChecklist('finalizing', 'done');
      setParseProgress(100);
      const questionCount = extractParsedQuestionCount(parseOutcome.data);
      setParsedQuestionCount(questionCount);
      setStep('done');
      localStorage.setItem(`supermarks:lastParse:${exam.id}`, JSON.stringify(parseOutcome.data));

      setIsModalOpen(false);
      resetWizardState();
      await loadExams();
      navigate(`/exams/${exam.id}/review`);
    } catch (err) {
      const stepName = step;
      const stepEndpoint =
        stepName === 'creating'
          ? buildApiUrl('exams')
          : stepName === 'uploading' && examId
            ? buildApiUrl(`exams/${examId}/key/upload`)
            : stepName === 'parsing' && examId
              ? buildApiUrl(`exams/${examId}/key/parse`)
              : buildApiUrl('unknown');

      if (isNetworkFetchError(err) || isAbortError(err)) {
        const details = err instanceof Error ? err.stack || err.message : String(err);
        setWizardError({ summary: `Step: ${stepName} | Status: network-error`, details, isAbort: isAbortError(err) });
        showError(`Network request failed. Step: ${stepName}. URL: ${stepEndpoint}`);
      } else if (err instanceof ApiError) {
        const details = err.responseBodySnippet || '<empty>';
        try {
          const parseDetails = JSON.parse(details) as ParseErrorDetails;
          if (stepName === 'parsing') {
            markChecklist(stageToChecklistId(parseDetails.stage), 'failed');
            if (parseDetails.page_index && parseDetails.page_count) {
              setFailedSummary(`Failed at: ${parseDetails.stage || 'unknown'} (page ${parseDetails.page_index}/${parseDetails.page_count})`);
            }
          }
        } catch {
          // no-op
        }
        setWizardError({ summary: `Step: ${stepName} | Status: ${err.status}`, details });
        showError(`${stepName} failed (status ${err.status})`);
      } else {
        setWizardError({ summary: `Step: ${stepName} | Status: unknown`, details: err instanceof Error ? err.message : 'Unknown error' });
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

  return (
    <div className="stack">
      <h1>Exams</h1>

      <div className="grid-2 top-cards">
        <section className="card">
          <h2>Enter Exam Key</h2>
          <p className="subtle-text">Create an exam and parse answer keys in one guided flow.</p>
          <button ref={openWizardButtonRef} type="button" className="btn btn-primary" onClick={() => setIsModalOpen(true)}>
            Enter Exam Key
          </button>
          {parsedQuestionCount !== null && <p className="subtle-text">Last parse detected {parsedQuestionCount} questions.</p>}
        </section>

        <section className="card">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setDiagnosticsOpen((prev) => !prev)}
            aria-expanded={diagnosticsOpen}
            aria-controls="diagnostics-panel"
          >
            API diagnostics
          </button>
          {diagnosticsOpen && (
            <div id="diagnostics-panel" className="stack" style={{ marginTop: '0.75rem' }}>
              <p className="subtle-text">Configured API base: {maskApiBaseUrl(API_BASE_URL)}</p>
              <button type="button" className="btn btn-secondary" onClick={onPingApi} disabled={isPingingApi}>
                {isPingingApi ? 'Pinging...' : 'Ping API'}
              </button>
              {pingResult && <pre className="code-box">{pingResult}</pre>}
            </div>
          )}
        </section>
      </div>

      <section className="card">
        <h2>Exam List</h2>
        <label htmlFor="exam-search">Search exams</label>
        <input
          id="exam-search"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder="Type exam name..."
        />

        {loading && (
          <ul className="stack" aria-label="Loading exams">
            {[1, 2, 3].map((item) => (
              <li key={item} className="skeleton skeleton-row" />
            ))}
          </ul>
        )}

        {!loading && filteredExams.length === 0 && (
          <p className="subtle-text">No exams match your search yet. Add an exam key above to get started.</p>
        )}

        {!loading && filteredExams.length > 0 && (
          <ul className="stack">
            {filteredExams.map((exam) => (
              <li key={exam.id}>
                <Link to={`/exams/${exam.id}`}>{exam.name}</Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {isModalOpen && (
        <Modal title="Enter Exam Key" onClose={closeModal} initialFocusRef={examNameRef}>
          <h2>Enter Exam Key</h2>
          <p className="subtle-text wizard-step-banner">Current step: {isRunning ? step : 'ready'}</p>
          <form onSubmit={onCreateAndUpload} className="stack" encType="multipart/form-data">
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

            <label htmlFor="exam-key-files">Key files (PDF or image)</label>
            <FileUploader
              files={modalFiles}
              disabled={isRunning}
              maxBytesPerFile={LARGE_FILE_BYTES}
              onChange={(files) => {
                setModalFiles(files);
                setAllowLargeUpload(false);
              }}
              onReject={(message) => showError(message)}
            />

            <p className="subtle-text">Total: {formatMb(totalFileBytes)}</p>
            {totalTooLarge && (
              <div className="warning-strong">
                <p>Total selection exceeds 12 MB and may fail in serverless environments.</p>
                <label htmlFor="allow-large-upload">
                  <input
                    id="allow-large-upload"
                    type="checkbox"
                    checked={allowLargeUpload}
                    onChange={(event) => setAllowLargeUpload(event.target.checked)}
                    disabled={isRunning}
                  />
                  {' '}I understand and want to continue anyway.
                </label>
              </div>
            )}

            <div className="wizard-progress-block">
              <div className="wizard-progress-header subtle-text">
                <span>Progress: {parseProgress}%</span>
                <span>Elapsed: {formatElapsed(elapsedSeconds)}</span>
              </div>
              {step === 'parsing' && parsePageCount > 0 && (
                <p className="subtle-text">
                  Parsing page {parsePageIndex || estimatedParsingPage}/{parsePageCount}…
                </p>
              )}
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
            {failedSummary && <p className="warning-text">{failedSummary}</p>}
            {parseTimings && <pre className="code-box">{Object.entries(parseTimings).map(([k, v]) => `${k}: ${v}ms`).join('\n')}</pre>}
            {wizardError && <DebugPanel summary={wizardError.summary} details={wizardError.details} />}

            <details>
              <summary>Show details</summary>
              <div className="subtle-text stack">
                <p>API base URL: {diagnostics.apiBaseUrl}</p>
                <p>hasApiKey: {String(diagnostics.hasApiKey)}</p>
                <p>buildId: {diagnostics.buildId}</p>
                <p>Computed create endpoint: {buildApiUrl('exams')}</p>
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
              <button type="submit" className="btn btn-primary" disabled={isRunning || (totalTooLarge && !allowLargeUpload)}>
                {isRunning ? 'Working...' : 'Enter exam & parse'}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  if (isRunning) {
                    requestControllerRef.current?.abort();
                    return;
                  }
                  closeModal();
                }}
              >
                {isRunning ? 'Cancel request' : 'Close'}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
