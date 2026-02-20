import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, getOpenApiPaths } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { QuestionRead } from '../types/api';

interface Criterion {
  desc: string;
  marks: number;
}

interface EditableQuestion {
  id: number;
  label: string;
  max_marks: number;
  criteria: Criterion[];
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

  useEffect(() => {
    const loadQuestions = async () => {
      if (!examId || Number.isNaN(examId)) {
        showError('Invalid exam id for review page.');
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        const [fetchedQuestions, paths] = await Promise.all([
          api.getExamQuestionsForReview(examId),
          getOpenApiPaths(),
        ]);

        const patchAvailable = paths.has('/questions/{question_id}')
          || paths.has('/exams/{exam_id}/questions/{question_id}')
          || paths.has('/exams/{exam_id}/wizard/questions/{question_id}');
        setSaveAvailable(patchAvailable);

        const mapped = fetchedQuestions.map(mapQuestion);
        setQuestions(mapped);
        showSuccess(`Loaded ${mapped.length} questions for review.`);
      } catch (error) {
        console.error('Failed to fetch questions for review', error);
        const storageKey = `supermarks:lastParse:${examId}`;
        const storedParse = localStorage.getItem(storageKey);
        if (storedParse) {
          try {
            const parsed = JSON.parse(storedParse) as unknown;
            const fallbackQuestions = mapFallbackQuestions(parsed);
            if (fallbackQuestions.length > 0) {
              setQuestions(fallbackQuestions);
              showError('Failed to fetch questions from backend. Loaded fallback parse result from local storage.');
              return;
            }
          } catch (storageError) {
            console.error('Failed to parse fallback local storage parse result', storageError);
          }
        }

        showError(error instanceof Error ? error.message : 'Failed to load review questions');
      } finally {
        setLoading(false);
      }
    };

    void loadQuestions();
  }, [examId, showError, showSuccess]);

  const currentQuestion = questions[currentIndex];

  const updateCurrentQuestion = (updater: (question: EditableQuestion) => EditableQuestion) => {
    setQuestions((prev) => prev.map((question, index) => (index === currentIndex ? updater(question) : question)));
  };

  const onFieldChange = (field: keyof EditableQuestion, value: string | number) => {
    updateCurrentQuestion((question) => {
      const next = {
        ...question,
        [field]: value,
      };
      return {
        ...next,
        rubric_json: buildRubric(next),
      };
    });
  };

  const onCriterionChange = (criterionIndex: number, field: keyof Criterion, value: string | number) => {
    updateCurrentQuestion((question) => {
      const criteria = question.criteria.map((criterion, index) => (index === criterionIndex
        ? { ...criterion, [field]: field === 'marks' ? Number(value) : value }
        : criterion));
      const next = { ...question, criteria };
      return {
        ...next,
        rubric_json: buildRubric(next),
      };
    });
  };

  const onAddCriterion = () => {
    updateCurrentQuestion((question) => {
      const next = { ...question, criteria: [...question.criteria, { desc: '', marks: 0 }] };
      return {
        ...next,
        rubric_json: buildRubric(next),
      };
    });
  };

  const onRemoveCriterion = (criterionIndex: number) => {
    updateCurrentQuestion((question) => {
      const next = { ...question, criteria: question.criteria.filter((_, index) => index !== criterionIndex) };
      return {
        ...next,
        rubric_json: buildRubric(next),
      };
    });
  };

