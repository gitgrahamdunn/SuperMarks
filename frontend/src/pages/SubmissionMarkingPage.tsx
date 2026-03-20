import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { AutoGrowTextarea } from '../components/AutoGrowTextarea';
import { useToast } from '../components/ToastProvider';
import type {
  GradeResultRead,
  QuestionRead,
  SubmissionPrepareQuestionStatus,
  SubmissionPrepareStatus,
  SubmissionRead,
  SubmissionResults,
  TranscriptionRead,
} from '../types/api';

interface MarkingDraft {
  marks_awarded: string;
  teacher_note: string;
}

export function SubmissionMarkingPage() {
  const params = useParams();
  const submissionId = Number(params.submissionId);
  const [searchParams] = useSearchParams();
  const examId = Number(searchParams.get('examId'));
  const requestedQuestionId = Number(searchParams.get('questionId') || 0);
  const returnTo = searchParams.get('returnTo')?.trim() || `/exams/${examId}`;
  const returnLabel = searchParams.get('returnLabel')?.trim() || 'Back to Exam queue';
  const [submission, setSubmission] = useState<SubmissionRead | null>(null);
  const [questions, setQuestions] = useState<QuestionRead[]>([]);
  const [results, setResults] = useState<SubmissionResults | null>(null);
  const [prepareStatus, setPrepareStatus] = useState<SubmissionPrepareStatus | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [saving, setSaving] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [drafts, setDrafts] = useState<Record<number, MarkingDraft>>({});
  const markInputRef = useRef<HTMLInputElement | null>(null);
  const { showError, showSuccess } = useToast();

  const load = async () => {
    const [submissionData, questionData, resultData, prepareData] = await Promise.all([
      api.getSubmission(submissionId),
      api.listQuestions(examId),
      api.getResults(submissionId),
      api.getPrepareStatus(submissionId),
    ]);
    setSubmission(submissionData);
    setQuestions(questionData);
    setResults(resultData);
    setPrepareStatus(prepareData);
  };

  useEffect(() => {
    if (!submissionId || !examId) return;
    void load().catch((error) => {
      showError(error instanceof Error ? error.message : 'Failed to load marking workspace');
    });
  }, [submissionId, examId, showError]);

  const preparationByQuestion = useMemo(() => {
    const entries = prepareStatus?.questions ?? [];
    return new Map(entries.map((entry) => [entry.question_id, entry]));
  }, [prepareStatus]);

  const questionRows = useMemo(() => {
    return questions.map((question) => {
      const transcription = results?.transcriptions.find((item) => item.question_id === question.id) ?? null;
      const grade = results?.grades.find((item) => item.question_id === question.id) ?? null;
      const prep = preparationByQuestion.get(question.id) ?? null;
      const objectiveCodes = Array.isArray(question.rubric_json?.objective_codes)
        ? question.rubric_json.objective_codes.map((item) => String(item))
        : [];
      const teacherNote = grade?.feedback_json?.teacher_note;
      const prepReasons = prep?.flagged_reasons ?? [];
      const blockingReasons = prep?.blocking_reasons ?? [];
      const requiresAttention = !grade || grade.model_name !== 'teacher_manual' || prepReasons.length > 0 || blockingReasons.length > 0;
      return {
        question,
        transcription,
        grade,
        prep,
        prepReasons,
        blockingReasons,
        objectiveCodes,
        teacherNote: typeof teacherNote === 'string' ? teacherNote : '',
        savedMarks: grade ? String(grade.marks_awarded ?? '') : '',
        requiresAttention,
      };
    }).sort((a, b) => {
      if (a.requiresAttention !== b.requiresAttention) return a.requiresAttention ? -1 : 1;
      return a.question.id - b.question.id;
    });
  }, [preparationByQuestion, questions, results]);

  const currentRow = questionRows[currentIndex];
  const completedCount = questionRows.filter((row) => !row.requiresAttention).length;
  const flaggedCount = questionRows.length - completedCount;
  const nextAttentionIndex = useMemo(
    () => questionRows.findIndex((row, index) => index > currentIndex && row.requiresAttention),
    [currentIndex, questionRows],
  );
  const nextQuestionIndex = currentIndex < questionRows.length - 1 ? currentIndex + 1 : -1;

  useEffect(() => {
    if (currentIndex >= questionRows.length) {
      setCurrentIndex(Math.max(0, questionRows.length - 1));
    }
  }, [currentIndex, questionRows.length]);

  useEffect(() => {
    if (!requestedQuestionId || questionRows.length === 0) return;
    const requestedIndex = questionRows.findIndex((row) => row.question.id === requestedQuestionId);
    if (requestedIndex >= 0 && requestedIndex !== currentIndex) {
      setCurrentIndex(requestedIndex);
    }
  }, [currentIndex, questionRows, requestedQuestionId]);

  useEffect(() => {
    if (!currentRow) return;
    setDrafts((prev) => {
      if (prev[currentRow.question.id]) return prev;
      return {
        ...prev,
        [currentRow.question.id]: {
          marks_awarded: String(currentRow.grade?.marks_awarded ?? ''),
          teacher_note: currentRow.teacherNote,
        },
      };
    });
  }, [currentRow]);

  useEffect(() => {
    if (!currentRow || saving) return;
    const timer = window.setTimeout(() => {
      markInputRef.current?.focus();
      markInputRef.current?.select();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [currentRow?.question.id, saving]);

  const currentDraft = currentRow ? drafts[currentRow.question.id] : undefined;
  const totalAwarded = useMemo(() => questionRows.reduce((sum, row) => sum + Number(row.grade?.marks_awarded || 0), 0), [questionRows]);
  const dirtyByQuestion = useMemo(() => {
    const entries = questionRows.map((row) => {
      const draft = drafts[row.question.id];
      const dirty = Boolean(draft) && (draft.marks_awarded !== row.savedMarks || draft.teacher_note !== row.teacherNote);
      return [row.question.id, dirty] as const;
    });
    return new Map(entries);
  }, [drafts, questionRows]);
  const currentIsDirty = currentRow ? dirtyByQuestion.get(currentRow.question.id) === true : false;
  const totalPossible = useMemo(() => questionRows.reduce((sum, row) => sum + Number(row.question.max_marks || 0), 0), [questionRows]);

  const updateDraft = (patch: Partial<MarkingDraft>) => {
    if (!currentRow) return;
    setDrafts((prev) => ({
      ...prev,
      [currentRow.question.id]: {
        marks_awarded: prev[currentRow.question.id]?.marks_awarded ?? '',
        teacher_note: prev[currentRow.question.id]?.teacher_note ?? '',
        ...patch,
      },
    }));
  };

  const moveToIndex = (index: number) => {
    setCurrentIndex(Math.max(0, Math.min(questionRows.length - 1, index)));
  };

  const moveBy = (delta: number) => {
    setCurrentIndex((idx) => Math.max(0, Math.min(questionRows.length - 1, idx + delta)));
  };

  const moveToNextAttention = () => {
    if (nextAttentionIndex >= 0) {
      setCurrentIndex(nextAttentionIndex);
      return true;
    }
    return false;
  };

  const saveCurrent = async (goNext = false) => {
    if (!currentRow || !currentDraft) return;
    const rawMarks = Number(currentDraft.marks_awarded);
    const maxMarks = Number(currentRow.question.max_marks || 0);
    if (!Number.isFinite(rawMarks) || rawMarks < 0 || rawMarks > maxMarks) {
      showError(`Enter a mark between 0 and ${maxMarks}.`);
      return;
    }

    try {
      setSaving(true);
      const saved = await api.saveManualGrade(submissionId, currentRow.question.id, {
        marks_awarded: rawMarks,
        teacher_note: currentDraft.teacher_note,
      });
      setResults((prev) => mergeSavedGrade(prev, submissionId, saved));
      showSuccess(`Saved ${currentRow.question.label}.`);
      if (goNext) {
        if (!moveToNextAttention() && nextQuestionIndex >= 0) {
          moveToIndex(nextQuestionIndex);
        }
      }
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to save mark');
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!currentRow || saving) return;
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName?.toLowerCase();
      const isTextarea = tagName === 'textarea';
      const isInput = tagName === 'input';
      const isEditable = Boolean(target?.isContentEditable) || isTextarea || isInput;

      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        void saveCurrent(true);
        return;
      }

      if (isTextarea || Boolean(event.altKey) || Boolean(event.shiftKey) || Boolean(event.metaKey) || Boolean(event.ctrlKey)) {
        return;
      }

      if (event.key === '[') {
        event.preventDefault();
        moveBy(-1);
        return;
      }

      if (event.key === ']') {
        event.preventDefault();
        moveBy(1);
        return;
      }

      if (event.key.toLowerCase() === 'j') {
        event.preventDefault();
        if (!moveToNextAttention() && nextQuestionIndex >= 0) {
          moveToIndex(nextQuestionIndex);
        }
        return;
      }

      if (isEditable) {
        return;
      }

      if (/^[0-9]$/.test(event.key)) {
        event.preventDefault();
        updateDraft({ marks_awarded: event.key });
        markInputRef.current?.focus();
        markInputRef.current?.select();
        return;
      }

      if (event.key === '.' && currentDraft?.marks_awarded && !currentDraft.marks_awarded.includes('.')) {
        event.preventDefault();
        updateDraft({ marks_awarded: `${currentDraft.marks_awarded}.` });
        markInputRef.current?.focus();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [currentDraft?.marks_awarded, currentRow, nextAttentionIndex, nextQuestionIndex, saving]);

  const prepareMissingAssets = async () => {
    try {
      setPreparing(true);
      const status = await api.prepareSubmission(submissionId);
      setPrepareStatus(status);
      const [submissionData, resultData] = await Promise.all([
        api.getSubmission(submissionId),
        api.getResults(submissionId),
      ]);
      setSubmission(submissionData);
      setResults(resultData);
      if (status.ready_for_marking) {
        showSuccess(status.actions_run.length > 0 ? `Prepared: ${status.actions_run.join(' → ')}` : 'Submission is ready for marking.');
      } else if (status.actions_run.length > 0) {
        showError(`Prepared what I could: ${status.actions_run.join(' → ')}`);
      } else {
        showError(status.summary_reasons[0] || 'Could not prepare submission automatically.');
      }
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to prepare submission');
    } finally {
      setPreparing(false);
    }
  };

  if (!submission) return <p>Loading marking workspace…</p>;
  if (submission.capture_mode === 'front_page_totals') {
    return (
      <div className="card stack">
        <p>This submission is using the front-page totals workflow.</p>
        <p><Link to={`/submissions/${submissionId}/front-page-totals?examId=${examId}&returnTo=${encodeURIComponent(returnTo)}&returnLabel=${encodeURIComponent(returnLabel)}`}>Open front-page totals capture</Link></p>
      </div>
    );
  }
  if (!currentRow) {
    return (
      <div className="card stack">
        <p>No questions available for marking.</p>
        <p><Link to={returnTo}>{returnLabel}</Link></p>
      </div>
    );
  }

  return (
    <div className="card card--hero stack review-card review-workspace">
      <p><Link to={returnTo}>← {returnLabel}</Link></p>
      <div className="review-header-block">
        <div>
          <h1 className="page-title" style={{ fontSize: '2rem' }}>Mark submission</h1>
          <p className="subtle-text">Teacher-entered marks are shown first. Keep the key visible, enter marks quickly, and clear flagged items before finishing.</p>
        </div>
        <div className="review-summary-pills">
          <span className="review-summary-pill is-flagged">Needs entry: {flaggedCount}</span>
          <span className="review-summary-pill is-confirmed">Teacher-marked: {completedCount}</span>
          <span className="review-summary-pill status-in-progress">Total: {totalAwarded} / {totalPossible}</span>
        </div>
      </div>

      {prepareStatus && !prepareStatus.ready_for_marking && (
        <section className="card stack prepare-banner">
          <div className="prepare-banner-header">
            <div>
              <h2 style={{ margin: 0 }}>Prepare for marking</h2>
              <p className="subtle-text" style={{ margin: 0 }}>
                Ready questions: {prepareStatus.questions_ready} / {prepareStatus.questions_total}
                {prepareStatus.manual_marked_questions > 0 ? ` · Teacher-marked: ${prepareStatus.manual_marked_questions}` : ''}
              </p>
            </div>
            <button type="button" className="btn btn-primary" onClick={() => void prepareMissingAssets()} disabled={preparing || !prepareStatus.can_prepare_now}>
              {preparing ? 'Preparing…' : 'Prepare missing assets'}
            </button>
          </div>
          {prepareStatus.summary_reasons.length > 0 && (
            <ul className="prepare-reason-list">
              {prepareStatus.summary_reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          )}
          {prepareStatus.unsafe_to_retry_reasons.length > 0 && (
            <ul className="prepare-reason-list">
              {prepareStatus.unsafe_to_retry_reasons.map((reason) => <li key={reason}><strong>Unsafe to auto-retry:</strong> {reason}</li>)}
            </ul>
          )}
          {!prepareStatus.can_prepare_now && (
            <p className="warning-text" style={{ margin: 0 }}>
              {prepareStatus.unsafe_to_retry_reasons.length > 0
                ? 'Automatic prep is blocked to protect teacher work already entered.'
                : 'Automatic prep is blocked until the template/page mismatch is fixed.'}
            </p>
          )}
        </section>
      )}

      <div className="review-layout-grid review-layout-grid--wide">
        <aside className="review-queue-panel stack">
          <div>
            <strong>{submission.student_name}</strong>
            <p className="subtle-text">Question {currentIndex + 1} of {questionRows.length}</p>
            {requestedQuestionId > 0 && currentRow && (
              <p className="subtle-text" style={{ marginTop: 6 }}>
                Landed on <strong>{currentRow.question.label}</strong> from the class queue.
              </p>
            )}
          </div>
          <div className="review-shortcut-card">
            <strong>Fast keys</strong>
            <p className="subtle-text">Digits set marks · Ctrl/Cmd + Enter saves + moves · [ and ] move · J jumps to the next item needing attention</p>
          </div>
          <div className="review-queue-list">
            {questionRows.map((row, index) => (
              <button
                key={row.question.id}
                type="button"
                className={`review-queue-item${index === currentIndex ? ' is-active' : ''}${row.requiresAttention ? ' is-flagged' : ' is-confirmed'}`}
                onClick={() => setCurrentIndex(index)}
              >
                <div className="review-queue-item-header">
                  <span>{row.question.label}</span>
                  <span>{row.grade?.marks_awarded ?? '—'} / {row.question.max_marks}</span>
                </div>
                <div className="review-queue-item-meta">
                  {row.objectiveCodes.length > 0 && <span>{row.objectiveCodes.join(', ')}</span>}
                  <span>{row.grade?.model_name === 'teacher_manual' ? 'Teacher-marked' : 'Needs teacher entry'}</span>
                  {dirtyByQuestion.get(row.question.id) === true && <span className="review-inline-dirty">Unsaved draft</span>}
                </div>
                {(row.blockingReasons.length > 0 || row.prepReasons.length > 0) && (
                  <div className="prepare-inline-reasons">
                    {row.blockingReasons[0] || row.prepReasons[0]}
                  </div>
                )}
              </button>
            ))}
          </div>
        </aside>

        <div className="stack">
          {flaggedCount === 0 ? (
            <section className="review-completion-banner" aria-live="polite">
              <strong>Marking complete.</strong>
              <span>Every question in this submission is now teacher-marked. Review totals or open final results.</span>
              <Link className="btn btn-secondary" to={`/submissions/${submissionId}/results?examId=${examId}`}>Open results</Link>
            </section>
          ) : (
            <section className="review-next-action-banner" aria-live="polite">
              <div>
                <strong>Next action</strong>
                <p className="subtle-text" style={{ marginTop: '.2rem' }}>
                  {nextAttentionIndex >= 0
                    ? `After ${currentRow.question.label}, jump to ${questionRows[nextAttentionIndex]?.question.label}.`
                    : nextQuestionIndex >= 0
                      ? `After ${currentRow.question.label}, continue to ${questionRows[nextQuestionIndex]?.question.label}.`
                      : 'This is the last remaining question needing attention in the queue.'}
                </p>
              </div>
              <button type="button" className="btn btn-secondary" onClick={() => {
                if (!moveToNextAttention() && nextQuestionIndex >= 0) {
                  moveToIndex(nextQuestionIndex);
                }
              }}>
                {nextAttentionIndex >= 0 ? 'Go to next needs-entry item' : 'Go to next question'}
              </button>
              <Link className="btn btn-secondary" to={returnTo}>{returnLabel}</Link>
            </section>
          )}

          <section className="card stack review-edit-panel">
            <div className="review-focus-title-row">
              <h2 style={{ margin: 0 }}>{currentRow.question.label}</h2>
              <div className="review-status-pill-row">
                {currentIsDirty && <span className="review-status-pill is-dirty">Unsaved draft</span>}
                <span className={`review-status-pill ${currentRow.requiresAttention ? 'is-flagged' : 'is-confirmed'}`}>
                  {currentRow.blockingReasons.length > 0
                    ? 'Unsafe to auto-retry'
                    : currentRow.prepReasons.length > 0
                      ? 'Needs preparation'
                      : currentRow.requiresAttention
                        ? 'Needs teacher entry'
                        : 'Teacher-marked'}
                </span>
              </div>
            </div>
            <div className="review-question-meta-stack">
              <div>
                <p className="metric-label">Objectives</p>
                {currentRow.objectiveCodes.length > 0 ? (
                  <div className="objective-pill-wrap objective-pill-wrap--compact">
                    {currentRow.objectiveCodes.map((code) => (
                      <span key={`${currentRow.question.id}-${code}`} className="objective-pill objective-pill--emphasis">{code}</span>
                    ))}
                  </div>
                ) : (
                  <p className="subtle-text" style={{ margin: 0 }}>No objective codes configured for this question.</p>
                )}
              </div>
              <p className="subtle-text" style={{ margin: 0 }}>Max marks: {currentRow.question.max_marks}</p>
            </div>
            {(currentRow.blockingReasons.length > 0 || currentRow.prepReasons.length > 0) && (
              <div className="prepare-question-alert">
                <strong>{currentRow.blockingReasons.length > 0 ? 'Blocked:' : 'Flagged:'}</strong>
                <ul className="prepare-reason-list">
                  {currentRow.blockingReasons.map((reason) => <li key={reason}>{reason}</li>)}
                  {currentRow.prepReasons.map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              </div>
            )}
            <div className="marking-split-grid">
              <div className="stack">
                <label>Answer crop</label>
                {currentRow.prep?.has_crop ? (
                  <img
                    src={api.getCropImageUrl(submissionId, currentRow.question.id)}
                    alt={`Answer crop for ${currentRow.question.label}`}
                    className="result-crop"
                  />
                ) : (
                  <div className="review-readonly-block">No answer crop available yet.</div>
                )}
                <label>Transcription</label>
                <div className="review-readonly-block">{renderTranscription(currentRow.transcription, currentRow.prep)}</div>
              </div>
              <div className="stack">
                <label>Answer key</label>
                <div className="review-readonly-block">{String(currentRow.question.rubric_json?.answer_key || 'No answer key captured.')}</div>
                <label>Prompt</label>
                <div className="review-readonly-block">{String(currentRow.question.rubric_json?.question_text || 'No prompt text captured.')}</div>
                <img
                  src={api.getQuestionKeyVisualUrl(examId, currentRow.question.id)}
                  alt={`Key visual for ${currentRow.question.label}`}
                  className="result-crop"
                />
              </div>
            </div>

            <div className="review-field-grid">
              <label className="stack">
                Marks awarded
                <input
                  ref={markInputRef}
                  type="number"
                  min={0}
                  max={currentRow.question.max_marks}
                  step="0.5"
                  value={currentDraft?.marks_awarded ?? ''}
                  onChange={(event) => updateDraft({ marks_awarded: event.target.value })}
                />
              </label>
            </div>

            <AutoGrowTextarea
              id={`teacher-note-${currentRow.question.id}`}
              label="Teacher note"
              className="textarea-large"
              value={currentDraft?.teacher_note ?? ''}
              onChange={(event) => updateDraft({ teacher_note: event.target.value })}
            />

            <div className="actions-row review-actions-row">
              <button type="button" onClick={() => moveBy(-1)} disabled={currentIndex === 0}>Back</button>
              <button type="button" onClick={() => moveBy(1)} disabled={currentIndex >= questionRows.length - 1}>Next</button>
              <button type="button" className="btn btn-secondary" onClick={() => void saveCurrent(false)} disabled={saving}>Save mark</button>
              <button type="button" className="btn btn-primary" onClick={() => void saveCurrent(true)} disabled={saving}>{saving ? 'Saving…' : 'Save + next needs entry'}</button>
              <Link className="btn btn-secondary" to={`/submissions/${submissionId}/results?examId=${examId}`}>Open results</Link>
              <Link className="btn btn-secondary" to={returnTo}>{returnLabel}</Link>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function renderTranscription(transcription: TranscriptionRead | null, prep: SubmissionPrepareQuestionStatus | null | undefined): string {
  if (!prep?.has_transcription) return 'No transcription available yet. You can still mark from the crop and answer key.';
  if (!transcription) return 'Transcription record is missing.';
  return transcription.text || 'Transcription is empty.';
}

function mergeSavedGrade(previous: SubmissionResults | null, submissionId: number, saved: GradeResultRead): SubmissionResults {
  if (!previous) {
    return {
      submission_id: submissionId,
      capture_mode: 'question_level',
      total_score: saved.marks_awarded,
      total_possible: 0,
      objective_totals: [],
      front_page_totals: null,
      transcriptions: [],
      grades: [saved],
    };
  }

  const nextGrades = previous.grades.some((grade) => grade.question_id === saved.question_id)
    ? previous.grades.map((grade) => (grade.question_id === saved.question_id ? saved : grade))
    : [...previous.grades, saved];

  return {
    ...previous,
    total_score: nextGrades.reduce((sum, grade) => sum + grade.marks_awarded, 0),
    grades: nextGrades,
  };
}
