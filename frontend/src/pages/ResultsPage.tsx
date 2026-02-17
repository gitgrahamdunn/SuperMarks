import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { QuestionRead, SubmissionResults } from '../types/api';

export function ResultsPage() {
  const params = useParams();
  const submissionId = Number(params.submissionId);
  const [searchParams] = useSearchParams();
  const examId = Number(searchParams.get('examId'));

  const [results, setResults] = useState<SubmissionResults | null>(null);
  const [questions, setQuestions] = useState<QuestionRead[]>([]);
  const { showError } = useToast();

  useEffect(() => {
    const load = async () => {
      try {
        const [resultData, questionData] = await Promise.all([
          api.getResults(submissionId),
          api.listQuestions(examId),
        ]);
        setResults(resultData);
        setQuestions(questionData);
      } catch (error) {
        showError(error instanceof Error ? error.message : 'Failed to load results');
      }
    };
    if (submissionId && examId) {
      void load();
    }
  }, [submissionId, examId]);

  const rows = useMemo(() => {
    if (!results) return [];
    return questions.map((question) => ({
      question,
      transcription: results.transcriptions.find((item) => item.question_id === question.id),
      grade: results.grades.find((item) => item.question_id === question.id),
    }));
  }, [results, questions]);

  return (
    <div>
      <p><Link to={`/submissions/${submissionId}`}>← Back to Submission</Link></p>
      <h1>Results</h1>
      {!results && <p>Loading...</p>}
      {rows.map((row) => (
        <div className="card" key={row.question.id}>
          <h2>{row.question.label}</h2>
          <img
            src={api.getCropImageUrl(submissionId, row.question.id)}
            alt={`Crop ${row.question.label}`}
            className="result-crop"
          />
          <p><strong>Transcription:</strong> {row.transcription?.text ?? 'N/A'}</p>
          <p><strong>Marks:</strong> {row.grade?.marks_awarded ?? 'N/A'} / {row.question.max_marks}</p>
          <p><strong>Breakdown:</strong></p>
          <pre>{JSON.stringify(row.grade?.breakdown_json ?? {}, null, 2)}</pre>
          <p><strong>Feedback:</strong></p>
          <pre>{JSON.stringify(row.grade?.feedback_json ?? {}, null, 2)}</pre>
        </div>
      ))}
    </div>
  );
}
