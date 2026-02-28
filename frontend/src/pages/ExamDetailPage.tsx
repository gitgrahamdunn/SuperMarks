import { FormEvent, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { ExamDetail } from '../types/api';

export function ExamDetailPage() {
  const ENABLE_MANUAL_QUESTION_ADD = false;
  const params = useParams();
  const examId = Number(params.examId);
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [studentName, setStudentName] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const { showError, showSuccess } = useToast();

  const loadDetail = async () => {
    try {
      setDetail(await api.getExamDetail(examId));
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to load exam');
    }
  };

  useEffect(() => {
    if (examId) {
      void loadDetail();
    }
  }, [examId]);

  const onUploadSubmission = async (event: FormEvent) => {
    event.preventDefault();
    if (files.length === 0) return;
    try {
      await api.uploadSubmission(examId, studentName, files);
      showSuccess('Submission uploaded');
      setStudentName('');
      setFiles([]);
      await loadDetail();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to upload submission');
    }
  };

  if (!detail) return <p>Loading...</p>;

  return (
    <div>
      <p><Link to="/">← Back to Exams</Link></p>
      <h1>{detail.exam.name}</h1>

      <div className="grid-2">
        <section className="card">
          <h2>Questions</h2>
          <p className="subtle-text">Questions are generated from the exam key.</p>
          <ul>
            {detail.questions.map((question) => (
              <li key={question.id}>{question.label} (max: {question.max_marks})</li>
            ))}
          </ul>
          {ENABLE_MANUAL_QUESTION_ADD && null}
        </section>

        <section className="card">
          <h2>Submissions</h2>
          <ul>
            {detail.submissions.map((submission) => (
              <li key={submission.id}>
                <Link to={`/submissions/${submission.id}`}>{submission.student_name}</Link> ({submission.status})
              </li>
            ))}
          </ul>

          <form onSubmit={onUploadSubmission} className="stack" encType="multipart/form-data">
            <label htmlFor="student-name">Student name</label>
            <input
              id="student-name"
              value={studentName}
              onChange={(e) => setStudentName(e.target.value)}
              placeholder="Student name"
              required
            />
            <label htmlFor="submission-files">Submission files</label>
            <input
              id="submission-files"
              type="file"
              onChange={(e) => setFiles(Array.from(e.target.files || []))}
              multiple
              required
            />
            <button type="submit" className="btn btn-primary">Upload Submission</button>
          </form>
        </section>
      </div>
    </div>
  );
}