  const onSave = async () => {
    if (!currentQuestion || !saveAvailable) {
      if (!saveAvailable) {
        showError('Save is unavailable because no PATCH endpoint exists.');
      }
      return;
    }

    try {
      setSaving(true);
      await api.updateQuestion(examId, currentQuestion.id, {
        label: currentQuestion.label,
        max_marks: currentQuestion.max_marks,
        rubric_json: buildRubric(currentQuestion),
      });
      showSuccess('Question saved successfully.');
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

  const canGoBack = currentIndex > 0;
  const canGoNext = currentIndex < questions.length - 1;
  const criteriaTotalMarks = useMemo(
    () => currentQuestion?.criteria.reduce((sum, criterion) => sum + (Number.isFinite(criterion.marks) ? criterion.marks : 0), 0) || 0,
    [currentQuestion],
  );

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
    <div className="card stack review-card">
      <p><Link to={`/exams/${examId}`}>← Back to Exam</Link></p>
      <h1>Create Exam Wizard: Review Questions</h1>
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

      <div className="stack criteria-block">
        <div className="criteria-header">
          <h3>Criteria</h3>
          <button type="button" onClick={onAddCriterion}>+ Add criterion</button>
        </div>
        {currentQuestion.criteria.length === 0 && <p className="subtle-text">No criteria yet.</p>}

        {currentQuestion.criteria.map((criterion, criterionIndex) => (
          <div key={`${currentQuestion.id}-${criterionIndex}`} className="criteria-row">
            <input
              value={criterion.desc}
              onChange={(e) => onCriterionChange(criterionIndex, 'desc', e.target.value)}
              placeholder="Description"
            />
            <input
              type="number"
              min={0}
              value={criterion.marks}
              onChange={(e) => onCriterionChange(criterionIndex, 'marks', Number(e.target.value))}
              placeholder="Marks"
            />
            <button type="button" onClick={() => onRemoveCriterion(criterionIndex)}>Remove</button>
          </div>
        ))}
        <p className="subtle-text">Total criterion marks: {criteriaTotalMarks}</p>
      </div>

      <label className="stack">
        Answer key
        <textarea rows={4} value={currentQuestion.answer_key} onChange={(e) => onFieldChange('answer_key', e.target.value)} />
      </label>

      <label className="stack">
        Model solution
        <textarea rows={5} value={currentQuestion.model_solution} onChange={(e) => onFieldChange('model_solution', e.target.value)} />
      </label>

      {!saveAvailable && <p className="subtle-text">Save is unavailable because the backend does not expose a PATCH endpoint.</p>}

      <div className="actions-row">
        <button type="button" onClick={() => setCurrentIndex((idx) => Math.max(0, idx - 1))} disabled={!canGoBack}>Back</button>
        <button type="button" onClick={() => setCurrentIndex((idx) => Math.min(questions.length - 1, idx + 1))} disabled={!canGoNext}>Next</button>
        <button type="button" onClick={onSave} disabled={saving || !saveAvailable}>{saving ? 'Saving...' : 'Save'}</button>
        <button type="button" onClick={() => navigate(`/exams/${examId}`)}>Finish Review</button>
      </div>
    </div>
  );
}

function mapFallbackQuestions(parseResult: unknown): EditableQuestion[] {
  if (Array.isArray(parseResult)) {
    return parseResult.map(mapFallbackQuestion).filter((question): question is EditableQuestion => question !== null);
  }

  if (typeof parseResult !== 'object' || parseResult === null) {
    return [];
  }

  const value = parseResult as { questions?: unknown; result?: { questions?: unknown } };
  if (Array.isArray(value.questions)) {
    return value.questions.map(mapFallbackQuestion).filter((question): question is EditableQuestion => question !== null);
  }

  if (Array.isArray(value.result?.questions)) {
    return value.result.questions.map(mapFallbackQuestion).filter((question): question is EditableQuestion => question !== null);
  }

  return [];
}

function mapFallbackQuestion(item: unknown, index: number): EditableQuestion | null {
  if (typeof item !== 'object' || item === null) {
    return {
      id: index + 1,
      label: `Question ${index + 1}`,
      max_marks: 0,
      criteria: [],
      answer_key: String(item || ''),
      model_solution: '',
      rubric_json: {},
    };
  }

  const value = item as Record<string, unknown>;
  const id = typeof value.id === 'number' ? value.id : index + 1;
  const label = String(value.label || value.question || `Question ${index + 1}`);
  const max_marks = typeof value.max_marks === 'number'
    ? value.max_marks
    : typeof value.points === 'number'
      ? value.points
      : 0;
  const rubric_json = typeof value.rubric_json === 'object' && value.rubric_json !== null
    ? value.rubric_json as Record<string, unknown>
    : {};

  return {
    id,
    label,
    max_marks,
    criteria: normalizeCriteria(rubric_json.criteria),
    answer_key: String(rubric_json.answer_key || value.answer_key || ''),
    model_solution: String(rubric_json.model_solution || value.model_solution || ''),
    rubric_json,
  };
}

function buildRubric(question: Pick<EditableQuestion, 'criteria' | 'answer_key' | 'model_solution' | 'rubric_json'>) {
  return {
    ...question.rubric_json,
    criteria: question.criteria,
    answer_key: question.answer_key,
    model_solution: question.model_solution,
  };
}

function mapQuestion(question: QuestionRead): EditableQuestion {
  const criteriaSource = question.rubric_json?.criteria;
  const criteria = normalizeCriteria(criteriaSource);

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

function normalizeCriteria(criteriaSource: unknown): Criterion[] {
  if (!Array.isArray(criteriaSource)) {
    return [];
  }

  return criteriaSource.map((item) => {
    if (typeof item === 'string') {
      return { desc: item, marks: 0 };
    }

    if (typeof item === 'object' && item) {
      const value = item as Record<string, unknown>;
      return {
        desc: String(value.desc || value.description || ''),
        marks: Number(value.marks || 0),
      };
    }

    return { desc: String(item), marks: 0 };
  });
}
