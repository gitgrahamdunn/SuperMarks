import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, getOpenApiPaths } from '../api/client';
import { EvidenceOverlayCanvas, type EvidenceBox } from '../components/EvidenceOverlayCanvas';
import { useToast } from '../components/ToastProvider';
import type { ParseCost, ParseUsage, QuestionRead } from '../types/api';

interface Criterion {
  desc: string;
  marks: number;
}

type MarksSource = 'explicit' | 'inferred' | 'unknown';



interface ParseMeta {
  model_used: string;
  usage: ParseUsage;
  cost: ParseCost;
}

interface EditableQuestion {
  needs_review: boolean;
  evidence: EvidenceBox[];
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
  const [previewError, setPreviewError] = useState(false);
  const [showSplitDialog, setShowSplitDialog] = useState(false);
  const [criteriaSplitIndex, setCriteriaSplitIndex] = useState(1);
  const [parseMeta, setParseMeta] = useState<ParseMeta | null>(null);
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
        const storageKey = `supermarks:lastParse:${examId}`;
        const storedParse = localStorage.getItem(storageKey);
        if (storedParse) {
          try {
            const parsed = JSON.parse(storedParse) as Partial<ParseMeta>;
            if (parsed?.model_used && parsed?.usage && parsed?.cost) {
              setParseMeta(parsed as ParseMeta);
            }
          } catch {
            setParseMeta(null);
          }
        }
        const [fetchedQuestions, paths] = await Promise.all([
          api.getExamQuestionsForReview(examId),
          getOpenApiPaths(),
        ]);

        const patchAvailable = paths.has('/questions/{question_id}')
          || paths.has('/api/questions/{question_id}')
          || paths.has('/api/exams/{exam_id}/questions/{question_id}')
          || paths.has('/api/exams/{exam_id}/wizard/questions/{question_id}');
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

  useEffect(() => {
    setPreviewError(false);
    setShowSplitDialog(false);
    setCriteriaSplitIndex(1);
  }, [currentIndex]);

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

