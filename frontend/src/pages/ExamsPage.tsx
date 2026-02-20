import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { API_BASE_URL, ApiError, api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { ExamRead } from '../types/api';

type WizardStep = 'creating' | 'uploading' | 'parsing' | 'done';

type StepLog = {
  step: WizardStep;
  endpointUrl: string;
  status: number | 'network-error';
  responseSnippet: string;
  exceptionMessage?: string;
};

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
  const [error, setError] = useState('');
  const [createdExamId, setCreatedExamId] = useState<number | null>(null);
  const [parsedQuestionCount, setParsedQuestionCount] = useState<number | null>(null);
  const [stepLogs, setStepLogs] = useState<StepLog[]>([]);
  const [allowLargeUpload, setAllowLargeUpload] = useState(false);
  const { showError, showSuccess, showWarning } = useToast();
  const navigate = useNavigate();

  const totalFileBytes = useMemo(() => modalFiles.reduce((sum, file) => sum + file.size, 0), [modalFiles]);
  const hasSingleLargeFile = useMemo(() => modalFiles.some((file) => file.size > LARGE_FILE_BYTES), [modalFiles]);
  const totalTooLarge = totalFileBytes > LARGE_TOTAL_BYTES;

  const loadExams = async () => {
    try {
      setLoading(true);
      setExams(await api.getExams());
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

  const closeModal = () => {
    if (isRunning) {
      return;
    }
    setIsModalOpen(false);
    setModalName('');
    setModalFiles([]);
    setParsedQuestionCount(null);
    setError('');
    setCreatedExamId(null);
    setStepLogs([]);
    setAllowLargeUpload(false);
    setStep('creating');
  };

  const logStep = (entry: StepLog) => {
    setStepLogs((prev) => [...prev, entry]);
  };

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

    setError('');
    setParsedQuestionCount(null);
    setCreatedExamId(null);
    setStepLogs([]);

    let examId: number | null = null;

    try {
      setIsRunning(true);

      setStep('creating');
      const createEndpoint = `${API_BASE_URL}/exams`;
      const exam = await api.createExam(modalName.trim());
      examId = exam.id;
      setCreatedExamId(exam.id);
      logStep({
        step: 'creating',
        endpointUrl: createEndpoint,
        status: 200,
        responseSnippet: JSON.stringify(exam).slice(0, 500),
      });
      showSuccess('Create step succeeded.');

      setStep('uploading');
      const uploadEndpoint = `${API_BASE_URL}/exams/${exam.id}/key/upload`;
      const uploadResult = await api.uploadExamKey(exam.id, modalFiles);
      logStep({
        step: 'uploading',
        endpointUrl: uploadEndpoint,
        status: 200,
        responseSnippet: JSON.stringify(uploadResult).slice(0, 500),
      });
      showSuccess('Upload step succeeded.');

      setStep('parsing');
      const parseOutcome = await api.parseExamKeyRaw(exam.id);
      const parseSnippet = (parseOutcome.responseText || JSON.stringify(parseOutcome.data)).slice(0, 500);
      logStep({
        step: 'parsing',
        endpointUrl: parseOutcome.url,
        status: parseOutcome.status,
        responseSnippet: parseSnippet,
      });

      if (parseOutcome.data === null) {
        const parseError = `Parse step failed. Endpoint: ${parseOutcome.url}. Status: ${parseOutcome.status}. Response: ${parseSnippet || '<empty>'}`;
        setError(parseError);
        showError(`parsing failed (status ${parseOutcome.status})`);
        return;
      }

      showSuccess('Parse step succeeded.');
      const questionCount = extractParsedQuestionCount(parseOutcome.data);
      setParsedQuestionCount(questionCount);
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
      console.error('Create exam wizard failed', err);
      const stepName = step;
      const stepEndpoint =
        stepName === 'creating'
          ? `${API_BASE_URL}/exams`
          : stepName === 'uploading' && examId
            ? `${API_BASE_URL}/exams/${examId}/key/upload`
            : stepName === 'parsing' && examId
              ? `${API_BASE_URL}/exams/${examId}/key/parse`
              : `${API_BASE_URL}/unknown`;

      if (isNetworkFetchError(err)) {
        const networkMessage = `Network request failed (browser blocked/aborted). Step: ${stepName}. URL: ${stepEndpoint}`;
        setError(networkMessage);
        logStep({
          step: stepName,
          endpointUrl: stepEndpoint,
          status: 'network-error',
          responseSnippet: '',
          exceptionMessage: err instanceof Error ? err.stack || err.message : String(err),
        });
        showError(networkMessage);
      } else if (err instanceof ApiError) {
        const details = `${err.method} ${err.url}\nStatus: ${err.status}\nBody: ${err.responseBodySnippet || '<empty>'}`;
        setError(details);
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
        setError(details);
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
      setIsRunning(false);
    }
  };

  const wizardSteps: Array<{ id: WizardStep; label: string }> = [
    { id: 'creating', label: 'Creating exam' },
    { id: 'uploading', label: 'Uploading key files' },
    { id: 'parsing', label: 'Parsing key files' },
    { id: 'done', label: `Done (${parsedQuestionCount ?? 0} questions)` },
  ];

  const activeStepIndex = wizardSteps.findIndex((wizardStep) => wizardStep.id === step);

  return (
    <div>
      <h1>Exams</h1>
      <div className="card actions-row">
        <button type="button" onClick={() => setIsModalOpen(true)} disabled={isRunning}>Enter Exam Key</button>
      </div>

      {isModalOpen && (
        <div className="modal-backdrop">
          <div className="card modal stack">
            <h2>Create Exam Wizard</h2>
            <p className="subtle-text wizard-step-banner">Current step: {isRunning ? step : 'ready'}</p>
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

              <ul className="subtle-text">
                {wizardSteps.map((wizardStep, index) => {
                  const isActive = step === wizardStep.id;
                  const isComplete = activeStepIndex > index;
                  const marker = isComplete ? '✓' : isActive ? '…' : '○';
                  return <li key={wizardStep.id}>{marker} {wizardStep.label}</li>;
                })}
              </ul>

              {createdExamId && <p className="subtle-text">Exam ID: {createdExamId}</p>}
              {error && <p className="subtle-text">{error}</p>}

              <details>
                <summary>Show details</summary>
                <div className="subtle-text stack">
                  <p>API base URL: {API_BASE_URL}</p>
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
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
