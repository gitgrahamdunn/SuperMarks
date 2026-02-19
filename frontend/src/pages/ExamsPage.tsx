import { FormEvent, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
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
  const [creatingWithKey, setCreatingWithKey] = useState(false);
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

  const onCreateAndUpload = async (event: FormEvent) => {
    event.preventDefault();
    if (!modalName.trim() || modalFiles.length === 0) {
      showError('Exam name and key file are required');
      return;
    }

    try {
      setCreatingWithKey(true);
      const exam = await api.createExam(modalName.trim());

      try {
        await api.uploadExamKey(exam.id, modalFiles);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          showError('Key upload endpoint not available. Attempting parse anyway.');
        } else {
          throw error;
        }
      }

      const parseResult = await api.parseExamKey(exam.id);
      localStorage.setItem(`exam-review-${exam.id}`, JSON.stringify(parseResult));
      showSuccess('Exam key parsed. Review questions next.');
      setModalName('');
      setModalFiles([]);
      setIsModalOpen(false);
      await loadExams();
      navigate(`/exams/${exam.id}/review`, { state: { parseResult } });
    } catch (error) {
      console.error('Failed create+upload+parse flow', error);
      showError(error instanceof Error ? error.message : 'Failed to create exam with key upload');
    } finally {
      setCreatingWithKey(false);
    }
  };

  return (
    <div>
      <h1>Exams</h1>
      <form onSubmit={onCreate} className="card inline-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Exam name" required />
        <button type="submit" disabled={creating}>{creating ? 'Creating...' : 'Create Exam'}</button>
        <button type="button" onClick={() => setIsModalOpen(true)}>Create Exam + Upload Key</button>
      </form>

      {isModalOpen && (
        <div className="modal-backdrop">
          <div className="card modal">
            <h2>Create Exam + Upload Key</h2>
            <form onSubmit={onCreateAndUpload} className="stack" encType="multipart/form-data">
              <input
                value={modalName}
                onChange={(e) => setModalName(e.target.value)}
                placeholder="Exam name"
                required
              />
              <input
                type="file"
                accept="application/pdf,image/png,image/jpeg,image/jpg"
                onChange={(e) => setModalFiles(Array.from(e.target.files || []))}
                multiple
                required
              />
              <div className="actions-row">
                <button type="submit" disabled={creatingWithKey}>{creatingWithKey ? 'Processing...' : 'Create + Parse'}</button>
                <button type="button" onClick={() => setIsModalOpen(false)} disabled={creatingWithKey}>Cancel</button>
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