  const saveQuestion = async (question: EditableQuestion) => {
    const updated = await api.updateQuestion(examId, question.id, {
      label: question.label,
      max_marks: question.max_marks,
      rubric_json: buildRubric(question),
    });
    const mapped = mapQuestion(updated);
    setQuestions((prev) => prev.map((item) => (item.id === mapped.id ? mapped : item)));
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
      await saveQuestion(currentQuestion);
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

  const onConfirmMarks = async () => {
    if (!currentQuestion || !saveAvailable) return;
    try {
      setSaving(true);
      const suggestion = getMarksSuggestion(currentQuestion);
      const rubricWithMeta = {
        ...buildRubric(currentQuestion),
        marks_source: suggestion.source,
        marks_confidence: suggestion.confidence,
      };
      const updated = await api.updateQuestion(examId, currentQuestion.id, {
        max_marks: currentQuestion.max_marks,
        rubric_json: rubricWithMeta,
        label: currentQuestion.label,
      });
      const mapped = mapQuestion(updated);
      setQuestions((prev) => prev.map((item) => (item.id === mapped.id ? mapped : item)));
      showSuccess('Saved');
      setCurrentIndex((idx) => Math.min(questions.length - 1, idx + 1));
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to confirm marks');
    } finally {
      setSaving(false);
    }
  };

  const refreshQuestions = async (focusQuestionId?: number) => {
    const fetchedQuestions = await api.getExamQuestionsForReview(examId);
    const mapped = fetchedQuestions.map(mapQuestion);
    setQuestions(mapped);
    if (mapped.length === 0) {
      setCurrentIndex(0);
      return;
    }
    if (focusQuestionId) {
      const foundIndex = mapped.findIndex((item) => item.id === focusQuestionId);
      if (foundIndex >= 0) {
        setCurrentIndex(foundIndex);
        return;
      }
    }
    setCurrentIndex((prev) => Math.min(prev, mapped.length - 1));
  };

  const onMergeWithNext = async () => {
    if (!currentQuestion || currentIndex >= questions.length - 1) return;
    try {
      setSaving(true);
      const result = await api.mergeQuestionWithNext(examId, currentQuestion.id);
      await refreshQuestions(result.question.id);
      showSuccess('Merged with next question.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to merge question');
    } finally {
      setSaving(false);
    }
  };

  const onSplitByCriteria = async () => {
    if (!currentQuestion) return;
    try {
      setSaving(true);
      const result = await api.splitQuestionByCriteria(examId, currentQuestion.id, criteriaSplitIndex);
      await refreshQuestions(result.original.id);
      setShowSplitDialog(false);
      showSuccess('Question split into two.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to split question');
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
  const marksSuggestion = useMemo(() => (currentQuestion ? getMarksSuggestion(currentQuestion) : null), [currentQuestion]);

  const isHighUsage = (parseMeta?.cost.total_cost || 0) > 0.02;

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
      {parseMeta && (
        <div className="subtle-text">
          <p>Model used: {parseMeta.model_used}</p>
          <p>Tokens: {parseMeta.usage.total_tokens.toLocaleString()}</p>
          <p>Cost: ${parseMeta.cost.total_cost.toFixed(4)}</p>
          {isHighUsage && <p className="warning-text">This key required higher model usage.</p>}
        </div>
      )}

      <div className="question-list">
        {questions.map((question, index) => (
          <button
            key={question.id}
            type="button"
            className={`question-list-item ${index === currentIndex ? 'active' : ''}`}
            onClick={() => setCurrentIndex(index)}
          >
            <strong>{question.label || `Q${index + 1}`}</strong> · {question.max_marks} marks
            <div className="subtle-text">{String(question.rubric_json.question_text || '').slice(0, 72) || 'No text preview'}</div>
          </button>
        ))}
      </div>

      <div className="stack criteria-block">
        <div className="criteria-header">
          <h3>Question tools</h3>
        </div>
        <div className="actions-row">
          {currentIndex < questions.length - 1 && (
            <button type="button" onClick={onMergeWithNext} disabled={saving}>Merge with next</button>
          )}
          <button type="button" onClick={() => setShowSplitDialog((v) => !v)} disabled={saving || currentQuestion.criteria.length < 2}>Split into two</button>
        </div>
        {showSplitDialog && (
          <div className="stack">
            <label className="stack">
              Split mode
              <select value="criteria_index" disabled>
                <option value="criteria_index">Split criteria list at item #N</option>
              </select>
            </label>
            <label className="stack">
              Split index (first N criteria stay in current question)
              <input type="number" min={1} max={Math.max(1, currentQuestion.criteria.length - 1)} value={criteriaSplitIndex} onChange={(e) => setCriteriaSplitIndex(Number(e.target.value))} />
            </label>
            <button type="button" onClick={onSplitByCriteria} disabled={criteriaSplitIndex < 1 || criteriaSplitIndex >= currentQuestion.criteria.length || saving}>Confirm split</button>
          </div>
        )}
      </div>

      <div className="stack" style={{ border: '1px solid #d1d5db', borderRadius: 10, padding: 10, background: '#f8fafc' }}>
        <label><input type="checkbox" checked={!previewError && (currentQuestion.evidence?.length ?? 0) > 0} readOnly /> Evidence loaded</label>
        {!previewError ? (
          <EvidenceOverlayCanvas
            imageUrl={api.getQuestionKeyVisualUrl(examId, currentQuestion.id)}
            evidence={currentQuestion.evidence || []}
            visible
            onImageError={() => setPreviewError(true)}
          />
        ) : (
          <div className="stack" style={{ gap: 8 }}>
            <p className="subtle-text">Image failed to load.</p>
            <button type="button" onClick={() => window.open(`/api/exams/${examId}/key/page/1`, '_blank', 'noopener,noreferrer')}>
              Open key page
            </button>
          </div>
        )}
      </div>

      <label className="stack">
        Label
        <input value={currentQuestion.label} onChange={(e) => onFieldChange('label', e.target.value)} />
      </label>

      <label className="stack">
        Max marks
        <div className="actions-row" style={{ alignItems: 'center' }}>
          <input
            type="number"
            min={0}
            value={currentQuestion.max_marks}
            onChange={(e) => onFieldChange('max_marks', Number(e.target.value))}
          />
          {marksSuggestion && (
            <button type="button" onClick={() => onFieldChange('max_marks', marksSuggestion.value)}>
              Suggest: {marksSuggestion.value} ({marksSuggestion.confidence.toFixed(2)})
            </button>
          )}
        </div>
      </label>

      <div className="stack criteria-block">
        <div className="criteria-header">
          <h3>Scoring criteria</h3>
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
        <button type="button" onClick={onConfirmMarks} disabled={saving || !saveAvailable}>Confirm marks</button>
        <button type="button" onClick={async () => { await api.completeExamKeyReview(examId); navigate(`/exams/${examId}`); }}>Finish setup</button>
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
      evidence: [],
      needs_review: true,
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
    evidence: Array.isArray((rubric_json as Record<string, unknown>).evidence) ? ((rubric_json as Record<string, unknown>).evidence as EvidenceBox[]) : [],
    needs_review: Boolean((rubric_json as Record<string, unknown>).needs_review),
  };
}

function buildRubric(question: Pick<EditableQuestion, 'criteria' | 'answer_key' | 'model_solution' | 'rubric_json' | 'needs_review' | 'evidence'>) {
  return {
    ...question.rubric_json,
    criteria: question.criteria,
    answer_key: question.answer_key,
    model_solution: question.model_solution,
    needs_review: question.needs_review,
    evidence: question.evidence,
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
    evidence: Array.isArray(question.rubric_json?.evidence) ? (question.rubric_json.evidence as EvidenceBox[]) : [],
    needs_review: Boolean(question.rubric_json?.needs_review),
  };
}

function getMarksSuggestion(question: EditableQuestion): { value: number; confidence: number; source: MarksSource } {
  const source = (question.rubric_json.marks_source as MarksSource) || 'unknown';
  const storedConfidence = Number(question.rubric_json.marks_confidence || 0);
  const hasMarks = Number(question.max_marks) > 0;
  if (source === 'explicit' && hasMarks) {
    return { value: question.max_marks, confidence: 0.95, source };
  }
  if (source === 'inferred' && hasMarks) {
    return { value: question.max_marks, confidence: Math.max(0, Math.min(1, storedConfidence || 0.6)), source };
  }

  const criteriaCount = question.criteria.length;
  const criteriaSum = question.criteria.reduce((sum, c) => sum + (Number.isFinite(c.marks) ? c.marks : 0), 0);
  const questionText = String(question.rubric_json.question_text || '');
  let guess = 2;
  if (criteriaCount > 0 && criteriaSum === 0) {
    guess = 1;
  } else if (criteriaSum > 0) {
    guess = criteriaSum;
  } else if (questionText.length > 180) {
    guess = 4;
  }
  return { value: guess, confidence: 0.3, source: 'unknown' };
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
