import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, ApiError, getOpenApiPaths } from '../api/client';
import { EvidenceOverlayCanvas, type EvidenceBox } from '../components/EvidenceOverlayCanvas';
import { AutoGrowTextarea } from '../components/AutoGrowTextarea';
import { useToast } from '../components/ToastProvider';
import type { ExamKeyPage, QuestionRead } from '../types/api';

type MarksSource = 'explicit' | 'inferred' | 'unknown';

interface EditableQuestion {
  needs_review: boolean;
  evidence: EvidenceBox[];
  id: number;
  label: string;
  max_marks: number;
  answer_key: string;
  objective_codes: string[];
  question_text: string;
  warnings: string[];
  rubric_json: Record<string, unknown>;
}

export function ExamReviewPage() {
  const { examId: examIdParam } = useParams();
  const examId = Number(examIdParam);
  const [questions, setQuestions] = useState<EditableQuestion[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveAvailable, setSaveAvailable] = useState(true);
  const [previewError, setPreviewError] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [keyImageErrorDetail, setKeyImageErrorDetail] = useState<string>('');
  const [showOverlay, setShowOverlay] = useState(true);
  const [manualPageNumber, setManualPageNumber] = useState<number | null>(null);
  const [keyPagesByNumber, setKeyPagesByNumber] = useState<Record<number, ExamKeyPage>>({});
  const [examUnavailable, setExamUnavailable] = useState(false);
  const { showError, showSuccess } = useToast();
  const navigate = useNavigate();

  const requestedPage = Number(searchParams.get('page') || 0);
  const requestedQuestionId = Number(searchParams.get('questionId') || 0);
  const currentQuestion = questions[currentIndex];

  useEffect(() => {
    const loadQuestions = async () => {
      if (!examId || Number.isNaN(examId)) {
        showError('Invalid exam id for review page.');
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setExamUnavailable(false);
        const [fetchedQuestions, fetchedKeyPages, paths] = await Promise.all([
          api.getExamQuestionsForReview(examId),
          api.listExamKeyPages(examId),
          getOpenApiPaths(),
        ]);

        const patchAvailable = paths.has('/questions/{question_id}')
          || paths.has('/api/questions/{question_id}')
          || paths.has('/api/exams/{exam_id}/questions/{question_id}')
          || paths.has('/api/exams/{exam_id}/wizard/questions/{question_id}');
        setSaveAvailable(patchAvailable);

        const mapped = fetchedQuestions.map(mapQuestion).sort(compareQuestionsForReview);
        setQuestions(mapped);

        const matchingIndexByQuestionId = requestedQuestionId > 0
          ? mapped.findIndex((question) => question.id === requestedQuestionId)
          : -1;
        const matchingIndexByPage = requestedPage > 0
          ? mapped.findIndex((question) => getCurrentPageNumber(question) === requestedPage)
          : -1;
        const nextIndex = matchingIndexByQuestionId >= 0
          ? matchingIndexByQuestionId
          : matchingIndexByPage >= 0
            ? matchingIndexByPage
            : 0;
        setCurrentIndex(nextIndex);
        if (requestedPage > 0) {
          setManualPageNumber(requestedPage);
        }
        setKeyPagesByNumber(
          fetchedKeyPages.reduce<Record<number, ExamKeyPage>>((acc, page) => {
            acc[page.page_number] = page;
            return acc;
          }, {}),
        );
        showSuccess(`Loaded ${mapped.length} parsed items for review.`);
      } catch (error) {
        console.error('Failed to fetch questions for review', error);
        if (error instanceof ApiError && error.status === 404) {
          setExamUnavailable(true);
          setQuestions([]);
          setKeyPagesByNumber({});
          return;
        }
        const storageKey = `supermarks:lastParse:${examId}`;
        const storedParse = localStorage.getItem(storageKey);
        if (storedParse) {
          try {
            const parsed = JSON.parse(storedParse) as unknown;
            const fallbackQuestions = mapFallbackQuestions(parsed);
            if (fallbackQuestions.length > 0) {
              setQuestions(fallbackQuestions);
              setCurrentIndex(0);
              setKeyPagesByNumber({});
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
  }, [examId, requestedPage, requestedQuestionId, showError, showSuccess]);

  useEffect(() => {
    setPreviewError(false);
    setImageLoaded(false);
    setKeyImageErrorDetail('');
    if (requestedPage > 0) {
      setManualPageNumber(requestedPage);
      return;
    }
    setManualPageNumber(null);
  }, [currentIndex, requestedPage]);

  useEffect(() => {
    if (!currentQuestion) return;
    const next = new URLSearchParams(searchParams);
    next.set('questionId', String(currentQuestion.id));
    if (requestedPage > 0) {
      next.set('page', String(requestedPage));
    } else {
      next.delete('page');
    }
    const currentSerialized = searchParams.toString();
    const nextSerialized = next.toString();
    if (currentSerialized !== nextSerialized) {
      setSearchParams(next, { replace: true });
    }
  }, [currentQuestion?.id, requestedPage, searchParams, setSearchParams]);

  const flaggedCount = useMemo(() => questions.filter((question) => question.needs_review).length, [questions]);
  const completedCount = questions.length - flaggedCount;
  const derivedPageNumber = getCurrentPageNumber(currentQuestion);
  const currentKeyPageNumber = manualPageNumber ?? derivedPageNumber;
  const currentKeyPageMeta = keyPagesByNumber[currentKeyPageNumber];
  const availableKeyPages = useMemo(() => Object.keys(keyPagesByNumber).map(Number).sort((a, b) => a - b), [keyPagesByNumber]);
  const overlayEvidence = useMemo(
    () => currentQuestion?.evidence?.filter((box) => Number(box.page_number || 0) === currentKeyPageNumber) ?? [],
    [currentQuestion, currentKeyPageNumber],
  );
  const regionCountOnPage = overlayEvidence.length;
  const activeQuestionIdForVersion = currentQuestion?.id || currentIndex;
  const keyVisualUrl = `${api.getExamKeyPageUrl(examId, currentKeyPageNumber)}?v=${examId}-${currentKeyPageNumber}-${activeQuestionIdForVersion}`;
  const isDebugMode = import.meta.env.DEV;
  const questionLabelText = formatQuestionLabel(currentQuestion?.label);
  const currentWarnings = currentQuestion?.warnings ?? [];
  const marksSuggestion = useMemo(() => (currentQuestion ? getMarksSuggestion(currentQuestion) : null), [currentQuestion]);

  const loadKeyImageErrorDetail = async () => {
    try {
      const response = await fetch(api.getExamKeyPageUrl(examId, currentKeyPageNumber));
      if (response.ok) {
        setKeyImageErrorDetail('');
        return;
      }
      const payload = await response.json().catch(() => null);
      const detail = payload?.detail;
      if (detail) {
        setKeyImageErrorDetail(typeof detail === 'string' ? detail : JSON.stringify(detail));
      } else {
        setKeyImageErrorDetail(`Image request failed with status ${response.status}`);
      }
    } catch (error) {
      setKeyImageErrorDetail(error instanceof Error ? error.message : 'Unknown key image error');
    }
  };

  const updateCurrentQuestion = (updater: (question: EditableQuestion) => EditableQuestion) => {
    setQuestions((prev) => prev.map((question, index) => (index === currentIndex ? updater(question) : question)));
  };

  const setCurrentQuestionPage = (pageNumber: number) => {
    const nextPageNumber = Math.max(1, Math.floor(pageNumber));
    setManualPageNumber(nextPageNumber);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('page', String(nextPageNumber));
    if (currentQuestion) {
      nextParams.set('questionId', String(currentQuestion.id));
    }
    setSearchParams(nextParams, { replace: true });
    setPreviewError(false);
    setImageLoaded(false);
    setKeyImageErrorDetail('');
  };

  const onFieldChange = (field: keyof EditableQuestion, value: string | number | string[] | boolean) => {
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

  const saveQuestion = async (question: EditableQuestion) => {
    const updated = await api.updateQuestion(examId, question.id, {
      label: question.label,
      max_marks: question.max_marks,
      rubric_json: buildRubric(question),
    });
    const mapped = mapQuestion(updated);
    setQuestions((prev) => prev.map((item) => (item.id === mapped.id ? mapped : item)));
    return mapped;
  };

  const persistCurrentQuestion = async (transform?: (question: EditableQuestion) => EditableQuestion) => {
    if (!currentQuestion || !saveAvailable) {
      if (!saveAvailable) {
        showError('Save is unavailable because no PATCH endpoint exists.');
      }
      return null;
    }

    const questionToSave = transform ? transform(currentQuestion) : currentQuestion;
    if (transform) {
      setQuestions((prev) => prev.map((item, index) => (index === currentIndex ? questionToSave : item)));
    }
    return saveQuestion(questionToSave);
  };

  const goToNextQuestion = () => {
    setCurrentIndex((idx) => Math.min(questions.length - 1, idx + 1));
  };

  const onSave = async () => {
    try {
      setSaving(true);
      await persistCurrentQuestion();
      showSuccess('Parsed data saved.');
    } catch (error) {
      console.error('Failed to save question', error);
      if (error instanceof ApiError && error.status === 404) {
        setSaveAvailable(false);
        showError('Save not available: no PATCH endpoint found.');
      } else {
        showError(error instanceof Error ? error.message : 'Failed to save parsed data');
      }
    } finally {
      setSaving(false);
    }
  };

  const onConfirm = async () => {
    try {
      setSaving(true);
      await persistCurrentQuestion((question) => ({
        ...question,
        needs_review: false,
        rubric_json: buildRubric({ ...question, needs_review: false }),
      }));
      showSuccess('Parsed item confirmed.');
      goToNextQuestion();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to confirm parsed data');
    } finally {
      setSaving(false);
    }
  };

  const onRetryLater = async () => {
    try {
      setSaving(true);
      await persistCurrentQuestion((question) => ({
        ...question,
        needs_review: true,
        rubric_json: buildRubric({ ...question, needs_review: true }),
      }));
      showSuccess('Marked for later review.');
      goToNextQuestion();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to defer parsed item');
    } finally {
      setSaving(false);
    }
  };

  const canGoBack = currentIndex > 0;
  const canGoNext = currentIndex < questions.length - 1;

  if (loading) {
    return <p>Loading review...</p>;
  }

  if (examUnavailable) {
    return (
      <div className="card stack">
        <p>This exam record is unavailable.</p>
        <p><Link className="btn btn-secondary" to="/">Back to Home</Link></p>
      </div>
    );
  }

  if (!currentQuestion) {
    return (
      <div className="card stack">
        <p>No answer-key review items are available for this exam.</p>
        <p className="subtle-text" style={{ margin: 0 }}>
          This review screen is only for parsed answer-key questions. If this exam is using the totals workflow, open the exam workspace and use the front-page totals queue to review page previews and confirm the extracted totals.
        </p>
        <div className="actions-row" style={{ marginTop: 0 }}>
          <Link className="btn btn-primary" to={`/exams/${examId}`}>Back to exam workspace</Link>
          <Link className="btn btn-secondary" to="/">Back to Home</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="card card--hero stack review-card review-workspace">
      <p><Link to={`/exams/${examId}`}>← Back to Exam</Link></p>
      <div className="review-header-block">
        <div>
          <h1 className="page-title" style={{ fontSize: "2rem" }}>Confirm parsed marking data</h1>
          <p className="subtle-text">Flagged items are shown first. Confirm or quickly edit label, marks, objective codes, and answer key.</p>
          {requestedPage > 0 && (
            <p className="subtle-text" style={{ marginTop: 6 }}>
              Focused on flagged page <strong>{requestedPage}</strong>. Move page-by-page, then return to the full queue when ready.
            </p>
          )}
        </div>
        <div className="review-summary-pills">
          <span className="review-summary-pill is-flagged">Needs review: {flaggedCount}</span>
          <span className="review-summary-pill is-confirmed">Confirmed: {completedCount}</span>
          <span className="review-summary-pill status-in-progress">Total: {questions.length}</span>
        </div>
      </div>

      <div className="review-layout-grid">
        <aside className="review-queue-panel stack">
          <div>
            <strong>Review queue</strong>
            <p className="subtle-text">Item {currentIndex + 1} of {questions.length}</p>
            {requestedPage > 0 && (
              <div className="actions-row" style={{ marginTop: 6 }}>
                <Link className="btn btn-secondary btn-sm" to={`/exams/${examId}/review`}>Full queue</Link>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setCurrentQuestionPage(requestedPage)}>
                  Recenter page {requestedPage}
                </button>
              </div>
            )}
          </div>
          <div className="review-queue-list">
            {questions.map((question, index) => {
              const pageNumber = getCurrentPageNumber(question);
              const warningCount = question.warnings.length;
              return (
                <button
                  key={question.id}
                  type="button"
                  className={`review-queue-item${index === currentIndex ? ' is-active' : ''}${question.needs_review ? ' is-flagged' : ' is-confirmed'}`}
                  onClick={() => setCurrentIndex(index)}
                >
                  <div className="review-queue-item-header">
                    <span>{question.label || `Question ${index + 1}`}</span>
                    <span>{question.max_marks} marks</span>
                  </div>
                  <div className="review-queue-item-meta">
                    <span>Page {pageNumber}</span>
                    {requestedPage > 0 && pageNumber === requestedPage && <span className="queue-inline-chip">Focused page</span>}
                    {question.objective_codes.length > 0 && <span>{question.objective_codes.join(', ')}</span>}
                    {warningCount > 0 && <span>{warningCount} warning{warningCount === 1 ? '' : 's'}</span>}
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        <div className="stack">
          <div className="stack review-focus-banner">
            <div className="review-focus-title-row">
              <p style={{ fontWeight: 600, margin: 0 }}>
                {questionLabelText
                  ? `Question ${questionLabelText} · page ${currentKeyPageNumber}`
                  : `Reviewing key page ${currentKeyPageNumber}`}
              </p>
              <span className={`review-status-pill ${currentQuestion.needs_review ? 'is-flagged' : 'is-confirmed'}`}>
                {currentQuestion.needs_review ? 'Needs review' : 'Confirmed'}
              </span>
            </div>
            <p className="subtle-text" style={{ margin: 0 }}>
              Source page {String(currentQuestion.rubric_json?.source_page_number || currentKeyPageNumber)}
              {currentQuestion.evidence.length > 0 ? ` · ${currentQuestion.evidence.length} evidence box${currentQuestion.evidence.length === 1 ? '' : 'es'}` : ' · no evidence boxes'}
            </p>

            <div className="actions-row" style={{ marginTop: 4 }}>
              <button type="button" onClick={() => setCurrentQuestionPage(currentKeyPageNumber - 1)} disabled={currentKeyPageNumber <= 1}>Prev page</button>
              <button type="button" onClick={() => setCurrentQuestionPage(currentKeyPageNumber + 1)}>Next page</button>
              <Link className="btn btn-secondary btn-sm" to={`/exams/${examId}`}>Back to exam recovery</Link>
            </div>
            {!currentKeyPageMeta && (
              <div className="warning-text">No key page metadata found for page {currentKeyPageNumber}</div>
            )}
            {previewError && currentKeyPageMeta && (
              <div className="warning-text">
                Key page image unavailable for page {currentKeyPageNumber}
                {isDebugMode && keyImageErrorDetail && (
                  <p className="subtle-text" style={{ margin: '8px 0 0 0', whiteSpace: 'pre-wrap' }}>{keyImageErrorDetail}</p>
                )}
              </div>
            )}
            {imageLoaded && !previewError && regionCountOnPage === 0 && (
              <div className="review-info-box">No overlay evidence was produced for this page. You can still confirm the parsed fields below.</div>
            )}

            {currentKeyPageMeta && !previewError ? (
              <EvidenceOverlayCanvas
                key={currentKeyPageNumber}
                imageKey={currentKeyPageNumber}
                imageUrl={keyVisualUrl}
                evidence={overlayEvidence}
                visible={showOverlay && regionCountOnPage > 0}
                pageNumber={currentKeyPageNumber}
                onImageError={() => {
                  setPreviewError(true);
                  setImageLoaded(false);
                  void loadKeyImageErrorDetail();
                }}
                onImageLoad={() => {
                  setPreviewError(false);
                  setImageLoaded(true);
                  setKeyImageErrorDetail('');
                }}
              />
            ) : null}

            {currentKeyPageMeta && regionCountOnPage > 0 && !previewError && (
              <label>
                <input type="checkbox" checked={showOverlay} onChange={(e) => setShowOverlay(e.target.checked)} /> Show overlays
              </label>
            )}
            <p className="subtle-text" style={{ margin: 0 }}>
              Overlay diagnostics: currentQuestionId: {currentQuestion.id} | currentKeyPageNumber: {currentKeyPageNumber} | availableKeyPages: [{availableKeyPages.join(', ')}] | regionCountOnPage: {regionCountOnPage} | manualPage: {manualPageNumber ?? 'auto'} | imageLoaded: {String(imageLoaded && !previewError)}
            </p>
            <p className="subtle-text" style={{ margin: 0, fontSize: '0.8rem' }}>
              Debug metadata → label: {currentQuestion.label} | original_label: {String(currentQuestion.rubric_json?.original_label || '—')} | source_page_number: {String(currentQuestion.rubric_json?.source_page_number || '—')} | key_page_number: {String(currentQuestion.rubric_json?.key_page_number || '—')} | region count: {currentQuestion.evidence.length}
            </p>
          </div>

          {currentWarnings.length > 0 && (
            <section className="card stack review-warning-list">
              <h3>Parse warnings</h3>
              <ul>
                {currentWarnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            </section>
          )}

          <section className="card stack review-edit-panel">
            <div className="review-field-grid">
              <label className="stack">
                Label
                <input value={currentQuestion.label} onChange={(e) => onFieldChange('label', e.target.value)} />
              </label>

              <label className="stack">
                Max marks
                <div className="actions-row" style={{ alignItems: 'center' }}>
                  <input type="number" min={0} value={currentQuestion.max_marks} onChange={(e) => onFieldChange('max_marks', Number(e.target.value))} />
                  {marksSuggestion && (
                    <button type="button" onClick={() => onFieldChange('max_marks', marksSuggestion.value)}>
                      Suggest {marksSuggestion.value}
                    </button>
                  )}
                </div>
                {marksSuggestion && (
                  <span className="subtle-text">Current marks source: {marksSuggestion.source} · confidence {marksSuggestion.confidence.toFixed(2)}</span>
                )}
              </label>
            </div>

            <label className="stack">
              Objective codes
              <input
                value={currentQuestion.objective_codes.join(', ')}
                onChange={(e) => onFieldChange('objective_codes', parseObjectiveCodes(e.target.value))}
                placeholder="OB1, OB2"
              />
              <span className="subtle-text">Comma-separated. Keep only codes you want attached to this question.</span>
            </label>

            <AutoGrowTextarea
              id={`answer-key-${currentQuestion.id}`}
              label="Answer key"
              className="textarea-large"
              value={currentQuestion.answer_key}
              onChange={(e) => onFieldChange('answer_key', e.target.value)}
            />

            <div className="stack">
              <label>Detected prompt text</label>
              <div className="review-readonly-block">{currentQuestion.question_text || 'No prompt text captured.'}</div>
            </div>

            {!saveAvailable && <p className="subtle-text">Save is unavailable because the backend does not expose a PATCH endpoint.</p>}

            <div className="actions-row">
              <button type="button" onClick={() => setCurrentIndex((idx) => Math.max(0, idx - 1))} disabled={!canGoBack}>Back</button>
              <button type="button" onClick={() => setCurrentIndex((idx) => Math.min(questions.length - 1, idx + 1))} disabled={!canGoNext}>Next</button>
              <button type="button" className="btn btn-secondary" onClick={onRetryLater} disabled={saving || !saveAvailable}>Flag for later</button>
              <button type="button" className="btn btn-secondary" onClick={onSave} disabled={saving || !saveAvailable}>{saving ? 'Saving...' : 'Save edits'}</button>
              <button type="button" className="btn btn-primary" onClick={onConfirm} disabled={saving || !saveAvailable}>{saving ? 'Saving...' : 'Confirm + next'}</button>
              <button type="button" onClick={async () => { await api.completeExamKeyReview(examId); navigate(`/exams/${examId}`); }}>Finish review</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function mapFallbackQuestions(parseResult: unknown): EditableQuestion[] {
  if (Array.isArray(parseResult)) {
    return parseResult.map(mapFallbackQuestion).filter((question): question is EditableQuestion => question !== null).sort(compareQuestionsForReview);
  }

  if (typeof parseResult !== 'object' || parseResult === null) {
    return [];
  }

  const value = parseResult as { questions?: unknown; result?: { questions?: unknown } };
  if (Array.isArray(value.questions)) {
    return value.questions.map(mapFallbackQuestion).filter((question): question is EditableQuestion => question !== null).sort(compareQuestionsForReview);
  }

  if (Array.isArray(value.result?.questions)) {
    return value.result.questions.map(mapFallbackQuestion).filter((question): question is EditableQuestion => question !== null).sort(compareQuestionsForReview);
  }

  return [];
}

function mapFallbackQuestion(item: unknown, index: number): EditableQuestion | null {
  if (typeof item !== 'object' || item === null) {
    return {
      id: index + 1,
      label: `Question ${index + 1}`,
      max_marks: 0,
      answer_key: String(item || ''),
      objective_codes: [],
      question_text: '',
      warnings: ['Needs teacher review'],
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
    answer_key: String(rubric_json.answer_key || value.answer_key || ''),
    objective_codes: normalizeObjectiveCodes(rubric_json.objective_codes ?? value.objective_codes),
    question_text: String(rubric_json.question_text || value.question_text || ''),
    warnings: normalizeWarnings(rubric_json.warnings ?? value.warnings),
    rubric_json,
    evidence: Array.isArray((rubric_json as Record<string, unknown>).evidence) ? ((rubric_json as Record<string, unknown>).evidence as EvidenceBox[]) : [],
    needs_review: Boolean((rubric_json as Record<string, unknown>).needs_review ?? true),
  };
}

function buildRubric(question: Pick<EditableQuestion, 'answer_key' | 'objective_codes' | 'question_text' | 'warnings' | 'rubric_json' | 'needs_review' | 'evidence'>) {
  return {
    ...question.rubric_json,
    answer_key: question.answer_key,
    objective_codes: question.objective_codes,
    question_text: question.question_text,
    warnings: question.warnings,
    needs_review: question.needs_review,
    evidence: question.evidence,
  };
}

function mapQuestion(question: QuestionRead): EditableQuestion {
  return {
    id: question.id,
    label: question.label,
    max_marks: question.max_marks,
    answer_key: String(question.rubric_json?.answer_key || ''),
    objective_codes: normalizeObjectiveCodes(question.rubric_json?.objective_codes),
    question_text: String(question.rubric_json?.question_text || ''),
    warnings: normalizeWarnings(question.rubric_json?.warnings),
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

  const questionText = String(question.question_text || question.rubric_json.question_text || '');
  let guess = 2;
  if (question.answer_key.trim().length < 2) {
    guess = 1;
  } else if (questionText.length > 180) {
    guess = 4;
  }
  return { value: guess, confidence: 0.3, source: 'unknown' };
}

function compareQuestionsForReview(a: EditableQuestion, b: EditableQuestion): number {
  if (a.needs_review !== b.needs_review) {
    return a.needs_review ? -1 : 1;
  }

  const aRubric = a.rubric_json as Record<string, unknown> | undefined;
  const bRubric = b.rubric_json as Record<string, unknown> | undefined;
  const aParseOrder = Number(aRubric?.parse_order || 0);
  const bParseOrder = Number(bRubric?.parse_order || 0);

  const aHasParseOrder = Number.isFinite(aParseOrder) && aParseOrder > 0;
  const bHasParseOrder = Number.isFinite(bParseOrder) && bParseOrder > 0;

  if (aHasParseOrder && bHasParseOrder && aParseOrder !== bParseOrder) {
    return aParseOrder - bParseOrder;
  }
  if (aHasParseOrder !== bHasParseOrder) {
    return aHasParseOrder ? -1 : 1;
  }

  const aSourcePage = Number(aRubric?.source_page_number || aRubric?.key_page_number || 0);
  const bSourcePage = Number(bRubric?.source_page_number || bRubric?.key_page_number || 0);
  if (aSourcePage !== bSourcePage) {
    return aSourcePage - bSourcePage;
  }

  return a.id - b.id;
}

function getCurrentPageNumber(question: EditableQuestion | undefined): number {
  if (!question) return 1;

  const rubric = question.rubric_json as Record<string, unknown> | undefined;
  const evidence = Array.isArray(question.evidence) ? question.evidence : [];
  const firstEvidencePage = evidence
    .map((item) => Number(item?.page_number || 0))
    .find((pageNumber) => Number.isFinite(pageNumber) && pageNumber > 0) || 0;
  const keyPageNumber = Number(rubric?.key_page_number || 0);
  const sourcePageNumber = Number(rubric?.source_page_number || 0);

  if (firstEvidencePage > 0) {
    return Math.max(1, Math.floor(firstEvidencePage));
  }

  if (Number.isFinite(sourcePageNumber) && sourcePageNumber > 0) {
    return Math.max(1, Math.floor(sourcePageNumber));
  }

  if (Number.isFinite(keyPageNumber) && keyPageNumber > 0) {
    return Math.max(1, Math.floor(keyPageNumber));
  }

  return 1;
}

function formatQuestionLabel(label: string | undefined): ReactNode | null {
  if (!label) return null;
  const match = label.match(/Q\s*([0-9]+)/i) || label.match(/Question\s*([0-9]+)/i);
  if (match) return `Q${match[1]}`;
  return null;
}

function normalizeObjectiveCodes(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || '').trim()).filter(Boolean);
}

function normalizeWarnings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || '').trim()).filter(Boolean);
}

function parseObjectiveCodes(value: string): string[] {
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}
