import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { API_BASE_URL, ApiError, api, buildApiUrl, getHealthPingUrl, maskApiBaseUrl, pingApiHealth } from '../api/client';
import { DebugPanel } from '../components/DebugPanel';
import { FileUploader } from '../components/FileUploader';
import { Modal } from '../components/Modal';
import { SkeletonCard } from '../components/SkeletonCard';
import { useToast } from '../components/ToastProvider';
import type { ExamRead } from '../types/api';

type WizardStep = 'creating' | 'uploading' | 'building_pages' | 'parsing' | 'done';
type StepLog = { step: WizardStep; endpointUrl: string; status: number | 'network-error'; responseSnippet: string; exceptionMessage?: string };
type WizardError = { summary: string; details: unknown; isAbort?: boolean };
type ParseErrorDetails = { stage?: string; page_index?: number; page_count?: number };
type ParseChecklistStepId = 'creating_exam' | 'uploading_key' | 'building_key_pages' | 'reading_questions' | 'detecting_marks' | 'drafting_rubric' | 'finalizing';
type ParseChecklistStatus = 'pending' | 'active' | 'done' | 'failed';
type ParseChecklistStep = { id: ParseChecklistStepId; label: string; status: ParseChecklistStatus };

const CHECKLIST_ORDER: Array<{ id: ParseChecklistStepId; label: string }> = [
  { id: 'creating_exam', label: 'Creating exam' },
  { id: 'uploading_key', label: 'Uploading key' },
  { id: 'building_key_pages', label: 'Building key pages' },
  { id: 'reading_questions', label: 'Reading questions' },
  { id: 'detecting_marks', label: 'Detecting marks' },
  { id: 'drafting_rubric', label: 'Drafting rubric' },
  { id: 'finalizing', label: 'Finalizing' },
];

const MB = 1024 * 1024;
const LARGE_FILE_BYTES = 8 * MB;
const LARGE_TOTAL_BYTES = 12 * MB;
const SUPPORTED_TYPES = new Set(['application/pdf', 'image/png', 'image/jpeg', 'image/jpg']);

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

function formatMb(bytes: number): string {
  return `${(bytes / MB).toFixed(2)} MB`;
}

function extractParsedQuestionCount(parseResult: unknown): number {
  if (Array.isArray(parseResult)) return parseResult.length;
  if (typeof parseResult !== 'object' || !parseResult) return 0;
  const shaped = parseResult as { questions?: unknown; result?: { questions?: unknown }; question_count?: unknown };
  if (Array.isArray(shaped.questions)) return shaped.questions.length;
  if (Array.isArray(shaped.result?.questions)) return shaped.result.questions.length;
  if (typeof shaped.question_count === 'number') return shaped.question_count;
  return 0;
}

function isNetworkFetchError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return error instanceof TypeError || /load failed|failed to fetch|network/i.test(error.message);
}

function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === 'AbortError') return true;
  if (error instanceof Error) return error.name === 'AbortError' || /aborted/i.test(error.message);
  return false;
}

