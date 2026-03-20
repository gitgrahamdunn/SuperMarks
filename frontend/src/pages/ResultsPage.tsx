import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { FrontPageTotals, ObjectiveTotalRead, QuestionRead, SubmissionResults } from '../types/api';

interface ResultRow {
  question: QuestionRead;
  objectiveCodes: string[];
  transcription: SubmissionResults['transcriptions'][number] | undefined;
  grade: SubmissionResults['grades'][number] | undefined;
}

interface ReportInsight {
  label: string;
  detail: string;
  tone: 'status-complete' | 'status-in-progress' | 'status-ready' | 'status-blocked';
}

function formatObjectivePercent(objective: ObjectiveTotalRead): string {
  if (!objective.max_marks) return '—';
  return `${Math.round((objective.marks_awarded / objective.max_marks) * 100)}%`;
}

function objectiveSummaryLine(objective: ObjectiveTotalRead): string {
  const questionLabel = objective.questions_count === 1 ? '1 question' : `${objective.questions_count} questions`;
  return `${objective.objective_code}: ${objective.marks_awarded}/${objective.max_marks} · ${formatObjectivePercent(objective)} · ${questionLabel}`;
}

function formatMaybeNumber(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? '—' : String(value);
}

function formatPercent(value: number | null): string {
  return value == null ? '—' : `${Math.round(value)}%`;
}

function safeNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asPrettyLines(value: unknown): string[] {
  if (value == null) return [];
  if (typeof value === 'string') {
    return value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  }
  if (Array.isArray(value)) {
    return value
      .flatMap((item) => asPrettyLines(item))
      .filter(Boolean);
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .flatMap(([key, entry]) => {
        if (entry == null || entry === '') return [];
        if (typeof entry === 'string' || typeof entry === 'number' || typeof entry === 'boolean') {
          return [`${humanizeKey(key)}: ${String(entry)}`];
        }
        if (Array.isArray(entry)) {
          const flattened = asPrettyLines(entry);
          return flattened.length > 0 ? [`${humanizeKey(key)}: ${flattened.join(' · ')}`] : [];
        }
        const nested = asPrettyLines(entry);
        return nested.length > 0 ? [`${humanizeKey(key)}: ${nested.join(' · ')}`] : [];
      })
      .filter(Boolean);
  }
  return [String(value)];
}

