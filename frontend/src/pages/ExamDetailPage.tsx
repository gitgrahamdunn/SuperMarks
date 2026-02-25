import { FormEvent, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { ExamDetail } from '../types/api';

export function ExamDetailPage() {
  const params = useParams();
  const examId = Number(params.examId);
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [questionLabel, setQuestionLabel] = useState('');
  const [maxMarks, setMaxMarks] = useState(5);
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

  const onAddQuestion = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api.addQuestion(examId, questionLabel, maxMarks);
      showSuccess('Question added');
      setQuestionLabel('');
      await loadDetail();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to add question');
    }
  };

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
          <ul>
            {detail.questions.map((question) => (
              <li key={question.id}>{question.label} (max: {question.max_marks})</li>
            ))}
          </ul>
          <form onSubmit={onAddQuestion} className="stack">
            <label htmlFor="question-label" className="stack">
              <span>Question label</span>
              <input
                id="question-label"
                value={questionLabel}
                onChange={(e) => setQuestionLabel(e.target.value)}
                required
              />
            </label>
            <label htmlFor="question-max-marks" className="stack">
              <span>Maximum marks</span>
              <input
                id="question-max-marks"
                type="number"
                value={maxMarks}
                min={0}
                onChange={(e) => setMaxMarks(Number(e.target.value))}
                required
              />
            </label>
            <button type="submit" className="button-primary">Add Question</button>
          </form>
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
            <label htmlFor="student-name" className="stack">
              <span>Student name</span>
              <input
                id="student-name"
                value={studentName}
                onChange={(e) => setStudentName(e.target.value)}
                required
              />
            </label>
            <label htmlFor="submission-files" className="stack">
              <span>Submission files</span>
              <input
                id="submission-files"
                type="file"
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
                multiple
                required
              />
            </label>
            <button type="submit" className="button-primary">Upload Submission</button>
          </form>
        </section>
      </div>
    </div>
  );
}
