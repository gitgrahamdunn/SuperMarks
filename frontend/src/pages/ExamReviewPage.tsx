import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { QuestionRead } from '../types/api';

interface EditableQuestion {
  id: number;
  label: string;
  max_marks: number;
  criteria: string[];
  answer_key: string;
  model_solution: string;
  rubric_json: Record<string, unknown>;
}

export function ExamReviewPage() {
  const { examId: examIdParam } = useParams();
  const examId = Number(examIdParam);
  const [questions, setQuestions] = useState<EditableQuestion[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveAvailable, setSaveAvailable] = useState(true);
  const { showError, showSuccess } = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const loadQuestions = async () => {
      if (!examId) return;
      try {
        setLoading(true);
        const fetchedQuestions = await api.listQuestions(examId);
        const mapped = fetchedQuestions.map(mapQuestion);
        setQuestions(mapped);
        localStorage.setItem(`exam-review-${examId}-questions`, JSON.stringify(mapped));
      } catch (error) {
        console.error('Failed to fetch questions for review', error);
        const stateData = (location.state as { parseResult?: Record<string, unknown> } | null)?.parseResult;
        const localData = localStorage.getItem(`exam-review-${examId}-questions`) || localStorage.getItem(`exam-review-${examId}`);

        if (localData || stateData) {
          const parseSource = stateData || JSON.parse(localData || '{}');
          const fallbackQuestions = parseQuestionsFromSource(parseSource).map((q, index) => ({
            ...q,
            id: -(index + 1),
            rubric_json: {
              criteria: q.criteria,
              answer_key: q.answer_key,
              model_solution: q.model_solution,
            },
          }));
          setQuestions(fallbackQuestions);
          showError('Loaded review data from local fallback.');
        } else {
          showError(error instanceof Error ? error.message : 'Failed to load review questions');
        }
      } finally {
        setLoading(false);
      }
    };

    void loadQuestions();
  }, [examId, location.state, showError]);

  const currentQuestion = questions[currentIndex];

  const onFieldChange = (field: keyof EditableQuestion, value: string | number | string[]) => {
    setQuestions((prev) => prev.map((question, index) => (index === currentIndex
      ? {
          ...question,
          [field]: value,
          rubric_json: {
            ...question.rubric_json,
            criteria: field === 'criteria' ? value : question.criteria,
            answer_key: field === 'answer_key' ? value : question.answer_key,
            model_solution: field === 'model_solution' ? value : question.model_solution,
          },
        }
      : question)));
  };

  const onSave = async () => {
    if (!currentQuestion) return;
    try {
      setSaving(true);
      await api.updateQuestion(examId, currentQuestion.id, {
        label: currentQuestion.label,
        max_marks: currentQuestion.max_marks,
        rubric_json: {
          ...currentQuestion.rubric_json,
          criteria: currentQuestion.criteria,
          answer_key: currentQuestion.answer_key,
          model_solution: currentQuestion.model_solution,
        },
      });
      showSuccess('Question saved');
      setSaveAvailable(true);
    } catch (error) {
      console.error('Failed to save question', error);
      if (error instanceof ApiError && error.status === 404) {
        setSaveAvailable(false);
        showError('Save not available: no PATCH endpoint found.');
      } else {
        showError(error instanceof Error ? error.message : 'Failed to save question');
      }
    } finally {
      setSaving(false);
    }
  };

  const criteriaText = useMemo(() => currentQuestion?.criteria.join('\n') || '', [currentQuestion]);

  if (loading) {
    return <p>Loading review...</p>;
  }

  if (!currentQuestion) {
    return (
      <div className="card">
        <p>No parsed questions available.</p>
        <p><Link to="/">Back to Exams</Link></p>
      </div>
    );
  }

  return (
    <div className="card stack">
      <p><Link to={`/exams/${examId}`}>← Back to Exam</Link></p>
      <h1>Review Parsed Questions</h1>
      <p>Question {currentIndex + 1} of {questions.length}</p>

      <label className="stack">
        Label
        <input value={currentQuestion.label} onChange={(e) => onFieldChange('label', e.target.value)} />
      </label>

      <label className="stack">
        Max marks
        <input
          type="number"
          min={0}
          value={currentQuestion.max_marks}
          onChange={(e) => onFieldChange('max_marks', Number(e.target.value))}
        />
      </label>

      <label className="stack">
        Criteria (one per line)
        <textarea
          rows={5}
          value={criteriaText}
          onChange={(e) => onFieldChange('criteria', e.target.value.split('\n').map((line) => line.trim()).filter(Boolean))}
        />
      </label>

      <label className="stack">
        Answer key
        <textarea rows={4} value={currentQuestion.answer_key} onChange={(e) => onFieldChange('answer_key', e.target.value)} />
      </label>

      <label className="stack">
        Model solution
        <textarea rows={5} value={currentQuestion.model_solution} onChange={(e) => onFieldChange('model_solution', e.target.value)} />
      </label>

      <div className="actions-row">
        <button type="button" onClick={() => setCurrentIndex((idx) => Math.max(0, idx - 1))} disabled={currentIndex === 0}>Back</button>
        <button type="button" onClick={() => setCurrentIndex((idx) => Math.min(questions.length - 1, idx + 1))} disabled={currentIndex === questions.length - 1}>Next</button>
        <button type="button" onClick={onSave} disabled={saving || !saveAvailable}>{saving ? 'Saving...' : 'Save'}</button>
        {!saveAvailable && <span>Save not available</span>}
        <button type="button" onClick={() => navigate(`/exams/${examId}`)}>Finish Review</button>
      </div>
    </div>
  );
}

function mapQuestion(question: QuestionRead): EditableQuestion {
  const criteria = Array.isArray(question.rubric_json?.criteria)
    ? question.rubric_json.criteria.map((entry) => String(entry))
    : [];

  return {
    id: question.id,
    label: question.label,
    max_marks: question.max_marks,
    criteria,
    answer_key: String(question.rubric_json?.answer_key || ''),
    model_solution: String(question.rubric_json?.model_solution || ''),
    rubric_json: question.rubric_json,
  };
}

function parseQuestionsFromSource(source: Record<string, unknown>): Omit<EditableQuestion, 'id' | 'rubric_json'>[] {
  const maybeQuestions = source.questions;
  if (!Array.isArray(maybeQuestions)) return [];
  return maybeQuestions.map((item, index) => {
    const typed = typeof item === 'object' && item ? item as Record<string, unknown> : {};
    return {
      label: String(typed.label || `Q${index + 1}`),
      max_marks: Number(typed.max_marks || 0),
      criteria: Array.isArray(typed.criteria) ? typed.criteria.map((entry) => String(entry)) : [],
      answer_key: String(typed.answer_key || ''),
      model_solution: String(typed.model_solution || ''),
    };
  });
}