function humanizeKey(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function frontPageReportInsights(frontPageTotals: FrontPageTotals): ReportInsight[] {
  const insights: ReportInsight[] = [];
  const overallPercent = frontPageTotals.overall_max_marks
    ? (frontPageTotals.overall_marks_awarded / frontPageTotals.overall_max_marks) * 100
    : null;

  insights.push({
    label: 'Overall result',
    detail: overallPercent == null
      ? `${frontPageTotals.overall_marks_awarded} confirmed from the paper front page.`
      : `${frontPageTotals.overall_marks_awarded}/${frontPageTotals.overall_max_marks} confirmed (${Math.round(overallPercent)}%).`,
    tone: overallPercent != null && overallPercent >= 80 ? 'status-complete' : overallPercent != null && overallPercent < 50 ? 'status-blocked' : 'status-in-progress',
  });

  if (frontPageTotals.objective_scores.length > 0) {
    const strongestObjective = [...frontPageTotals.objective_scores]
      .filter((row) => row.max_marks != null && row.max_marks > 0)
      .sort((left, right) => (right.marks_awarded / Number(right.max_marks)) - (left.marks_awarded / Number(left.max_marks)))[0];
    const weakestObjective = [...frontPageTotals.objective_scores]
      .filter((row) => row.max_marks != null && row.max_marks > 0)
      .sort((left, right) => (left.marks_awarded / Number(left.max_marks)) - (right.marks_awarded / Number(right.max_marks)))[0];

    if (strongestObjective) {
      insights.push({
        label: 'Strongest objective/category',
        detail: `${strongestObjective.objective_code} at ${strongestObjective.marks_awarded}/${strongestObjective.max_marks} (${Math.round((strongestObjective.marks_awarded / Number(strongestObjective.max_marks)) * 100)}%).`,
        tone: 'status-complete',
      });
    }

    if (weakestObjective && weakestObjective !== strongestObjective) {
      insights.push({
        label: 'Lowest objective/category',
        detail: `${weakestObjective.objective_code} at ${weakestObjective.marks_awarded}/${weakestObjective.max_marks} (${Math.round((weakestObjective.marks_awarded / Number(weakestObjective.max_marks)) * 100)}%).`,
        tone: 'status-blocked',
      });
    }
  }

  if (frontPageTotals.teacher_note.trim()) {
    insights.push({
      label: 'Teacher note saved',
      detail: 'This submission already carries a teacher note in the confirmed front-page totals record.',
      tone: 'status-ready',
    });
  }

  return insights;
}

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
  }, [submissionId, examId, showError]);

  const rows = useMemo<ResultRow[]>(() => {
    if (!results) return [];
    return questions.map((question) => ({
      question,
      objectiveCodes: Array.isArray(question.rubric_json?.objective_codes)
        ? question.rubric_json.objective_codes.map((item) => String(item))
        : [],
      transcription: results.transcriptions.find((item) => item.question_id === question.id),
      grade: results.grades.find((item) => item.question_id === question.id),
    }));
  }, [results, questions]);

  const totalPercent = useMemo(() => {
    if (!results?.total_possible) return null;
    return Math.round((results.total_score / results.total_possible) * 100);
  }, [results]);

  const objectiveSummaryText = useMemo(() => {
    if (!results?.objective_totals.length) return 'No objective totals are available for this submission yet.';
    return results.objective_totals.map((objective) => objectiveSummaryLine(objective)).join(' | ');
  }, [results]);

  const markedQuestionCount = useMemo(
    () => rows.filter((row) => typeof row.grade?.marks_awarded === 'number').length,
    [rows],
  );

  const objectiveLeaderboard = useMemo(() => {
    if (!results?.objective_totals.length) return [];
    return [...results.objective_totals]
      .map((objective) => ({
        ...objective,
        percent: objective.max_marks ? (objective.marks_awarded / objective.max_marks) * 100 : null,
      }))
      .sort((left, right) => (right.percent ?? -1) - (left.percent ?? -1));
  }, [results]);

  const strongestObjective = objectiveLeaderboard[0] ?? null;
  const weakestObjective = objectiveLeaderboard.length > 1 ? objectiveLeaderboard[objectiveLeaderboard.length - 1] : null;

  const reportInsights = useMemo<ReportInsight[]>(() => {
    if (!results) return [];

    if (results.capture_mode === 'front_page_totals' && results.front_page_totals) {
      return frontPageReportInsights(results.front_page_totals);
    }

    const insights: ReportInsight[] = [];
    if (totalPercent != null) {
      insights.push({
        label: 'Overall result',
        detail: `${results.total_score}/${results.total_possible} across ${markedQuestionCount} marked ${markedQuestionCount === 1 ? 'question' : 'questions'} (${totalPercent}%).`,
        tone: totalPercent >= 80 ? 'status-complete' : totalPercent < 50 ? 'status-blocked' : 'status-in-progress',
      });
    }
    if (strongestObjective?.percent != null) {
      insights.push({
        label: 'Strongest objective',
        detail: `${strongestObjective.objective_code} is currently strongest at ${strongestObjective.marks_awarded}/${strongestObjective.max_marks} (${Math.round(strongestObjective.percent)}%).`,
        tone: 'status-complete',
      });
    }
    if (weakestObjective?.percent != null && weakestObjective.objective_code !== strongestObjective?.objective_code) {
      insights.push({
        label: 'Lowest objective',
        detail: `${weakestObjective.objective_code} is the current drag point at ${weakestObjective.marks_awarded}/${weakestObjective.max_marks} (${Math.round(weakestObjective.percent)}%).`,
        tone: 'status-blocked',
      });
    }
    const rowsWithTeacherNotes = rows.filter((row) => String(row.grade?.feedback_json?.teacher_note || '').trim()).length;
    if (rowsWithTeacherNotes > 0) {
      insights.push({
        label: 'Saved teacher notes',
        detail: `${rowsWithTeacherNotes} ${rowsWithTeacherNotes === 1 ? 'question carries' : 'questions carry'} saved teacher notes for later review/context.`,
        tone: 'status-ready',
      });
    }
    return insights;
  }, [markedQuestionCount, results, rows, strongestObjective, totalPercent, weakestObjective]);

  return (
    <div className="results-stack">
      <section className="card card--hero stack">
        <p style={{ margin: 0 }}><Link to={`/submissions/${submissionId}`}>← Back to Submission</Link></p>
        <div className="page-header">
          <div>
            <p className="page-eyebrow">Results review</p>
            <h1 className="page-title">Marked results</h1>
            <p className="page-subtitle">Check saved totals, objective breakdowns, and question-level details in one place.</p>
          </div>
          <div className="page-toolbar">
            <Link className="btn btn-secondary" to={results?.capture_mode === 'front_page_totals' ? `/submissions/${submissionId}/front-page-totals?examId=${examId}` : `/submissions/${submissionId}/mark?examId=${examId}`}>
              {results?.capture_mode === 'front_page_totals' ? 'Open front-page totals' : 'Open marking workspace'}
            </Link>
            <span className="status-pill status-in-progress">{results?.capture_mode === 'front_page_totals' ? 'Front-page totals' : `${rows.length} questions`}</span>
          </div>
        </div>
      </section>
      {!results && <p>Loading...</p>}
      {results && (
        <>
          <section className="card stack">
            <div className="panel-title-row">
              <div>
                <h2 className="section-title">Student summary</h2>
                <p className="subtle-text">Teacher-facing total and objective breakdown for this submission.</p>
              </div>
              <span className="status-pill status-complete">{results.total_score} / {results.total_possible}</span>
            </div>
            <div className="metric-grid">
              <article className="metric-card">
                <p className="metric-label">Total score</p>
                <p className="metric-value">{results.total_score}</p>
                <p className="metric-meta">Out of {results.total_possible}{totalPercent != null ? ` · ${totalPercent}%` : ''}</p>
              </article>
              <article className="metric-card">
                <p className="metric-label">Objectives marked</p>
                <p className="metric-value">{results.objective_totals.length}</p>
                <p className="metric-meta">Objective groups represented in this submission</p>
              </article>
              <article className="metric-card">
                <p className="metric-label">Marked questions</p>
                <p className="metric-value">{results.capture_mode === 'front_page_totals' ? '—' : markedQuestionCount}</p>
                <p className="metric-meta">{results.capture_mode === 'front_page_totals' ? 'This lane stores paper totals instead of per-question marks' : `${rows.length - markedQuestionCount} remaining without saved marks`}</p>
              </article>
              <article className="metric-card">
                <p className="metric-label">Capture lane</p>
                <p className="metric-value">{results.capture_mode === 'front_page_totals' ? 'Front page' : 'Question level'}</p>
                <p className="metric-meta">This tells you where the authoritative saved result came from</p>
              </article>
            </div>

            <div className="review-readonly-block">
              <strong>Objective summary for export/reporting</strong>
              <div className="subtle-text" style={{ marginTop: '.35rem' }}>{objectiveSummaryText}</div>
            </div>

            {reportInsights.length > 0 && (
              <div className="results-insight-grid">
                {reportInsights.map((insight) => (
                  <article key={insight.label} className="results-insight-card">
                    <div className="panel-title-row" style={{ marginBottom: '.45rem' }}>
                      <strong>{insight.label}</strong>
                      <span className={`status-pill ${insight.tone}`}>{humanizeKey(insight.label)}</span>
                    </div>
                    <p className="subtle-text">{insight.detail}</p>
                  </article>
                ))}
              </div>
            )}

            <div>
              <p className="metric-label">Objective totals</p>
              {results.objective_totals.length === 0 ? (
                <p className="subtle-text">No objective codes were configured for these questions.</p>
              ) : (
                <div className="stack" style={{ gap: '.55rem' }}>
                  <div className="objective-pill-wrap">
                    {results.objective_totals.map((objective) => (
                      <span key={objective.objective_code} className="objective-pill" title={`${objective.questions_count} question${objective.questions_count === 1 ? '' : 's'}`}>
                        {objective.objective_code}: {objective.marks_awarded}/{objective.max_marks}
                      </span>
                    ))}
                  </div>
                  <div className="dashboard-table-wrap">
                    <table className="dashboard-table">
                      <thead>
                        <tr>
                          <th>Objective</th>
                          <th>Awarded</th>
                          <th>Max</th>
                          <th>Percent</th>
                          <th>Coverage</th>
                          <th>Teacher read</th>
                        </tr>
                      </thead>
                      <tbody>
                        {objectiveLeaderboard.map((objective) => (
                          <tr key={`objective-row-${objective.objective_code}`}>
                            <td><strong>{objective.objective_code}</strong></td>
                            <td>{objective.marks_awarded}</td>
                            <td>{objective.max_marks}</td>
                            <td>{formatPercent(objective.percent)}</td>
                            <td>{objective.questions_count === 1 ? '1 question' : `${objective.questions_count} questions`}</td>
                            <td>
                              {objective.percent == null
                                ? 'No max configured'
                                : objective.percent >= 80
                                  ? 'Strong'
                                  : objective.percent >= 60
                                    ? 'Steady'
                                    : 'Needs review'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </section>

          {results.capture_mode === 'front_page_totals' && results.front_page_totals && (
            <section className="card stack">
              <div className="panel-title-row">
                <div>
                  <h2 className="section-title">Front-page confirmation</h2>
                  <p className="subtle-text">These are the confirmed totals entered from the paper front page.</p>
                </div>
                <span className="status-pill status-complete">Confirmed</span>
              </div>
              <div className="metric-grid">
                <article className="metric-card">
                  <p className="metric-label">Overall total</p>
                  <p className="metric-value">{results.front_page_totals.overall_marks_awarded}</p>
                  <p className="metric-meta">{results.front_page_totals.overall_max_marks != null ? `Out of ${results.front_page_totals.overall_max_marks}` : 'No max entered'}</p>
                </article>
                <article className="metric-card">
                  <p className="metric-label">Objective totals captured</p>
                  <p className="metric-value">{results.front_page_totals.objective_scores.length}</p>
                  <p className="metric-meta">Teacher-confirmed front-page breakdowns</p>
                </article>
                <article className="metric-card">
                  <p className="metric-label">Saved note</p>
                  <p className="metric-value">{results.front_page_totals.teacher_note.trim() ? 'Yes' : 'No'}</p>
                  <p className="metric-meta">A teacher note can preserve why this totals read was trusted or adjusted</p>
                </article>
              </div>
              {results.front_page_totals.objective_scores.length > 0 && (
                <div className="dashboard-table-wrap">
                  <table className="dashboard-table">
                    <thead>
                      <tr>
                        <th>Objective / category</th>
                        <th>Awarded</th>
                        <th>Max</th>
                        <th>Percent</th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.front_page_totals.objective_scores.map((objective) => {
                        const percent = objective.max_marks ? (objective.marks_awarded / objective.max_marks) * 100 : null;
                        return (
                          <tr key={`front-page-objective-${objective.objective_code}`}>
                            <td><strong>{objective.objective_code}</strong></td>
                            <td>{objective.marks_awarded}</td>
                            <td>{objective.max_marks ?? '—'}</td>
                            <td>{formatPercent(percent)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {results.front_page_totals.teacher_note && <div className="review-readonly-block">{results.front_page_totals.teacher_note}</div>}
            </section>
          )}

          {results.capture_mode !== 'front_page_totals' && (
            <section className="card stack">
              <div className="panel-title-row">
                <div>
                  <h2 className="section-title">Question-by-question teacher review</h2>
                  <p className="subtle-text">Readable saved outcomes first, with structured breakdowns only when they add signal.</p>
                </div>
                <span className="status-pill status-neutral">{rows.length} questions</span>
              </div>
              <div className="stack" style={{ gap: '1rem' }}>
                {rows.map((row) => {
                  const breakdownLines = asPrettyLines(row.grade?.breakdown_json);
                  const feedbackLines = asPrettyLines(row.grade?.feedback_json);
                  const teacherNote = typeof row.grade?.feedback_json?.teacher_note === 'string'
                    ? String(row.grade.feedback_json.teacher_note).trim()
                    : '';
                  const confidence = safeNumber(row.transcription?.confidence);
                  return (
                    <div className="card" key={row.question.id}>
                      <div className="panel-title-row">
                        <div className="stack" style={{ gap: '.45rem' }}>
                          <div>
                            <h2 className="section-title">{row.question.label}</h2>
                            <p className="subtle-text">Marks: {row.grade?.marks_awarded ?? 'N/A'} / {row.question.max_marks}</p>
                          </div>
                          <div>
                            <p className="metric-label">Objectives</p>
                            {row.objectiveCodes.length > 0 ? (
                              <div className="objective-pill-wrap objective-pill-wrap--compact">
                                {row.objectiveCodes.map((code) => (
                                  <span key={`${row.question.id}-${code}`} className="objective-pill objective-pill--emphasis">{code}</span>
                                ))}
                              </div>
                            ) : (
                              <p className="subtle-text" style={{ margin: 0 }}>No objective codes configured.</p>
                            )}
                          </div>
                        </div>
                        <div className="results-question-status-stack">
                          <span className={`status-pill ${typeof row.grade?.marks_awarded === 'number' ? 'status-complete' : 'status-blocked'}`}>{typeof row.grade?.marks_awarded === 'number' ? 'Marked' : 'Unmarked'}</span>
                          {confidence != null && <span className="status-pill status-neutral">OCR {Math.round(confidence * 100)}%</span>}
                        </div>
                      </div>
                      <div className="result-card-grid">
                        <div className="stack">
                          <div className="image-frame">
                            <img
                              src={api.getCropImageUrl(submissionId, row.question.id)}
                              alt={`Crop ${row.question.label}`}
                              className="result-crop"
                            />
                          </div>
                          <div className="review-readonly-block"><strong>Transcription:</strong> {row.transcription?.text ?? 'N/A'}</div>
                        </div>
                        <div className="stack">
                          <div className="results-readable-grid">
                            <div className="review-readonly-block">
                              <strong>Saved mark</strong>
                              <div className="subtle-text" style={{ marginTop: '.35rem' }}>
                                {typeof row.grade?.marks_awarded === 'number'
                                  ? `${row.grade.marks_awarded} out of ${row.question.max_marks}`
                                  : 'No saved teacher mark yet.'}
                              </div>
                            </div>
                            <div className="review-readonly-block">
                              <strong>Teacher note</strong>
                              <div className="subtle-text" style={{ marginTop: '.35rem' }}>{teacherNote || 'No teacher note saved.'}</div>
                            </div>
                          </div>
                          <div>
                            <p className="metric-label">Breakdown</p>
                            {breakdownLines.length > 0 ? (
                              <ul className="results-bullet-list">
                                {breakdownLines.map((line) => <li key={`${row.question.id}-breakdown-${line}`}>{line}</li>)}
                              </ul>
                            ) : (
                              <div className="review-readonly-block">No saved breakdown for this question.</div>
                            )}
                          </div>
                          <div>
                            <p className="metric-label">Feedback</p>
                            {feedbackLines.length > 0 ? (
                              <ul className="results-bullet-list">
                                {feedbackLines.map((line) => <li key={`${row.question.id}-feedback-${line}`}>{line}</li>)}
                              </ul>
                            ) : (
                              <div className="review-readonly-block">No saved feedback for this question.</div>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
