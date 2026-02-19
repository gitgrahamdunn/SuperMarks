import { FormEvent, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { ExamRead } from '../types/api';

export function ExamsPage() {
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalName, setModalName] = useState('');
  const [modalFiles, setModalFiles] = useState<File[]>([]);
  const [createStep, setCreateStep] = useState<'idle' | 'creating' | 'uploading' | 'parsing'>('idle');
  const { showError, showSuccess } = useToast();
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
  };

  const onCreateAndUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Exam name and at least one key file are required.');
      return;
    }

    try {
      setCreateStep('creating');
      const exam = await api.createExam(modalName.trim());
      showSuccess('Exam created. Uploading key files...');

      setCreateStep('uploading');
      await api.uploadExamKey(exam.id, modalFiles);
      showSuccess('Key uploaded. Parsing key...');

      setCreateStep('parsing');
      await api.parseExamKey(exam.id);
      showSuccess('Key parsed successfully. Opening review wizard...');

      setModalName('');
      setModalFiles([]);
      setIsModalOpen(false);
      await loadExams();
      navigate(`/exams/${exam.id}/review`);
    } catch (error) {
      console.error('Failed create+upload+parse flow', error);
      showError(error instanceof Error ? error.message : 'Failed to create exam with key upload');
    } finally {
      setCreateStep('idle');
    }
  };

  const creatingWithKey = createStep !== 'idle';
  const createStepLabel = createStep === 'creating'
    ? 'Creating exam...'
    : createStep === 'uploading'
      ? 'Uploading key...'
      : createStep === 'parsing'
        ? 'Parsing key...'
        : '';

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

              {createStepLabel && <p className="subtle-text">{createStepLabel}</p>}

              <div className="actions-row">
                <button type="submit" disabled={creatingWithKey}>{creatingWithKey ? createStepLabel || 'Working...' : 'Create & Parse'}</button>
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
