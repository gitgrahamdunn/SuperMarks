import { FormEvent, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { ExamRead } from '../types/api';

export function ExamsPage() {
  const [exams, setExams] = useState<ExamRead[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const { showError, showSuccess } = useToast();

  const loadExams = async () => {
    try {
      setLoading(true);
      setExams(await api.getExams());
    } catch (error) {
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
      await api.createExam(name.trim());
      setName('');
      showSuccess('Exam created');
      await loadExams();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to create exam');
    }
  };

  return (
    <div>
      <h1>Exams</h1>
      <form onSubmit={onCreate} className="card inline-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Exam name" required />
        <button type="submit">Create Exam</button>
      </form>

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