export function ExamsPage() {
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalName, setModalName] = useState('');
  const [modalFiles, setModalFiles] = useState<File[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<WizardStep>('creating');
  const [wizardError, setWizardError] = useState<WizardError | null>(null);
  const [createdExamId, setCreatedExamId] = useState<number | null>(null);
  const [stepLogs, setStepLogs] = useState<StepLog[]>([]);
  const [pingResult, setPingResult] = useState('');
  const [isPingingApi, setIsPingingApi] = useState(false);
  const [allowLargeUpload, setAllowLargeUpload] = useState(false);
  const [parseProgress, setParseProgress] = useState(0);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [failedSummary, setFailedSummary] = useState<string | null>(null);
  const [checklistSteps, setChecklistSteps] = useState<ParseChecklistStep[]>(() => initChecklist());
  const parseProgressIntervalRef = useRef<number | null>(null);
  const elapsedIntervalRef = useRef<number | null>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const triggerButtonRef = useRef<HTMLButtonElement | null>(null);
  const examNameInputRef = useRef<HTMLInputElement | null>(null);
  const { showError, showSuccess, showWarning } = useToast();
  const navigate = useNavigate();

  const totalFileBytes = useMemo(() => modalFiles.reduce((sum, file) => sum + file.size, 0), [modalFiles]);
  const hasSingleLargeFile = useMemo(() => modalFiles.some((file) => file.size > LARGE_FILE_BYTES), [modalFiles]);
  const totalTooLarge = totalFileBytes > LARGE_TOTAL_BYTES;

  const filteredExams = useMemo(
    () => exams.filter((exam) => exam.name.toLowerCase().includes(searchTerm.toLowerCase().trim())),
    [exams, searchTerm],
  );

  useEffect(() => {
    if (isModalOpen) {
      window.setTimeout(() => examNameInputRef.current?.focus(), 0);
    }
  }, [isModalOpen]);

  const loadExams = async () => {
    try {
      setLoading(true);
      setExams(await api.getExams());
    } catch (error) {
      console.error('Failed to load exams', error);
      showError(error instanceof Error ? error.message : 'Failed to load exams');
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
    parseProgressIntervalRef.current = window.setInterval(() => setParseProgress((prev) => (prev < 92 ? prev + 2 : prev)), 2000);
  };

  const resetWizardState = () => {
    setModalName('');
    setModalFiles([]);
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

  const closeModal = () => {
    if (isRunning) return;
    setIsModalOpen(false);
    resetWizardState();
    triggerButtonRef.current?.focus();
  };

  const validateAndMergeFiles = (incoming: File[]) => {
    if (incoming.length === 0) return;

    const rejectedType = incoming.filter((file) => !SUPPORTED_TYPES.has(file.type));
    const rejectedSize = incoming.filter((file) => file.size > LARGE_FILE_BYTES);

    if (rejectedType.length > 0) {
      showError(`Unsupported file type: ${rejectedType.map((file) => file.name).join(', ')}. Please upload PDF, PNG, or JPG files.`);
    }

    if (rejectedSize.length > 0) {
      showError(`These files are over 8 MB and were not added: ${rejectedSize.map((file) => file.name).join(', ')}.`);
    }

    const valid = incoming.filter((file) => SUPPORTED_TYPES.has(file.type) && file.size <= LARGE_FILE_BYTES);
    if (valid.length === 0) return;

    setModalFiles((prev) => {
      const deduped = [...prev, ...valid].filter(
        (file, index, arr) => arr.findIndex((candidate) => `${candidate.name}-${candidate.size}-${candidate.lastModified}` === `${file.name}-${file.size}-${file.lastModified}`) === index,
      );
      const totalBytes = deduped.reduce((sum, file) => sum + file.size, 0);
      if (totalBytes > LARGE_TOTAL_BYTES) {
        showWarning('Total selected file size exceeds 12 MB. Upload may fail in serverless environments.');
      }
      setAllowLargeUpload(false);
      return deduped;
    });
  };

  const logStep = (entry: StepLog) => setStepLogs((prev) => [...prev, entry]);

  const runCreateAndUpload = async () => {
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Please provide an exam name and at least one key file.');
      return;
    }

    if (totalTooLarge && !allowLargeUpload) {
      showWarning('Total files exceed 12 MB. Confirm to continue.');
      return;
    }

    resetWizardState();
    setModalName(modalName);
    setModalFiles(modalFiles);

    let examId: number | null = null;
    const controller = new AbortController();
    requestControllerRef.current = controller;
    const requestOptions = { signal: controller.signal };

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

      setStep('parsing');
      markChecklist('reading_questions', 'active');
      setParseProgress(50);
      startParseProgress();
      const parseOutcome = await api.parseExamKeyRaw(exam.id, requestOptions);
      logStep({ step: 'parsing', endpointUrl: parseOutcome.url, status: parseOutcome.status, responseSnippet: (parseOutcome.responseText || JSON.stringify(parseOutcome.data)).slice(0, 500) });

      if (parseOutcome.data === null) {
        clearIntervals();
        setWizardError({ summary: `Step: parsing | Status: ${parseOutcome.status}`, details: parseOutcome.responseText || '<empty>' });
        showError(`Parsing failed (status ${parseOutcome.status})`);
        return;
      }

      clearIntervals();
      markChecklist('reading_questions', 'done');
      markChecklist('detecting_marks', 'done');
      markChecklist('drafting_rubric', 'done');
      markChecklist('finalizing', 'done');
      setParseProgress(100);
      showSuccess(`Exam ready with ${extractParsedQuestionCount(parseOutcome.data)} parsed question(s).`);
      setIsModalOpen(false);
      resetWizardState();
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
            : stepName === 'parsing' && examId
              ? buildApiUrl(`exams/${examId}/key/parse`)
              : buildApiUrl('unknown');

      if (isNetworkFetchError(err) || isAbortError(err)) {
        const details = err instanceof Error ? err.stack || err.message : String(err);
        setWizardError({ summary: `Step: ${stepName} | Status: network-error`, details, isAbort: isAbortError(err) });
        logStep({ step: stepName, endpointUrl: stepEndpoint, status: 'network-error', responseSnippet: '', exceptionMessage: details });
        showError(`Request failed during ${stepName}.`);
      } else if (err instanceof ApiError) {
        let parseDetails: ParseErrorDetails | null = null;
        try {
          parseDetails = JSON.parse(err.responseBodySnippet) as ParseErrorDetails;
        } catch {
          parseDetails = null;
        }

        if (stepName === 'parsing') {
          const failedStepId = stageToChecklistId(parseDetails?.stage);
          markChecklist(failedStepId, 'failed');
          setFailedSummary(parseDetails?.page_index && parseDetails?.page_count
            ? `Failed at ${parseDetails?.stage || 'unknown'} (page ${parseDetails.page_index}/${parseDetails.page_count})`
            : `Failed at ${parseDetails?.stage || 'unknown'} stage.`);
        }

        setWizardError({ summary: `Step: ${stepName} | Status: ${err.status}`, details: err.responseBodySnippet || '<empty>' });
        logStep({ step: stepName, endpointUrl: err.url, status: err.status, responseSnippet: err.responseBodySnippet || '<empty>', exceptionMessage: err.message });
        showError(`${stepName} failed (status ${err.status}).`);
      } else {
        const details = err instanceof Error ? err.stack || err.message : 'Unknown error';
        setWizardError({ summary: `Step: ${stepName} | Status: unknown`, details });
        showError(`Unexpected failure during ${stepName}.`);
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
      setPingResult('');
      const result = await pingApiHealth();
      setPingResult(`status=${result.status}\nbody=${result.body || '<empty>'}`);
    } catch (error) {
      setPingResult(`error=${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsPingingApi(false);
    }
  };

  return (
    <div className="stack-lg">
      <h1>Exams</h1>
      <div className="grid-2 top-cards">
        <section className="card stack">
          <h2>Enter Exam Key</h2>
          <p className="subtle-text">Create an exam and parse your answer key in one guided flow.</p>
          <button type="button" ref={triggerButtonRef} onClick={() => setIsModalOpen(true)} className="button-primary">Enter Exam Key</button>
        </section>

        <section className="card">
          <details>
            <summary>API diagnostics</summary>
            <div className="stack diagnostics-body">
              <p className="subtle-text">Configured API base: {maskApiBaseUrl(API_BASE_URL)}</p>
              <p className="subtle-text">Health URL: {getHealthPingUrl()}</p>
              <button type="button" onClick={onPingApi} disabled={isPingingApi} className="button-secondary">
                {isPingingApi ? 'Pinging…' : 'Ping API'}
              </button>
              {pingResult && <pre className="code-box">{pingResult}</pre>}
            </div>
          </details>
        </section>
      </div>

      <section className="card stack">
        <div className="card-heading-row">
          <h2>Exam list</h2>
          <label htmlFor="exam-search" className="stack">
            <span className="sr-only">Search exams</span>
            <input id="exam-search" type="search" placeholder="Search by exam name" value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} />
          </label>
        </div>
        {loading && (
          <div className="stack">
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </div>
        )}
        {!loading && filteredExams.length === 0 && (
          <p className="subtle-text">No exams match your search yet. Try a broader keyword or add a new exam key.</p>
        )}
        {!loading && filteredExams.length > 0 && (
          <ul className="plain-list">
            {filteredExams.map((exam) => (
              <li key={exam.id}>
                <Link to={`/exams/${exam.id}`}>{exam.name}</Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Modal title="Enter Exam Key" isOpen={isModalOpen} onClose={closeModal} labelledBy="enter-key-title">
        <h2 id="enter-key-title">Enter Exam Key</h2>
        <p className="subtle-text wizard-step-banner">Current step: {isRunning ? step : 'ready'}</p>
        <form onSubmit={onCreateAndUpload} className="stack" encType="multipart/form-data">
          <label htmlFor="exam-name" className="stack">
            <span>Exam name</span>
            <input id="exam-name" ref={examNameInputRef} value={modalName} onChange={(event) => setModalName(event.target.value)} required disabled={isRunning} />
          </label>

          <FileUploader
            files={modalFiles}
            onAddFiles={validateAndMergeFiles}
            onRemoveFile={(index) => setModalFiles((prev) => prev.filter((_, fileIndex) => fileIndex !== index))}
            disabled={isRunning}
          />

          {modalFiles.length > 0 && <p className="subtle-text">Total selected size: {formatMb(totalFileBytes)}</p>}
          {hasSingleLargeFile && <p className="warning-text">Some files are close to the upload limit. If upload fails, try smaller exports.</p>}
          {totalTooLarge && (
            <label htmlFor="large-upload-confirm" className="warning-strong">
              <input id="large-upload-confirm" type="checkbox" checked={allowLargeUpload} onChange={(event) => setAllowLargeUpload(event.target.checked)} disabled={isRunning} />
              I understand this exceeds 12 MB and may fail. Continue anyway.
            </label>
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
                return <li key={item.id} className={`wizard-checklist-item status-${item.status}`}><span>{marker}</span> {item.label}</li>;
              })}
            </ul>
          </div>

          {createdExamId && <p className="subtle-text">Exam ID: {createdExamId}</p>}
          {failedSummary && <p className="warning-text">{failedSummary}</p>}
          {wizardError && <DebugPanel summary={wizardError.summary} details={wizardError.details} />}

          <details>
            <summary>Show details</summary>
            <div className="subtle-text stack">
              <p>API base URL: {API_BASE_URL}</p>
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
            <button type="submit" className="button-primary" disabled={isRunning || (totalTooLarge && !allowLargeUpload)}>{isRunning ? 'Working…' : 'Enter Exam Key'}</button>
            <button type="button" className="button-secondary" onClick={() => (isRunning ? requestControllerRef.current?.abort() : closeModal())}>
              {isRunning ? 'Cancel run' : 'Close'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
