import { FormEvent, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { ExamRead } from '../types/api';

type WizardStep = 'idle' | 'creating' | 'uploading' | 'parsing' | 'done';

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

function extractParsedQuestionCount(parseResult: Record<string, unknown>): number {
  const maybeQuestions = parseResult.questions;
  if (Array.isArray(maybeQuestions)) {
    return maybeQuestions.length;
  }

  const maybeCount = parseResult.question_count;
  if (typeof maybeCount === 'number') {
    return maybeCount;
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
  const [createStep, setCreateStep] = useState<WizardStep>('idle');
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
    if (createStep !== 'idle') {
      return;
    }
    setIsModalOpen(false);
    setModalName('');
    setModalFiles([]);
    setParsedQuestionCount(null);
  };

  const onCreateAndUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Exam name and at least one key file are required.');
      return;
    }

    setParsedQuestionCount(null);

    let examId: number | null = null;
    setCreateStep('creating');
    try {
      console.log('[wizard] create exam -> start');
      const exam = await api.createExam(modalName.trim());
      examId = exam.id;
      console.log(`[wizard] create exam -> exam_id=${exam.id}`);
      showSuccess('Exam created. Uploading key files...');
    } catch (error) {
      console.error('Failed at create exam step', error);
      showError(`Create exam failed:\n${getErrorDetails(error)}`);
      setCreateStep('idle');
      return;
    }

    if (examId === null) {
      setCreateStep('idle');
      return;
    }

    setCreateStep('uploading');
    try {
      console.log('[wizard] upload key -> start');
      await api.uploadExamKey(examId, modalFiles);
      showSuccess('Key uploaded. Parsing key...');
    } catch (error) {
      console.error('Failed at upload key step', error);
      showError(`Upload key failed:\n${getErrorDetails(error)}`);
      setCreateStep('idle');
      return;
    }

    let questionCount = 0;
    setCreateStep('parsing');
    try {
      console.log('[wizard] parse key -> start');
      const parseResult = await api.parseExamKey(examId);
      questionCount = extractParsedQuestionCount(parseResult);
      console.log(`[wizard] parse key -> parsed_questions=${questionCount}`);
    } catch (error) {
      console.error('Failed at parse key step', error);
      showError(`Parse key failed:\n${getErrorDetails(error)}`);
      setCreateStep('idle');
      return;
    }

    setParsedQuestionCount(questionCount);
    setCreateStep('done');
    if (questionCount === 0) {
      showWarning('Parse completed but returned 0 questions. Opening review anyway.');
    } else {
      showSuccess(`Key parsed successfully (${questionCount} questions). Opening review wizard...`);
    }

    setModalName('');
    setModalFiles([]);
    setIsModalOpen(false);
    setCreateStep('idle');
    await loadExams();
    navigate(`/exams/${examId}/review`);
  };

  const creatingWithKey = createStep !== 'idle';
  const wizardSteps: Array<{ id: Exclude<WizardStep, 'idle'>; label: string }> = [
    { id: 'creating', label: 'Creating exam...' },
    { id: 'uploading', label: 'Uploading key...' },
    { id: 'parsing', label: 'Parsing key...' },
    { id: 'done', label: `Done (${parsedQuestionCount ?? 0} questions)` },
  ];

  const activeStepIndex = wizardSteps.findIndex((step) => step.id === createStep);

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
                  disabled={creatingWithKey}
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
                  disabled={creatingWithKey}
                />
              </label>

              {createStep !== 'idle' && (
                <ul className="subtle-text">
                  {wizardSteps.map((step, index) => {
                    const isActive = createStep === step.id;
                    const isComplete = activeStepIndex > index;
                    const marker = isComplete ? '✓' : isActive ? '…' : '○';
                    return <li key={step.id}>{marker} {step.label}</li>;
                  })}
                </ul>
              )}

              <div className="actions-row">
                <button type="submit" disabled={creatingWithKey}>{creatingWithKey ? 'Working...' : 'Enter exam & parse'}</button>
                <button type="button" onClick={closeModal} disabled={creatingWithKey}>Cancel</button>
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
