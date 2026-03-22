import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import { uploadToBlob } from '../blob/upload';
import { useToast } from '../components/ToastProvider';
import type { BulkUploadPreview, ExamDetail, ExamMarkingDashboardResponse, ExamObjectiveRead, ParseLatestResponse, QuestionRead, StoredFileRead, SubmissionDashboardRow, SubmissionRead } from '../types/api';

const PARSE_BATCH_SIZE = 3;

export function ExamDetailPage() {
  const params = useParams();
  const examId = Number(params.examId);
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [questions, setQuestions] = useState<QuestionRead[]>([]);
  const [submissions, setSubmissions] = useState<SubmissionRead[]>([]);
  const [markingDashboard, setMarkingDashboard] = useState<ExamMarkingDashboardResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [keyFiles, setKeyFiles] = useState<StoredFileRead[]>([]);
  const [latestParse, setLatestParse] = useState<ParseLatestResponse['job'] | null>(null);
  const [latestParseStatus, setLatestParseStatus] = useState<Awaited<ReturnType<typeof api.getExamKeyParseStatus>> | null>(null);
  const [isProcessingParse, setIsProcessingParse] = useState(false);
  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [rosterText, setRosterText] = useState('');
  const [preview, setPreview] = useState<BulkUploadPreview | null>(null);
  const [activeCandidateId, setActiveCandidateId] = useState<string>('');
  const [isUploading, setIsUploading] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isExportingSummary, setIsExportingSummary] = useState(false);
  const [isExportingObjectivesSummary, setIsExportingObjectivesSummary] = useState(false);
  const [isExportingStudentSummaries, setIsExportingStudentSummaries] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const { showError, showSuccess, showWarning } = useToast();

  const loadDetail = async () => {
    setIsLoading(true);
    try {
      const [examDetail, fetchedQuestions, uploadedKeyFiles, examSubmissions, latestParseJob, dashboard] = await Promise.all([
        api.getExamDetail(examId),
        api.listQuestions(examId),
        api.listExamKeyFiles(examId),
        api.listExamSubmissions(examId),
        api.getExamKeyParseLatest(examId),
        api.getExamMarkingDashboard(examId),
      ]);
      setDetail(examDetail);
      setQuestions(fetchedQuestions);
      setKeyFiles(uploadedKeyFiles);
      setSubmissions(examSubmissions);
      setMarkingDashboard(dashboard);
      setLatestParse(latestParseJob.job);
      if (latestParseJob.job) {
        const status = await api.getExamKeyParseStatus(examId, latestParseJob.job.job_id);
        setLatestParseStatus(status);
      } else {
        setLatestParseStatus(null);
      }
      setNotFound(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setNotFound(true);
        setDetail(null);
        return;
      }
      showError(error instanceof Error ? error.message : 'Failed to load exam');
    } finally {
      setIsLoading(false);
    }
  };

  const refreshParseStatus = async () => {
    const latest = await api.getExamKeyParseLatest(examId);
    setLatestParse(latest.job);
    if (latest.job) {
      const status = await api.getExamKeyParseStatus(examId, latest.job.job_id);
      setLatestParseStatus(status);
    } else {
      setLatestParseStatus(null);
    }
    return latest.job;
  };

  const scrollToKeyFiles = () => {
    document.getElementById('answer-key-files')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  useEffect(() => {
    if (examId) {
      void loadDetail();
    }
  }, [examId]);

  useEffect(() => {
    if (!isUploading) return;
    const started = Date.now();
    const id = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - started) / 1000));
    }, 250);
    return () => window.clearInterval(id);
  }, [isUploading]);

  useEffect(() => {
    if (!latestParse || latestParse.status !== 'running') return;
    const id = window.setInterval(() => {
      void refreshParseStatus();
    }, 2500);
    return () => window.clearInterval(id);
  }, [examId, latestParse?.job_id, latestParse?.status]);

  const activeCandidate = useMemo(
    () => preview?.candidates.find((candidate) => candidate.candidate_id === activeCandidateId) ?? preview?.candidates[0] ?? null,
    [preview, activeCandidateId],
  );

  const hasQuestions = questions.length > 0;
  const parseDone = latestParse?.status === 'done';
  const parseHasFailedPages = (latestParse?.failed_pages.length || 0) > 0;
  const parseHasPendingPages = (latestParse?.pending_pages.length || 0) > 0;
  const flaggedPages = latestParseStatus?.pages.filter((page) => page.should_escalate || page.status === 'failed') ?? [];
  const flaggedPageNumbers = useMemo(() => flaggedPages.map((page) => page.page_number), [flaggedPages]);
  const firstFlaggedPageNumber = flaggedPageNumbers[0] ?? null;
  const blockedRows = useMemo(() => markingDashboard?.submissions.filter((row) => row.workflow_status === 'blocked') ?? [], [markingDashboard]);
  const inProgressRows = useMemo(() => markingDashboard?.submissions.filter((row) => row.workflow_status === 'in_progress') ?? [], [markingDashboard]);
  const readyRows = useMemo(() => markingDashboard?.submissions.filter((row) => row.workflow_status === 'ready') ?? [], [markingDashboard]);
  const frontPageRows = useMemo(() => markingDashboard?.submissions.filter((row) => row.capture_mode === 'front_page_totals') ?? [], [markingDashboard]);
  const questionLevelRows = useMemo(() => markingDashboard?.submissions.filter((row) => row.capture_mode !== 'front_page_totals') ?? [], [markingDashboard]);
  const buildSubmissionWorkflowLink = (submissionId: number, captureMode: string, questionId?: number | null) => {
    const base = captureMode === 'front_page_totals'
      ? `/submissions/${submissionId}/front-page-totals`
      : `/submissions/${submissionId}/mark`;
    return `${base}?examId=${examId}${captureMode !== 'front_page_totals' && questionId ? `&questionId=${questionId}` : ''}&returnTo=${encodeURIComponent(`/exams/${examId}`)}&returnLabel=${encodeURIComponent('Back to exam queue')}`;
  };
  const buildSubmissionResultsLink = (submissionId: number) => `/submissions/${submissionId}/results?examId=${examId}`;

  const examNextAction = useMemo(() => {
    if (firstFlaggedPageNumber) {
      return {
        title: `Review flagged key page ${firstFlaggedPageNumber}`,
        detail: 'Answer-key review is still blocking clean class marking. Clear the flagged parse item first.',
        href: `/exams/${examId}/review?page=${firstFlaggedPageNumber}`,
        cta: 'Open flagged key review',
        tone: 'status-blocked',
      };
    }

    const blockedRow = blockedRows[0];
    if (blockedRow) {
      return {
        title: `Unblock ${blockedRow.student_name}`,
        detail: blockedRow.next_action || blockedRow.summary_reasons[0] || 'Open the blocked submission and clear the first flagged question.',
        href: buildSubmissionWorkflowLink(blockedRow.submission_id, blockedRow.capture_mode, blockedRow.next_question_id),
        cta: blockedRow.next_return_point ? `Open ${blockedRow.next_return_point}` : 'Open blocker',
        tone: 'status-blocked',
      };
    }

    const inProgressRow = inProgressRows[0];
    if (inProgressRow) {
      return {
        title: `Resume ${inProgressRow.student_name}`,
        detail: inProgressRow.next_action || 'Continue the next question that still needs teacher entry.',
        href: buildSubmissionWorkflowLink(inProgressRow.submission_id, inProgressRow.capture_mode, inProgressRow.next_question_id),
        cta: inProgressRow.next_return_point ? `Resume at ${inProgressRow.next_return_point}` : 'Resume marking',
        tone: 'status-in-progress',
      };
    }

    const readyRow = readyRows[0];
    if (readyRow) {
      return {
        title: `Start ${readyRow.student_name}`,
        detail: readyRow.next_action || 'This submission is prepared and ready for teacher marking.',
        href: buildSubmissionWorkflowLink(readyRow.submission_id, readyRow.capture_mode, readyRow.next_question_id),
        cta: readyRow.next_return_point ? `Start at ${readyRow.next_return_point}` : 'Open marking',
        tone: 'status-ready',
      };
    }

    return null;
  }, [blockedRows, examId, firstFlaggedPageNumber, inProgressRows, readyRows]);

  const laneSummaries = useMemo(() => {
    const buildLaneSummary = (rows: NonNullable<typeof markingDashboard>['submissions'], mode: 'front_page_totals' | 'question_level') => {
      const blocked = rows.filter((row) => row.workflow_status === 'blocked');
      const inProgress = rows.filter((row) => row.workflow_status === 'in_progress');
      const ready = rows.filter((row) => row.workflow_status === 'ready');
      const complete = rows.filter((row) => row.workflow_status === 'complete');
      const queued = rows.filter((row) => row.workflow_status !== 'complete');
      const nextRow = blocked[0] ?? inProgress[0] ?? ready[0] ?? complete[0] ?? null;
      const completionPercent = rows.length ? Math.round((complete.length / rows.length) * 100) : 0;
      return {
        mode,
        rows,
        blocked,
        inProgress,
        ready,
        complete,
        queued,
        nextRow,
        completionPercent,
      };
    };

    return {
      frontPage: buildLaneSummary(frontPageRows, 'front_page_totals'),
      questionLevel: buildLaneSummary(questionLevelRows, 'question_level'),
    };
  }, [frontPageRows, markingDashboard, questionLevelRows]);

  const mixedModeQueueSummary = useMemo(() => {
    const laneSummariesWithLabels = [
      { label: 'front-page totals', summary: laneSummaries.frontPage },
      { label: 'question-level', summary: laneSummaries.questionLevel },
    ];

    const activeLaneSummaries = laneSummariesWithLabels
      .map(({ label, summary }) => summarizeLaneReportingAttention(label, summary.queued))
      .filter((value): value is string => Boolean(value));

    if (activeLaneSummaries.length > 0) {
      return activeLaneSummaries.join(' ');
    }

    return 'Both workflow lanes are complete.';
  }, [laneSummaries]);

  const commandLanes = [
    {
      title: 'Front-page totals lane',
      description: 'Fast capture for papers that already show reporting totals.',
      summary: laneSummaries.frontPage,
      ctaFallback: 'Open totals capture',
    },
  ];

  const commandCenterTone = firstFlaggedPageNumber || blockedRows.length > 0
    ? 'status-blocked'
    : inProgressRows.length > 0
      ? 'status-in-progress'
      : readyRows.length > 0
        ? 'status-ready'
        : 'status-complete';

  const commandCenterTitle = firstFlaggedPageNumber
    ? 'Answer key review is the current bottleneck'
    : blockedRows.length > 0
      ? 'A blocked submission is slowing the queue'
      : inProgressRows.length > 0
        ? 'Marking is underway'
        : readyRows.length > 0
          ? 'The class is ready for the next marking pass'
          : 'Class workflow is complete';

  const commandCenterDetail = firstFlaggedPageNumber
    ? `Clear flagged key page ${firstFlaggedPageNumber}, then return to the student lanes once the answer-key review is trustworthy again.`
    : examNextAction?.detail || mixedModeQueueSummary;

  const classResultsRows = useMemo(() => {
    if (!markingDashboard) return [];
    return [...markingDashboard.submissions]
      .map((row) => ({
        ...row,
        percentValue: row.total_possible > 0 ? (row.running_total / row.total_possible) * 100 : null,
      }))
      .sort((a, b) => {
        if (a.workflow_status === 'complete' && b.workflow_status !== 'complete') return -1;
        if (a.workflow_status !== 'complete' && b.workflow_status === 'complete') return 1;
        if ((b.percentValue ?? -1) !== (a.percentValue ?? -1)) return (b.percentValue ?? -1) - (a.percentValue ?? -1);
        return a.student_name.localeCompare(b.student_name);
      });
  }, [markingDashboard]);

  const reportReadinessSummary = useMemo(() => {
    const completed = classResultsRows.filter((row) => row.workflow_status === 'complete');
    const readyForExport = completed.length;
    const withObjectiveTotals = completed.filter((row) => row.objective_totals.length > 0).length;
    const strongestRow = completed
      .filter((row) => row.total_possible > 0)
      .sort((a, b) => (b.running_total / b.total_possible) - (a.running_total / a.total_possible))[0] ?? null;
    const attentionRow = classResultsRows.find((row) => row.workflow_status !== 'complete') ?? null;
    return {
      readyForExport,
      withObjectiveTotals,
      strongestRow,
      attentionRow,
    };
  }, [classResultsRows]);

  const objectiveReportingSummary = useMemo(() => {
    const objectives = markingDashboard?.objectives ?? [];
    if (objectives.length === 0) {
      return {
        sortedObjectives: [] as ExamObjectiveRead[],
        insightCards: [] as Array<{ label: string; value: string; detail: string; tone: string }>,
      };
    }

    const scoredObjectives = objectives.filter((objective): objective is ExamObjectiveRead & { average_percent_all_current: number } => typeof objective.average_percent_all_current === 'number');
    const lowestCurrent = [...scoredObjectives].sort((a, b) => a.average_percent_all_current - b.average_percent_all_current)[0] ?? null;
    const highestCurrent = [...scoredObjectives].sort((a, b) => b.average_percent_all_current - a.average_percent_all_current)[0] ?? null;
    const leastReady = [...objectives].sort((a, b) => {
      const aCoverage = objectiveExportReadyRatio(a);
      const bCoverage = objectiveExportReadyRatio(b);
      if (aCoverage !== bCoverage) return aCoverage - bCoverage;
      return a.objective_code.localeCompare(b.objective_code);
    })[0] ?? null;

    const sortedObjectives = [...objectives].sort((a, b) => {
      const aReady = objectiveExportReadyRatio(a);
      const bReady = objectiveExportReadyRatio(b);
      if (aReady !== bReady) return aReady - bReady;
      const aCurrent = typeof a.average_percent_all_current === 'number' ? a.average_percent_all_current : -1;
      const bCurrent = typeof b.average_percent_all_current === 'number' ? b.average_percent_all_current : -1;
      if (aCurrent !== bCurrent) return aCurrent - bCurrent;
      return a.objective_code.localeCompare(b.objective_code);
    });

    return {
      sortedObjectives,
      insightCards: [
        lowestCurrent && {
          label: 'Biggest class drag',
          value: `${lowestCurrent.objective_code} · ${displayObjectivePercent(lowestCurrent.average_percent_all_current)}`,
          detail: `${lowestCurrent.complete_submissions_with_objective}/${lowestCurrent.submissions_with_objective} export-ready · current total ${lowestCurrent.total_awarded_all_current}/${lowestCurrent.total_max_all_current}`,
          tone: 'status-blocked',
        },
        leastReady && {
          label: 'Least export-ready objective',
          value: `${leastReady.complete_submissions_with_objective}/${leastReady.submissions_with_objective} ready · ${leastReady.objective_code}`,
          detail: leastReady.incomplete_submissions_with_objective > 0
            ? `${leastReady.incomplete_submissions_with_objective} submission${leastReady.incomplete_submissions_with_objective === 1 ? '' : 's'} still need teacher completion before this objective is fully trustworthy.`
            : 'Every submission carrying this objective is export-ready.',
          tone: leastReady.incomplete_submissions_with_objective > 0 ? 'status-in-progress' : 'status-complete',
        },
        highestCurrent && {
          label: 'Strongest current objective',
          value: `${highestCurrent.objective_code} · ${displayObjectivePercent(highestCurrent.average_percent_all_current)}`,
          detail: highestCurrent.strongest_complete_student
            ? `Strongest export-ready result: ${highestCurrent.strongest_complete_student} (${displayObjectivePercent(highestCurrent.strongest_complete_percent)}).`
            : 'No export-ready result exists yet for a strongest-student comparison.',
          tone: 'status-ready',
        },
      ].filter(Boolean) as Array<{ label: string; value: string; detail: string; tone: string }>,
    };
  }, [markingDashboard]);

  const onResumeParsing = async () => {
    try {
      setIsProcessingParse(true);
      const latest = await api.getExamKeyParseLatest(examId);
      if (!latest.job) {
        showWarning('No parse job available to resume.');
        return;
      }
      let status = await api.getExamKeyParseStatus(examId, latest.job.job_id);
      while (status.status === 'running') {
        const next = await api.parseExamKeyNext(examId, latest.job.job_id, PARSE_BATCH_SIZE);
        if ((next.pages_processed || []).length === 0 || next.status !== 'running') {
          break;
        }
        status = await api.getExamKeyParseStatus(examId, latest.job.job_id);
      }
      await api.finishExamKeyParse(examId, latest.job.job_id);
      await loadDetail();
      showSuccess('Parsing resumed from the exam page.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to resume parsing');
    } finally {
      setIsProcessingParse(false);
    }
  };

  const onRetryFailedPages = async () => {
    try {
      setIsProcessingParse(true);
      const latest = await api.getExamKeyParseLatest(examId);
      if (!latest.job) {
        showWarning('No parse job available for retries.');
        return;
      }
      if (latest.job.failed_pages.length === 0) {
        showWarning('No failed pages to retry.');
        return;
      }
      for (const pageNumber of latest.job.failed_pages) {
        await api.retryExamKeyParsePage(examId, latest.job.job_id, pageNumber);
      }
      await onResumeParsing();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to retry failed pages');
    } finally {
      setIsProcessingParse(false);
    }
  };

  const onRetrySinglePage = async (pageNumber: number) => {
    try {
      setIsProcessingParse(true);
      const latest = await api.getExamKeyParseLatest(examId);
      if (!latest.job) {
        showWarning('No parse job available for retries.');
        return;
      }
      const retried = await api.retryExamKeyParsePage(examId, latest.job.job_id, pageNumber);
      setQuestions(retried.questions);
      await refreshParseStatus();
      showSuccess(
        retried.status === 'done'
          ? `Retried page ${pageNumber}.`
          : `Page ${pageNumber} retried but still needs review.`,
      );
    } catch (error) {
      showError(error instanceof Error ? error.message : `Failed to retry page ${pageNumber}`);
    } finally {
      setIsProcessingParse(false);
    }
  };

  const onUploadBulk = async () => {
    if (!bulkFile) {
      showWarning('Select a PDF file first');
      return;
    }
    setIsUploading(true);
    setElapsedSeconds(0);
    setProgressMessage('Rendering pages...');
    try {
      const nextPreview = await api.uploadBulkSubmissionsFile(examId, [bulkFile], rosterText);
      setProgressMessage(`Extracting names (${nextPreview.page_count}/${nextPreview.page_count})...`);
      setPreview(nextPreview);
      setActiveCandidateId(nextPreview.candidates[0]?.candidate_id || '');
      showSuccess(`Detected ${nextPreview.candidates.length} students`);
    } catch (error) {
      if (error instanceof ApiError) {
        showError(`Bulk upload failed: ${error.message}`);
      } else {
      showError(error instanceof Error ? error.message : 'Failed to upload bulk file');
      }
    } finally {
      setIsUploading(false);
      setProgressMessage('');
    }
  };

  const updateCandidate = (candidateId: string, patch: Partial<BulkUploadPreview['candidates'][number]>) => {
    setPreview((current) => {
      if (!current) return current;
      return {
        ...current,
        candidates: current.candidates.map((candidate) => (
          candidate.candidate_id === candidateId ? { ...candidate, ...patch } : candidate
        )),
      };
    });
  };

  const onFinalize = async () => {
    if (!preview) return;
    setIsFinalizing(true);
    try {
      const result = await api.finalizeBulkSubmissions(
        examId,
        preview.bulk_upload_id,
        preview.candidates.map((candidate) => ({
          student_name: candidate.student_name,
          page_start: Number(candidate.page_start),
          page_end: Number(candidate.page_end),
        })),
      );
      result.warnings.forEach((warning: string) => showWarning(warning));
      showSuccess(`Created ${result.submissions.length} submissions`);
      const firstSubmission = result.submissions[0];
      if (firstSubmission) {
        navigate(buildSubmissionWorkflowLink(firstSubmission.id, 'front_page_totals'));
        return;
      }
      await loadDetail();
      navigate(`/exams/${examId}`);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to finalize bulk upload');
    } finally {
      setIsFinalizing(false);
    }
  };

  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  const onExportWorkbook = async () => {
    try {
      setIsExporting(true);
      const { blob, filename } = await api.downloadExamExportWorkbook(examId);
      downloadBlob(blob, filename);
      showSuccess('Excel grade export downloaded.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export Excel file');
    } finally {
      setIsExporting(false);
    }
  };

  const onExportSummaryCsv = async () => {
    try {
      setIsExportingSummary(true);
      const { blob, filename } = await api.downloadExamSummaryCsv(examId);
      downloadBlob(blob, filename);
      showSuccess('Summary CSV export downloaded.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export summary CSV');
    } finally {
      setIsExportingSummary(false);
    }
  };

  const onExportObjectivesSummaryCsv = async () => {
    try {
      setIsExportingObjectivesSummary(true);
      const { blob, filename } = await api.downloadExamObjectivesSummaryCsv(examId);
      downloadBlob(blob, filename);
      showSuccess('Objective summary CSV export downloaded.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export objective summary CSV');
    } finally {
      setIsExportingObjectivesSummary(false);
    }
  };

  const onExportStudentSummariesZip = async () => {
    try {
      setIsExportingStudentSummaries(true);
      const { blob, filename } = await api.downloadExamStudentSummariesZip(examId);
      downloadBlob(blob, filename);
      showSuccess('Student summaries export package downloaded.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export student summaries package');
    } finally {
      setIsExportingStudentSummaries(false);
    }
  };

  if (isLoading) return <p>Loading exam details…</p>;

  if (notFound) {
    return (
      <div className="card stack">
        <h1>Exam unavailable</h1>
        <p>This exam record no longer exists.</p>
        <p>
          <Link className="btn btn-secondary" to="/">Back to Home</Link>
        </p>
      </div>
    );
  }

  if (!detail) return <p>Unable to load exam details.</p>;

  const frontPagePendingRows = frontPageRows.filter((row) => row.workflow_status !== 'complete');
  const nextTotalsRow = frontPagePendingRows[0] ?? frontPageRows[0] ?? null;
  const confirmedTotalsCount = frontPageRows.filter((row) => row.workflow_status === 'complete').length;
  const frontPageCompletionPercent = frontPageRows.length > 0
    ? Math.round((confirmedTotalsCount / frontPageRows.length) * 100)
    : 0;
  const nextTotalsSubmission = nextTotalsRow
    ? submissions.find((submission) => submission.id === nextTotalsRow.submission_id) ?? null
    : null;
  const nextTotalsPreviewPage = nextTotalsSubmission?.pages[0] ?? null;

  return (
    <div className="page-stack">
      <section className="card card--hero">
        <div className="page-header">
          <div>
            <p className="page-eyebrow">Exam workspace</p>
            <p style={{ margin: 0 }}><Link to="/">← Back to Home</Link></p>
            <h1 className="page-title">{detail.exam.name}</h1>
            <p className="page-subtitle">Review the detected student names, capture and confirm the front-page totals, then export the class table.</p>
          </div>
          <div className="page-toolbar">
            {nextTotalsRow && (
              <Link className="btn btn-primary" to={buildSubmissionWorkflowLink(nextTotalsRow.submission_id, nextTotalsRow.capture_mode, nextTotalsRow.next_question_id)}>
                Open test
              </Link>
            )}
          </div>
        </div>
        <div className="metric-grid">
          <article className="metric-card">
            <p className="metric-label">Parsed papers</p>
            <p className="metric-value">{submissions.length}</p>
            <p className="metric-meta">Student records currently loaded for review</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Need confirmation</p>
            <p className="metric-value">{frontPagePendingRows.length}</p>
            <p className="metric-meta">Papers still waiting for teacher review</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Confirmed</p>
            <p className="metric-value">{confirmedTotalsCount}</p>
            <p className="metric-meta">Papers ready for export</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Flow</p>
            <p className="metric-value">{markingDashboard ? `${markingDashboard.completion.completion_percent}%` : '—'}</p>
            <p className="metric-meta">{'Detect names -> capture totals -> confirm -> export'}</p>
          </article>
        </div>
      </section>

      {!preview && (
        <>
          <section className="card stack">
            <div className="panel-title-row">
              <div>
                <h2 className="section-title">1. Confirm names and capture totals</h2>
                <p className="subtle-text">Open the next paper, verify the detected student name, enter or confirm the front-page totals, then continue through the queue.</p>
              </div>
              {nextTotalsRow && (
                <Link className="btn btn-primary" to={buildSubmissionWorkflowLink(nextTotalsRow.submission_id, nextTotalsRow.capture_mode, nextTotalsRow.next_question_id)}>
                  Open test
                </Link>
              )}
            </div>
            {frontPageRows.length === 0 ? (
              <p className="subtle-text">No papers are waiting in the confirmation queue yet.</p>
            ) : (
              <>
                <p className="subtle-text">
                  {frontPageCompletionPercent}% complete ({confirmedTotalsCount}/{frontPageRows.length} confirmed).
                </p>
                <p className="subtle-text">
                  Opening the test resumes at the next unconfirmed paper after the last one already completed.
                </p>
              </>
            )}
          </section>

          <section className="card stack">
            <div className="panel-title-row">
              <div>
                <h2 className="section-title">2. Export class table</h2>
                <p className="subtle-text">Once confirmations are done, export the results.</p>
              </div>
            </div>
            <div className="actions-row" style={{ marginTop: 0 }}>
              <button type="button" className="btn btn-secondary" onClick={() => void onExportSummaryCsv()} disabled={isExportingSummary}>
                {isExportingSummary ? 'Exporting…' : 'Export class summary CSV'}
              </button>
              <button type="button" className="btn btn-primary" onClick={() => void onExportWorkbook()} disabled={isExporting}>
                {isExporting ? 'Exporting…' : 'Export grades Excel'}
              </button>
            </div>
          </section>
        </>
      )}

      {false && (examNextAction || markingDashboard) && (
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Command center</h2>
              <p className="subtle-text">One scan for the current bottleneck, the next useful move, and how the two capture lanes relate.</p>
            </div>
            <span className={`status-pill ${commandCenterTone}`}>Teacher workflow</span>
          </div>

          <div className="command-center-grid">
            <section className="command-priority-card">
              <div className="panel-title-row" style={{ marginBottom: '.55rem' }}>
                <div>
                  <p className="metric-label">Overall posture</p>
                  <h3 className="command-priority-title">{commandCenterTitle}</h3>
                </div>
                <span className={`status-pill ${commandCenterTone}`}>{formatWorkflowStatus(commandCenterTone.replace('status-', ''))}</span>
              </div>
              <p className="subtle-text">{commandCenterDetail}</p>
              <div className="command-center-summary-list">
                <div className="command-center-summary-item">
                  <span className="command-center-summary-label">Class completion</span>
                  <strong>{markingDashboard ? `${markingDashboard!.completion.complete_count}/${markingDashboard!.completion.total_submissions}` : '—'}</strong>
                </div>
                <div className="command-center-summary-item">
                  <span className="command-center-summary-label">Answer-key flags</span>
                  <strong>{flaggedPages.length}</strong>
                </div>
                <div className="command-center-summary-item">
                  <span className="command-center-summary-label">Active queue</span>
                  <strong>{markingDashboard ? markingDashboard!.completion.total_submissions - markingDashboard!.completion.complete_count : submissions.length}</strong>
                </div>
              </div>
              {examNextAction && (
                <div className="review-next-action-banner" aria-live="polite">
                  <div>
                    <strong>{examNextAction!.title}</strong>
                    <p className="subtle-text" style={{ marginTop: '.2rem' }}>{examNextAction!.detail}</p>
                  </div>
                  <Link className="btn btn-primary" to={examNextAction!.href}>{examNextAction!.cta}</Link>
                </div>
              )}
            </section>

            {markingDashboard && (
              <section className="command-lanes-card stack">
                <div className="panel-title-row" style={{ marginBottom: '.35rem' }}>
                  <div>
                    <p className="metric-label">Totals queue</p>
                    <h3 className="command-priority-title">How totals capture is progressing</h3>
                  </div>
                  <span className="status-pill status-neutral">Totals-first queue</span>
                </div>
                <p className="subtle-text">This workspace is currently centered on the front-page totals lane.</p>
                <div className="command-lane-grid">
                  {commandLanes.map((lane) => (
                    <article key={lane.title} className="command-lane-card">
                      <div className="panel-title-row" style={{ marginBottom: '.45rem' }}>
                        <div>
                          <strong>{lane.title}</strong>
                          <p className="subtle-text" style={{ marginTop: '.2rem' }}>{lane.description}</p>
                        </div>
                        <span className="status-pill status-neutral">{lane.summary.rows.length} total</span>
                      </div>
                      <div className="command-lane-progress">
                        <div className="command-lane-progress-bar">
                          <span style={{ width: `${lane.summary.completionPercent}%` }} />
                        </div>
                        <div className="command-lane-progress-meta">
                          <strong>{lane.summary.completionPercent}% complete</strong>
                          <span className="subtle-text">{lane.summary.complete.length}/{lane.summary.rows.length || 0} done</span>
                        </div>
                      </div>
                      <div className="inline-stat-row">
                        <span className="status-pill status-ready">Ready: {lane.summary.ready.length}</span>
                        <span className="status-pill status-blocked">Blocked: {lane.summary.blocked.length}</span>
                        <span className="status-pill status-in-progress">In progress: {lane.summary.inProgress.length}</span>
                      </div>
                      {lane.summary.nextRow ? (
                        <div className="command-lane-next-step">
                          <div>
                            <strong>Next up: {lane.summary.nextRow.student_name}</strong>
                            <p className="subtle-text" style={{ marginTop: '.2rem' }}>{reportingNextActionLabel(lane.summary.nextRow)}</p>
                          </div>
                          <Link className="btn btn-secondary" to={buildSubmissionWorkflowLink(lane.summary.nextRow.submission_id, lane.summary.nextRow.capture_mode, lane.summary.nextRow.next_question_id)}>
                            {lane.summary.nextRow.capture_mode === 'front_page_totals'
                              ? lane.ctaFallback
                              : lane.summary.nextRow.workflow_status === 'blocked'
                                ? 'Open blocker'
                                : lane.summary.nextRow.workflow_status === 'in_progress'
                                  ? 'Resume marking'
                                  : lane.summary.nextRow.workflow_status === 'ready'
                                    ? 'Start marking'
                                    : lane.ctaFallback}
                          </Link>
                        </div>
                      ) : (
                        <p className="subtle-text">No submissions in this lane yet.</p>
                      )}
                    </article>
                  ))}
                </div>
              </section>
            )}
          </div>
        </section>
      )}
      {false && (
      <div className="page-stack">
        <section className="card stack">
            <div className="review-header-block">
              <div>
                <h2 style={{ marginBottom: 4 }}>Totals dashboard</h2>
                <p className="subtle-text" style={{ margin: 0 }}>Operational view for totals confirmation progress and export readiness.</p>
              </div>
              {markingDashboard && (
                <div className="review-summary-pills">
                  <span className="review-summary-pill status-ready">Ready: {markingDashboard!.completion.ready_count}</span>
                  <span className="review-summary-pill is-flagged">Blocked: {markingDashboard!.completion.blocked_count}</span>
                  <span className="review-summary-pill status-in-progress">In progress: {markingDashboard!.completion.in_progress_count}</span>
                  <span className="review-summary-pill is-confirmed">Complete: {markingDashboard!.completion.complete_count}</span>
                </div>
              )}
            </div>
            {markingDashboard && (
              <>
                <div className="actions-row" style={{ marginTop: 0 }}>
                  <span className="subtle-text">
                    Exam completion: <strong>{markingDashboard!.completion.completion_percent}%</strong> ({markingDashboard!.completion.complete_count}/{markingDashboard!.completion.total_submissions})
                  </span>
                  <button type="button" className="btn btn-secondary" onClick={() => void onExportStudentSummariesZip()} disabled={isExportingStudentSummaries} title="Downloads plain-text and printable HTML student summaries plus a manifest CSV.">
                    {isExportingStudentSummaries ? 'Exporting…' : 'Export student summary package'}
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={() => void onExportObjectivesSummaryCsv()} disabled={isExportingObjectivesSummary} title="One row per objective with export-ready coverage, class averages, and strongest/weakest complete results.">
                    {isExportingObjectivesSummary ? 'Exporting…' : 'Export objective summary CSV'}
                  </button>
                </div>
                {markingDashboard!.submissions.length === 0 && <p className="subtle-text">No submissions yet.</p>}
                {markingDashboard!.submissions.length > 0 && (
                  <section className="queue-lane-card stack" style={{ gap: '.9rem' }}>
                    <div className="panel-title-row" style={{ marginBottom: 0 }}>
                      <div>
                        <h3 className="section-title" style={{ marginBottom: 0 }}>Class results & reporting view</h3>
                        <p className="subtle-text" style={{ margin: '.15rem 0 0' }}>Teacher-facing rollup of which student results are export-ready, what each student currently holds, and where reporting still needs attention.</p>
                      </div>
                      <span className="status-pill status-neutral">{reportReadinessSummary.readyForExport}/{classResultsRows.length} export-ready</span>
                    </div>

                    <div className="queue-lane-summary-strip">
                      <div className="queue-lane-summary-stat">
                        <span className="queue-lane-summary-label">Export-ready students</span>
                        <strong>{reportReadinessSummary.readyForExport}</strong>
                        <span className="subtle-text">teacher-complete results</span>
                      </div>
                      <div className="queue-lane-summary-stat">
                        <span className="queue-lane-summary-label">Objective-backed results</span>
                        <strong>{reportReadinessSummary.withObjectiveTotals}</strong>
                        <span className="subtle-text">complete rows with objective totals</span>
                      </div>
                      <div className="queue-lane-summary-stat">
                        <span className="queue-lane-summary-label">Strongest confirmed result</span>
                        <strong>{reportReadinessSummary.strongestRow ? reportReadinessSummary.strongestRow.student_name : '—'}</strong>
                        <span className="subtle-text">{reportReadinessSummary.strongestRow ? `${reportReadinessSummary.strongestRow.running_total}/${reportReadinessSummary.strongestRow.total_possible} · ${formatPercent(reportReadinessSummary.strongestRow.running_total, reportReadinessSummary.strongestRow.total_possible)}` : 'No complete scored result yet'}</span>
                      </div>
                      <div className="queue-lane-summary-stat queue-lane-summary-stat--next">
                        <span className="queue-lane-summary-label">Reporting attention</span>
                        <strong>{reportReadinessSummary.attentionRow ? reportReadinessSummary.attentionRow!.student_name : 'Queue clear'}</strong>
                        <span className="subtle-text">{reportReadinessSummary.attentionRow ? issuesLabel(reportReadinessSummary.attentionRow!) : 'Every submission currently has a complete result'}</span>
                      </div>
                    </div>

                    <div className="dashboard-table-wrap">
                      <table className="dashboard-table dashboard-table--queue">
                        <thead>
                          <tr>
                            <th>Student result</th>
                            <th>Total & export posture</th>
                            <th>Objective summary</th>
                            <th>Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {classResultsRows.map((row) => {
                            const exportReady = row.export_ready;
                            const exportSummary = exportReady
                              ? row.capture_mode === 'front_page_totals'
                                ? 'Confirmed front-page totals will export as the authoritative result.'
                                : 'Teacher-entered question marks are complete and ready for export.'
                              : issuesLabel(row);
                            return (
                              <tr key={`class-results-${row.submission_id}`} className="queue-row">
                                <td>
                                  <div className="dashboard-student dashboard-student--queue">
                                    <div className="dashboard-student-topline">
                                      <Link to={`/submissions/${row.submission_id}/results?examId=${examId}`}>{row.student_name}</Link>
                                      <span className="status-pill status-neutral">{workflowLaneLabel(row.capture_mode)}</span>
                                      <span className={`review-status-pill ${statusClassName(row.workflow_status)}`}>{formatWorkflowStatus(row.workflow_status)}</span>
                                    </div>
                                    <div className="dashboard-student-meta">
                                      <span className="subtle-text">{row.capture_mode === 'front_page_totals' ? 'Result comes from confirmed paper totals.' : `${row.teacher_marked_questions}/${row.questions_total} questions teacher-marked.`}</span>
                                      <span className="status-pill status-neutral">{row.marking_progress}</span>
                                    </div>
                                    <div className="dashboard-student-summary">{exportSummary}</div>
                                  </div>
                                </td>
                                <td>
                                  <div className="score-snapshot-cell queue-score-card">
                                    <div>
                                      <strong>{row.running_total} / {row.total_possible}</strong>
                                      <div className="subtle-text">{formatPercent(row.running_total, row.total_possible)} of current total</div>
                                    </div>
                                    <div className="queue-row-chip-group">
                                      <span className={`status-pill ${exportReady ? 'status-complete' : row.capture_mode === 'front_page_totals' ? 'status-ready' : row.workflow_status === 'blocked' ? 'status-blocked' : 'status-in-progress'}`}>
                                        {exportReady ? 'Export-ready' : row.capture_mode === 'front_page_totals' ? 'Totals not yet confirmed' : 'Result still in progress'}
                                      </span>
                                      <span className="status-pill status-neutral">{row.next_return_point ? `Return point: ${row.next_return_point}` : row.capture_mode === 'front_page_totals' ? 'Paper totals lane' : 'Class reporting row'}</span>
                                    </div>
                                  </div>
                                </td>
                                <td>
                                  <div className="score-snapshot-cell queue-score-card">
                                    {row.objective_totals.length === 0 ? (
                                      <span className="subtle-text">No objective totals attached yet</span>
                                    ) : (
                                      <div className="stack" style={{ gap: '.35rem' }}>
                                        <div className="subtle-text">{row.objective_totals.length} objective total{row.objective_totals.length === 1 ? '' : 's'}</div>
                                        <div className="objective-pill-wrap objective-pill-wrap--compact">
                                          {row.objective_totals.map((objective) => (
                                            <span key={`class-results-objective-${row.submission_id}-${objective.objective_code}`} className="objective-pill objective-pill--emphasis" title={`${objective.questions_count} question${objective.questions_count === 1 ? '' : 's'} · ${formatPercent(objective.marks_awarded, objective.max_marks)}`}>
                                              {objective.objective_code}: {objective.marks_awarded}/{objective.max_marks}
                                            </span>
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                </td>
                                <td>
                                  <div className="dashboard-actions dashboard-actions--queue">
                                    <Link className="btn btn-primary btn-sm" to={`/submissions/${row.submission_id}/results?examId=${examId}`}>
                                      Open results
                                    </Link>
                                    <Link className="btn btn-secondary btn-sm" to={buildSubmissionWorkflowLink(row.submission_id, row.capture_mode, row.next_question_id)}>
                                      {primaryActionLabel(row)}
                                    </Link>
                                    <p className="subtle-text queue-action-note">{exportReady ? 'Use results for teacher-readable review before or after CSV export.' : secondaryActionLabel(row)}</p>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </section>
                )}
                {markingDashboard!.objectives.length > 0 && (
                  <div className="stack" style={{ gap: '.75rem' }}>
                    <div className="panel-title-row">
                      <div>
                        <h3 className="section-title" style={{ marginBottom: 0 }}>Class objective reporting</h3>
                        <p className="subtle-text" style={{ margin: 0 }}>Teacher-readable objective health pulled from the same reporting model as the dashboard and objective summary export.</p>
                      </div>
                    </div>

                    {objectiveReportingSummary.insightCards.length > 0 && (
                      <div className="objective-reporting-card-grid">
                        {objectiveReportingSummary.insightCards.map((card) => (
                          <article key={card.label} className="objective-reporting-card">
                            <div className="panel-title-row" style={{ marginBottom: '.45rem' }}>
                              <strong>{card.label}</strong>
                              <span className={`status-pill ${card.tone}`}>{card.value}</span>
                            </div>
                            <p className="subtle-text" style={{ margin: 0 }}>{card.detail}</p>
                          </article>
                        ))}
                      </div>
                    )}

                    <div className="dashboard-table-wrap">
                      <table className="dashboard-table">
                        <thead>
                          <tr>
                            <th>Objective</th>
                            <th>Current class read</th>
                            <th>Export-ready coverage</th>
                            <th>Strongest / weakest complete</th>
                            <th>Teacher follow-up priority</th>
                            <th>Coverage</th>
                            <th>Teacher read</th>
                          </tr>
                        </thead>
                        <tbody>
                          {objectiveReportingSummary.sortedObjectives.map((objective) => {
                            const coverage = objective.questions_count === 1 ? '1 question' : `${objective.questions_count} questions`;
                            const strongestWeakest = objective.strongest_complete_student && objective.weakest_complete_student
                              ? `${objective.strongest_complete_student} (${displayObjectivePercent(objective.strongest_complete_percent)}) · ${objective.weakest_complete_student} (${displayObjectivePercent(objective.weakest_complete_percent)})`
                              : 'No export-ready comparison yet';
                            const prioritizedAttentionSubmissions = [...objective.attention_submissions].sort((left, right) => {
                              const priority = (workflowStatus: string): number => {
                                switch (workflowStatus) {
                                  case 'blocked':
                                    return 0;
                                  case 'in_progress':
                                    return 1;
                                  case 'ready':
                                    return 2;
                                  default:
                                    return 3;
                                }
                              };
                              const priorityDifference = priority(left.workflow_status) - priority(right.workflow_status);
                              if (priorityDifference !== 0) {
                                return priorityDifference;
                              }
                              return left.student_name.localeCompare(right.student_name);
                            });
                            const primaryAttentionSubmission = prioritizedAttentionSubmissions[0] ?? null;
                            const hasWeakestCompleteFollowUp = !!objective.weakest_complete_submission;
                            const followUpPrioritySummary = primaryAttentionSubmission
                              ? hasWeakestCompleteFollowUp
                                ? 'Open the first incomplete result below, then compare against the weakest complete result.'
                                : 'Open the first incomplete result below.'
                              : hasWeakestCompleteFollowUp
                                ? 'No incomplete blockers remain. Review the weakest complete result below for follow-up.'
                                : 'Every submission carrying this objective is export-ready.';
                            return (
                              <tr key={`exam-objective-${objective.objective_code}`}>
                                <td>
                                  <div className="stack" style={{ gap: '.2rem' }}>
                                    <strong>{objective.objective_code}</strong>
                                    <span className="subtle-text">Current avg {displayObjectivePercent(objective.average_percent_all_current)} · complete avg {displayObjectivePercent(objective.average_percent_complete)}</span>
                                  </div>
                                </td>
                                <td>{objective.total_awarded_all_current}/{objective.total_max_all_current}</td>
                                <td>{objective.complete_submissions_with_objective}/{objective.submissions_with_objective}</td>
                                <td>{strongestWeakest}</td>
                                <td>
                                  {prioritizedAttentionSubmissions.length === 0 && !objective.weakest_complete_submission ? (
                                    <span className="subtle-text">Every submission carrying this objective is export-ready.</span>
                                  ) : (
                                    <div className="stack" style={{ gap: '.45rem' }}>
                                      <span className="subtle-text">{followUpPrioritySummary}</span>
                                      {prioritizedAttentionSubmissions.map((submission, index) => {
                                        const priorityLabel = index === 0
                                          ? 'Open first'
                                          : submission.workflow_status === 'blocked'
                                            ? 'Next blocker'
                                            : submission.workflow_status === 'in_progress'
                                              ? 'Then resume'
                                              : 'Then start';
                                        return (
                                          <div key={`${objective.objective_code}-${submission.submission_id}`} className="stack" style={{ gap: '.15rem' }}>
                                            <div className="panel-title-row" style={{ marginBottom: 0 }}>
                                              <Link to={buildSubmissionWorkflowLink(submission.submission_id, submission.capture_mode)}>
                                                {submission.student_name}
                                              </Link>
                                              <span className={`status-pill ${index === 0 ? 'status-blocked' : 'status-neutral'}`}>{priorityLabel}</span>
                                            </div>
                                            <span className="subtle-text">
                                              {submission.workflow_status.replace('_', ' ')}
                                              {typeof submission.objective_percent === 'number' ? ` · ${displayObjectivePercent(submission.objective_percent)}` : ''}
                                              {submission.next_return_point ? ` · ${submission.next_return_point}` : ''}
                                            </span>
                                          </div>
                                        );
                                      })}
                                      {objective.weakest_complete_submission && (
                                        <div className="stack" style={{ gap: '.15rem' }}>
                                          <div className="panel-title-row" style={{ marginBottom: 0 }}>
                                            <Link to={buildSubmissionResultsLink(objective.weakest_complete_submission.submission_id)}>
                                              Weakest complete: {objective.weakest_complete_submission.student_name}
                                            </Link>
                                            <span className={`status-pill ${prioritizedAttentionSubmissions.length > 0 ? 'status-neutral' : 'status-ready'}`}>
                                              {prioritizedAttentionSubmissions.length > 0 ? 'Then review' : 'Review next'}
                                            </span>
                                          </div>
                                          <span className="subtle-text">
                                            {typeof objective.weakest_complete_submission.objective_percent === 'number'
                                              ? `${displayObjectivePercent(objective.weakest_complete_submission.objective_percent)} · compare this complete result after the incomplete work above`
                                              : prioritizedAttentionSubmissions.length > 0
                                                ? 'Compare this complete result after the incomplete work above'
                                                : 'Review this complete result for follow-up'}
                                          </span>
                                        </div>
                                      )}
                                    </div>
                                  )}
                                </td>
                                <td>{coverage}</td>
                                <td>{objective.teacher_summary}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                {markingDashboard!.submissions.length > 0 && (
                  <div className="stack" style={{ gap: '1rem' }}>
                    {[
                      { title: 'Front-page totals queue', rows: frontPageRows, description: 'Totals-confirmation papers stay grouped together so you can run that fast capture lane in one pass.', summary: laneSummaries.frontPage },
                    ].map((section) => (
                      <section key={section.title} className="queue-lane-card stack" style={{ gap: '.8rem' }}>
                        <div className="panel-title-row" style={{ marginBottom: 0 }}>
                          <div>
                            <h3 className="section-title" style={{ marginBottom: 0 }}>{section.title}</h3>
                            <p className="subtle-text" style={{ margin: '.15rem 0 0' }}>{section.description}</p>
                          </div>
                          <span className="status-pill status-neutral">{section.rows.length} submission{section.rows.length === 1 ? '' : 's'}</span>
                        </div>

                        <div className="queue-lane-summary-strip">
                          <div className="queue-lane-summary-stat">
                            <span className="queue-lane-summary-label">Lane completion</span>
                            <strong>{section.summary.completionPercent}%</strong>
                            <span className="subtle-text">{section.summary.complete.length}/{section.summary.rows.length || 0} complete</span>
                          </div>
                          <div className="queue-lane-summary-stat">
                            <span className="queue-lane-summary-label">Teacher attention</span>
                            <strong>{section.summary.queued.length}</strong>
                            <span className="subtle-text">active work items</span>
                          </div>
                          <div className="queue-lane-summary-stat">
                            <span className="queue-lane-summary-label">Status mix</span>
                            <div className="inline-stat-row">
                              <span className="status-pill status-ready">Ready: {section.summary.ready.length}</span>
                              <span className="status-pill status-blocked">Blocked: {section.summary.blocked.length}</span>
                              <span className="status-pill status-in-progress">In progress: {section.summary.inProgress.length}</span>
                            </div>
                          </div>
                          {section.summary.nextRow && (
                            <div className="queue-lane-summary-stat queue-lane-summary-stat--next">
                              <span className="queue-lane-summary-label">Next suggested item</span>
                              <strong>{section.summary.nextRow.student_name}</strong>
                              <span className="subtle-text">{section.summary.nextRow.next_return_point || primaryActionLabel(section.summary.nextRow)}</span>
                            </div>
                          )}
                        </div>

                        {section.rows.length === 0 ? (
                          <p className="subtle-text">No submissions in this lane yet.</p>
                        ) : (
                          <div className="dashboard-table-wrap">
                            <table className="dashboard-table dashboard-table--queue">
                              <thead>
                                <tr>
                                  <th>Submission work item</th>
                                  <th>Progress & issues</th>
                                  <th>Results snapshot</th>
                                  <th>Actions</th>
                                </tr>
                              </thead>
                              <tbody>
                                {section.rows.map((row) => {
                                  const submission = submissions.find((item) => item.id === row.submission_id);
                                  const currentPercent = row.total_possible > 0 ? `${Math.round((row.running_total / row.total_possible) * 100)}%` : null;
                                  const progressPercent = rowProgressPercent(row);
                                  return (
                                    <tr key={row.submission_id} className="queue-row">
                                      <td>
                                        <div className="dashboard-student dashboard-student--queue">
                                          <div className="dashboard-student-topline">
                                            <Link to={`/submissions/${row.submission_id}`}>{row.student_name}</Link>
                                            <span className="status-pill status-neutral">{workflowLaneLabel(row.capture_mode)}</span>
                                            <span className={`review-status-pill ${statusClassName(row.workflow_status)}`}>{formatWorkflowStatus(row.workflow_status)}</span>
                                          </div>
                                          <div className="dashboard-student-meta">
                                            {submission && <span className="subtle-text">Pipeline: {submission.status}</span>}
                                            {row.next_return_point && row.capture_mode !== 'front_page_totals' && <span className="subtle-text">Next focus: {row.next_return_point}</span>}
                                            <span className="status-pill status-neutral">{row.marking_progress}</span>
                                          </div>
                                          <div className="dashboard-student-summary">{progressLabel(row)}</div>
                                          <div className="queue-row-chip-group">
                                            <span className={`status-pill ${row.capture_mode === 'front_page_totals' ? (row.workflow_status === 'complete' ? 'status-complete' : 'status-ready') : row.flagged_count > 0 ? 'status-flagged' : 'status-complete'}`}>
                                              {row.capture_mode === 'front_page_totals'
                                                ? (row.workflow_status === 'complete' ? 'Totals confirmed' : 'Needs confirmation')
                                                : row.flagged_count > 0
                                                  ? `${row.flagged_count} flagged`
                                                  : 'No flagged items'}
                                            </span>
                                            {row.capture_mode !== 'front_page_totals' && (
                                              <span className={`status-pill ${row.ready_for_marking ? 'status-ready' : row.can_prepare_now ? 'status-in-progress' : 'status-blocked'}`}>
                                                {row.ready_for_marking ? 'Ready for teacher entry' : row.can_prepare_now ? 'Recoverable with prep' : 'Prep blocked'}
                                              </span>
                                            )}
                                          </div>
                                        </div>
                                      </td>
                                      <td>
                                        <div className="queue-status-cell">
                                          <div className="queue-status-heading">{queueSnapshotLabel(row)}</div>
                                          <div className="queue-row-progress">
                                            <div className="queue-row-progress-bar" aria-hidden="true">
                                              <span style={{ width: `${progressPercent}%` }} />
                                            </div>
                                            <div className="queue-row-progress-meta">
                                              <strong>{progressPercent}% workflow complete</strong>
                                              <span className="subtle-text">{progressDetailLabel(row)}</span>
                                            </div>
                                          </div>
                                          <div className="queue-status-meta">
                                            <span className="subtle-text">{issuesLabel(row)}</span>
                                          </div>
                                        </div>
                                      </td>
                                      <td>
                                        <div className="score-snapshot-cell queue-score-card">
                                          <div>
                                            <strong>{row.running_total} / {row.total_possible}</strong>
                                            <div className="subtle-text">{currentPercent ? `${currentPercent} of current total` : 'No total available yet'}</div>
                                          </div>
                                          {row.objective_totals.length === 0 ? (
                                            <span className="subtle-text">No objective totals yet</span>
                                          ) : (
                                            <div className="stack" style={{ gap: '.35rem' }}>
                                              <div className="subtle-text">{row.objective_totals.length} objective total{row.objective_totals.length === 1 ? '' : 's'}</div>
                                              <div className="objective-pill-wrap objective-pill-wrap--compact">
                                                {row.objective_totals.map((objective) => (
                                                  <span key={`${row.submission_id}-${objective.objective_code}`} className="objective-pill objective-pill--emphasis" title={`${objective.questions_count} question${objective.questions_count === 1 ? '' : 's'}`}>
                                                    {objective.objective_code}: {objective.marks_awarded}/{objective.max_marks}
                                                  </span>
                                                ))}
                                              </div>
                                            </div>
                                          )}
                                        </div>
                                      </td>
                                      <td>
                                        <div className="dashboard-actions dashboard-actions--queue">
                                          <Link className="btn btn-primary btn-sm" to={buildSubmissionWorkflowLink(row.submission_id, row.capture_mode, row.next_question_id)}>
                                            {primaryActionLabel(row)}
                                          </Link>
                                          <Link className="btn btn-secondary btn-sm" to={`/submissions/${row.submission_id}?returnTo=${encodeURIComponent(`/exams/${examId}`)}&returnLabel=${encodeURIComponent('Back to exam queue')}`}>
                                            Open submission record
                                          </Link>
                                          <p className="subtle-text queue-action-note">{secondaryActionLabel(row)}</p>
                                        </div>
                                      </td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </section>
                    ))}
                  </div>
                )}
              </>
            )}
          </section>
      </div>
      )}

    </div>
  );
}

function formatWorkflowStatus(status: string): string {
  switch (status) {
    case 'in_progress':
      return 'In progress';
    case 'complete':
    case 'done':
      return 'Complete';
    case 'ready':
      return 'Ready';
    case 'blocked':
    case 'failed':
      return 'Blocked';
    case 'neutral':
      return 'Monitoring';
    default:
      return status.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
  }
}

function statusClassName(status: string): string {
  switch (status) {
    case 'complete':
      return 'is-confirmed';
    case 'blocked':
      return 'is-flagged';
    case 'ready':
      return 'status-ready';
    case 'in_progress':
      return 'status-in-progress';
    default:
      return '';
  }
}

function workflowLaneLabel(captureMode: string): string {
  return captureMode === 'front_page_totals' ? 'Front-page totals lane' : 'Question-level lane';
}

function objectiveExportReadyRatio(objective: ExamObjectiveRead): number {
  if (objective.submissions_with_objective <= 0) return 1;
  return objective.complete_submissions_with_objective / objective.submissions_with_objective;
}

function displayObjectivePercent(value: number | '' | null | undefined): string {
  return typeof value === 'number' ? `${Math.round(value)}%` : '—';
}

function formatPercent(value: number, total: number): string {
  if (total <= 0) return '—';
  return `${Math.round((value / total) * 100)}%`;
}

function progressLabel(row: SubmissionDashboardRow): string {
  if (row.capture_mode === 'front_page_totals') {
    return row.reporting_attention || (row.workflow_status === 'complete' ? 'Every submission currently has a complete result.' : 'Front-page totals still need teacher confirmation.');
  }
  return `${row.teacher_marked_questions} of ${row.questions_total} questions teacher-marked`;
}

function summarizeLaneReportingAttention(laneLabel: string, rows: SubmissionDashboardRow[]): string | null {
  if (rows.length === 0) {
    return null;
  }

  const attentionCounts = new Map<string, number>();
  rows.forEach((row) => {
    const attention = row.reporting_attention?.trim() || issuesLabel(row);
    attentionCounts.set(attention, (attentionCounts.get(attention) || 0) + 1);
  });

  if (attentionCounts.size === 1) {
    const [[attention, count]] = [...attentionCounts.entries()];
    return `${count} ${laneLabel} submission${count === 1 ? '' : 's'}: ${attention}`;
  }

  return `${rows.length} ${laneLabel} submission${rows.length === 1 ? '' : 's'} need teacher attention before export.`;
}

function reportingNextActionLabel(row: SubmissionDashboardRow): string {
  if (row.next_action) {
    return row.next_action;
  }
  if (row.capture_mode === 'front_page_totals') {
    return 'Capture and confirm the front-page totals.';
  }
  if (row.workflow_status === 'blocked') {
    return row.next_return_point ? `Open ${row.next_return_point} to clear the blocker.` : 'Resolve the first blocked question before continuing.';
  }
  if (row.workflow_status === 'in_progress') {
    return row.next_return_point ? `Resume marking at ${row.next_return_point}.` : 'Continue the next question needing teacher entry.';
  }
  if (row.workflow_status === 'ready') {
    return row.next_return_point ? `Start marking at ${row.next_return_point}.` : 'Prepared and ready to start marking.';
  }
  return 'Review results or return to the class queue.';
}

function queueSnapshotLabel(row: SubmissionDashboardRow): string {
  return reportingNextActionLabel(row);
}

function primaryActionLabel(row: SubmissionDashboardRow): string {
  if (row.capture_mode === 'front_page_totals') {
    return row.export_ready ? 'Review confirmed totals' : 'Open totals capture';
  }
  if (row.workflow_status === 'blocked') {
    return row.next_return_point ? `Open blocker: ${row.next_return_point}` : 'Open blocker';
  }
  if (row.workflow_status === 'in_progress') {
    return row.next_return_point ? `Resume: ${row.next_return_point}` : 'Resume marking';
  }
  if (row.workflow_status === 'ready') {
    return row.next_return_point ? `Start: ${row.next_return_point}` : 'Start marking';
  }
  return 'Open marking';
}


function rowProgressPercent(row: SubmissionDashboardRow): number {
  if (row.capture_mode === 'front_page_totals') {
    return row.workflow_status === 'complete' ? 100 : 70;
  }
  if (row.questions_total <= 0) {
    return row.workflow_status === 'complete' ? 100 : 0;
  }
  return Math.max(0, Math.min(100, Math.round((row.teacher_marked_questions / row.questions_total) * 100)));
}

function progressDetailLabel(row: SubmissionDashboardRow): string {
  if (row.capture_mode === 'front_page_totals') {
    return reportingNextActionLabel(row);
  }
  if (row.workflow_status === 'complete') {
    return row.next_action || 'Review results or return to the class queue.';
  }
  if (row.next_return_point) {
    return `Current return point: ${row.next_return_point}.`;
  }
  return `${row.teacher_marked_questions} of ${row.questions_total} questions teacher-marked.`;
}

function secondaryActionLabel(row: SubmissionDashboardRow): string {
  if (row.capture_mode === 'front_page_totals') {
    return row.workflow_status === 'complete' ? 'Use the submission record to audit what was confirmed.' : 'Open the submission record if you need the underlying uploaded paper first.';
  }
  if (row.workflow_status === 'blocked') {
    return 'Open the submission record for preparation detail, missing assets, or blocker context.';
  }
  if (row.workflow_status === 'in_progress') {
    return 'Submission record shows the broader preparation trail while marking continues.';
  }
  if (row.workflow_status === 'ready') {
    return 'Submission record is the broader audit trail if you want to inspect files before starting.';
  }
  return 'Submission record stays available for audit and export context.';
}

function issuesLabel(row: SubmissionDashboardRow): string {
  if (row.reporting_attention) {
    return row.reporting_attention;
  }
  if (row.capture_mode === 'front_page_totals') {
    return row.summary_reasons[0] || 'Teacher review keeps extracted totals trustworthy before export.';
  }
  if (row.flagged_count > 0) {
    return `${row.flagged_count} flagged item${row.flagged_count === 1 ? '' : 's'} need review.`;
  }
  return row.summary_reasons[0] || 'No flagged issues currently surfaced.';
}
