import { FormEvent, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { ExamRead } from '../types/api';

type WizardStep = 'creating' | 'uploading' | 'parsing' | 'done';

interface WizardParseResult {
  questions?: unknown;
  result?: {
    questions?: unknown;
  };
}

function getErrorDetails(error: unknown): string {
  if (error instanceof ApiError) {
    const body = error.responseBodySnippet ? `\nBody: ${error.responseBodySnippet}` : '\nBody: <empty>';
    return `${error.method} ${error.url}\nStatus: ${error.status}${body}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Unknown error';
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

export function ExamsPage() {
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalName, setModalName] = useState('');
  const [modalFiles, setModalFiles] = useState<File[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<WizardStep>('creating');
  const [error, setError] = useState('');
  const [createdExamId, setCreatedExamId] = useState<number | null>(null);
  const [parsedQuestionCount, setParsedQuestionCount] = useState<number | null>(null);
  const { showError, showSuccess, showWarning } = useToast();
  const navigate = useNavigate();

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

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      setCreating(true);
      await api.createExam(name.trim());
      setName('');
      showSuccess('Exam created');
      await loadExams();
    } catch (error) {
      console.error('Failed to create exam', error);
      showError(error instanceof Error ? error.message : 'Failed to create exam');
    } finally {
      setCreating(false);
    }
  };

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
  };

  const onCreateAndUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Exam name and at least one key file are required.');
      return;
    }

    setError('');
    setParsedQuestionCount(null);
    setCreatedExamId(null);

    try {
      setIsRunning(true);
      setStep('creating');
      console.log('[wizard] create exam -> start');
      const exam = await api.createExam(modalName.trim());
      const examId = exam.id;
      setCreatedExamId(examId);
      console.log('[wizard] create exam ->', { exam_id: exam.id });
      showSuccess('Exam created. Uploading key files...');

      setStep('uploading');
      console.log('[wizard] upload key -> start');
      const uploadResult = await api.uploadExamKey(examId, modalFiles);
      console.log('[wizard] upload key ->', uploadResult);
      showSuccess('Key uploaded. Parsing key...');

      setStep('parsing');
      console.log('[wizard] parse key -> start');
      const { data: parseResult, responseText } = await api.parseExamKeyRaw(examId);
      console.log('[wizard] parse key ->', parseResult ?? responseText);
      if (parseResult === null) {
        const responseSnippet = responseText.slice(0, 300);
        const parseError = `POST /exams/${examId}/key/parse\nStatus: 200\nBody: ${responseSnippet || '<empty>'}`;
        setError(parseError);
        showError(`Parse key failed:\n${parseError}`);
        return;
      }

      const questionCount = extractParsedQuestionCount(parseResult);
      console.log(`[wizard] parse key -> parsed_questions=${questionCount}`);

      setParsedQuestionCount(questionCount);
      setStep('done');
      localStorage.setItem(`supermarks:lastParse:${examId}`, JSON.stringify(parseResult));

      if (questionCount === 0) {
        showWarning('Parse completed but returned 0 questions. Opening review anyway.');
      } else {
        showSuccess(`Key parsed successfully (${questionCount} questions). Opening review wizard...`);
      }

      setModalName('');
      setModalFiles([]);
      setIsModalOpen(false);
      await loadExams();
      navigate(`/exams/${examId}/review`);
    } catch (err) {
      console.error('Create exam wizard failed', err);
      const details = getErrorDetails(err);
      setError(details);
      showError(`Create exam wizard failed:\n${details}`);
    } finally {
      setIsRunning(false);
    }
  };

  const wizardSteps: Array<{ id: WizardStep; label: string }> = [
    { id: 'creating', label: 'Creating exam...' },
    { id: 'uploading', label: 'Uploading key...' },
    { id: 'parsing', label: 'Parsing key...' },
    { id: 'done', label: `Done (${parsedQuestionCount ?? 0} questions)` },
  ];

  const activeStepIndex = wizardSteps.findIndex((wizardStep) => wizardStep.id === step);

  return (
    <div>
      <h1>Exams</h1>
      <form onSubmit={onCreate} className="card inline-form wrap-mobile">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Exam name" required />
        <button type="submit" disabled={creating}>{creating ? 'Creating...' : 'Create Exam'}</button>
        <button type="button" onClick={() => setIsModalOpen(true)} disabled={creating}>Create Exam Wizard</button>
      </form>

      {isModalOpen && (
        <div className="modal-backdrop">
          <div className="card modal stack">
            <h2>Create Exam Wizard</h2>
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
                  onChange={(e) => setModalFiles(Array.from(e.target.files || []))}
                  multiple
                  required
                  disabled={isRunning}
                />
              </label>

              {isRunning && (
                <ul className="subtle-text">
                  <li>Current step: {step}</li>
                  {wizardSteps.map((wizardStep, index) => {
                    const isActive = step === wizardStep.id;
                    const isComplete = activeStepIndex > index;
                    const marker = isComplete ? '✓' : isActive ? '…' : '○';
                    return <li key={wizardStep.id}>{marker} {wizardStep.label}</li>;
                  })}
                </ul>
              )}

              {createdExamId && <p className="subtle-text">Exam ID: {createdExamId}</p>}
              {error && <p className="subtle-text">{error}</p>}

              <div className="actions-row">
                <button type="submit" disabled={isRunning}>{isRunning ? 'Working...' : 'Enter exam & parse'}</button>
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
