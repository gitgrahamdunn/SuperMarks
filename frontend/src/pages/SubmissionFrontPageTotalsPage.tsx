import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { AutoGrowTextarea } from '../components/AutoGrowTextarea';
import { useToast } from '../components/ToastProvider';
import type {
  FrontPageExtractionEvidence,
  FrontPageObjectiveScore,
  FrontPageTotalsCandidate,
  QuestionRead,
  SubmissionRead,
} from '../types/api';

type PreviewMode = 'focused' | 'full';

type FocusedPreviewRect = {
  x: number;
  y: number;
  w: number;
  h: number;
};

const frontPageCandidateValueCache = new Map<number, FrontPageTotalsCandidate>();
const frontPageCandidatePromiseCache = new Map<number, Promise<FrontPageTotalsCandidate>>();
const frontPageSubmissionValueCache = new Map<number, SubmissionRead>();
const frontPageSubmissionPromiseCache = new Map<number, Promise<SubmissionRead>>();

function getCachedFrontPageCandidates(submissionId: number): FrontPageTotalsCandidate | null {
  return frontPageCandidateValueCache.get(submissionId) ?? null;
}

async function loadFrontPageCandidatesCached(submissionId: number): Promise<FrontPageTotalsCandidate> {
  const cachedValue = frontPageCandidateValueCache.get(submissionId);
  if (cachedValue) return cachedValue;

  const pendingRequest = frontPageCandidatePromiseCache.get(submissionId);
  if (pendingRequest) return pendingRequest;

  const request = api.getFrontPageTotalsCandidates(submissionId)
    .then((payload) => {
      frontPageCandidateValueCache.set(submissionId, payload);
      frontPageCandidatePromiseCache.delete(submissionId);
      return payload;
    })
    .catch((error) => {
      frontPageCandidatePromiseCache.delete(submissionId);
      throw error;
    });

  frontPageCandidatePromiseCache.set(submissionId, request);
  return request;
}

function getCachedFrontPageSubmission(submissionId: number): SubmissionRead | null {
  return frontPageSubmissionValueCache.get(submissionId) ?? null;
}

async function loadFrontPageSubmissionCached(submissionId: number): Promise<SubmissionRead> {
  const cachedValue = frontPageSubmissionValueCache.get(submissionId);
  if (cachedValue) return cachedValue;

  const pendingRequest = frontPageSubmissionPromiseCache.get(submissionId);
  if (pendingRequest) return pendingRequest;

  const request = api.getSubmission(submissionId)
    .then((payload) => {
      frontPageSubmissionValueCache.set(submissionId, payload);
      frontPageSubmissionPromiseCache.delete(submissionId);
      return payload;
    })
    .catch((error) => {
      frontPageSubmissionPromiseCache.delete(submissionId);
      throw error;
    });

  frontPageSubmissionPromiseCache.set(submissionId, request);
  return request;
}

function buildSeededObjectiveScores(questions: QuestionRead[]): FrontPageObjectiveScore[] {
  const totals = new Map<string, { objective_code: string; max_marks: number }>();

  for (const question of questions) {
    const objectiveCodes = Array.isArray(question.rubric_json?.objective_codes)
      ? question.rubric_json.objective_codes.map((item) => String(item).trim()).filter(Boolean)
      : [];

    for (const code of objectiveCodes) {
      const existing = totals.get(code) ?? { objective_code: code, max_marks: 0 };
      existing.max_marks += Number(question.max_marks || 0);
      totals.set(code, existing);
    }
  }

  return [...totals.values()]
    .sort((a, b) => a.objective_code.localeCompare(b.objective_code, undefined, { numeric: true, sensitivity: 'base' }))
    .map((row) => ({ objective_code: row.objective_code, marks_awarded: 0, max_marks: row.max_marks || null }));
}

function parseNumeric(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatMaybeNumber(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? '—' : String(value);
}

function normalizeCode(value: string): string {
  return value.trim().toLowerCase();
}

function confidenceLabel(confidence: number | undefined): string {
  if (confidence == null) return 'No confidence';
  return `${Math.round(confidence * 100)}% confidence`;
}

function clampNormalized(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function getUsableEvidence(
  evidence: FrontPageExtractionEvidence[] | undefined,
  pageNumber: number,
): FocusedPreviewRect[] {
  if (!Array.isArray(evidence)) return [];

  return evidence
    .filter((item) => item.page_number === pageNumber)
    .map((item) => ({
      x: Number(item.x),
      y: Number(item.y),
      w: Number(item.w),
      h: Number(item.h),
    }))
    .filter((item) => (
      Number.isFinite(item.x)
      && Number.isFinite(item.y)
      && Number.isFinite(item.w)
      && Number.isFinite(item.h)
      && item.w > 0
      && item.h > 0
    ));
}

function buildFocusedPreviewRect(
  candidateTotals: FrontPageTotalsCandidate | null,
  pageNumber: number,
): FocusedPreviewRect | null {
  if (!candidateTotals) return null;

  const evidenceRects = [
    ...getUsableEvidence(candidateTotals.student_name?.evidence, pageNumber),
    ...getUsableEvidence(candidateTotals.overall_marks_awarded?.evidence, pageNumber),
    ...getUsableEvidence(candidateTotals.overall_max_marks?.evidence, pageNumber),
    ...candidateTotals.objective_scores.flatMap((row) => [
      ...getUsableEvidence(row.objective_code.evidence, pageNumber),
      ...getUsableEvidence(row.marks_awarded.evidence, pageNumber),
      ...getUsableEvidence(row.max_marks?.evidence, pageNumber),
    ]),
  ];

  if (evidenceRects.length === 0) return null;

  const minX = Math.min(...evidenceRects.map((item) => item.x));
  const minY = Math.min(...evidenceRects.map((item) => item.y));
  const maxX = Math.max(...evidenceRects.map((item) => item.x + item.w));
  const maxY = Math.max(...evidenceRects.map((item) => item.y + item.h));
  const padding = 0.04;

  const paddedMinX = clampNormalized(minX - padding);
  const paddedMinY = clampNormalized(minY - padding);
  const paddedMaxX = clampNormalized(maxX + padding);
  const paddedMaxY = clampNormalized(maxY + padding);

  return {
    x: paddedMinX,
    y: paddedMinY,
    w: Math.min(Math.max(paddedMaxX - paddedMinX, 0.12), 1 - paddedMinX),
    h: Math.min(Math.max(paddedMaxY - paddedMinY, 0.12), 1 - paddedMinY),
  };
}

function buildInitialFrontPageFormState(
  questions: QuestionRead[],
  totals: SubmissionRead['front_page_totals'] | null | undefined,
  candidateTotals: FrontPageTotalsCandidate | null,
  studentName: string,
): {
  overallMarksAwarded: string;
  overallMaxMarks: string;
  teacherNote: string;
  objectiveScores: FrontPageObjectiveScore[];
} {
  const seededObjectiveScores = buildSeededObjectiveScores(questions);
  const defaultOverallMax = questions.length > 0
    ? String(questions.reduce((sum, question) => sum + Number(question.max_marks || 0), 0))
    : '';

  const overallMarksAwarded = totals
    ? String(totals.overall_marks_awarded)
    : candidateTotals?.overall_marks_awarded?.value_text?.trim() || '';

  const overallMaxMarks = totals?.overall_max_marks != null
    ? String(totals.overall_max_marks)
    : candidateTotals?.overall_max_marks?.value_text?.trim() || defaultOverallMax;

  const teacherNote = !totals && candidateTotals?.student_name?.value_text && candidateTotals.student_name.value_text !== studentName
    ? `Extractor saw student name: ${candidateTotals.student_name.value_text}.`
    : (totals?.teacher_note || '');

  const candidateRows = candidateTotals?.objective_scores ?? [];
  const seededByCode = new Map(seededObjectiveScores.map((row) => [normalizeCode(row.objective_code), row]));
  const objectiveScores = totals?.objective_scores?.length
    ? totals.objective_scores
    : [
      ...seededObjectiveScores.map((row) => {
        const extracted = candidateRows.find((candidateRow) => normalizeCode(candidateRow.objective_code.value_text) === normalizeCode(row.objective_code));
        return {
          objective_code: row.objective_code,
          marks_awarded: extracted ? (parseNumeric(extracted.marks_awarded.value_text) ?? 0) : 0,
          max_marks: extracted?.max_marks?.value_text ? parseNumeric(extracted.max_marks.value_text) : row.max_marks,
        };
      }),
      ...candidateRows
        .filter((row) => !seededByCode.has(normalizeCode(row.objective_code.value_text)))
        .map((row) => ({
          objective_code: row.objective_code.value_text,
          marks_awarded: parseNumeric(row.marks_awarded.value_text) ?? 0,
          max_marks: row.max_marks?.value_text ? parseNumeric(row.max_marks.value_text) : null,
        })),
    ];

  return {
    overallMarksAwarded,
    overallMaxMarks,
    teacherNote,
    objectiveScores,
  };
}

export function SubmissionFrontPageTotalsPage() {
  const params = useParams();
  const submissionId = Number(params.submissionId);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const examId = Number(searchParams.get('examId'));
  const returnTo = searchParams.get('returnTo')?.trim() || `/exams/${examId}`;
  const returnLabel = searchParams.get('returnLabel')?.trim() || 'Back to Exam queue';
  const { showError, showSuccess } = useToast();

  const [submission, setSubmission] = useState<SubmissionRead | null>(null);
  const [examSubmissions, setExamSubmissions] = useState<SubmissionRead[]>([]);
  const [questions, setQuestions] = useState<QuestionRead[]>([]);
  const [candidateTotals, setCandidateTotals] = useState<FrontPageTotalsCandidate | null>(null);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [isCandidateLoading, setIsCandidateLoading] = useState(true);
  const [candidateLoadSeconds, setCandidateLoadSeconds] = useState(0);
  const [allowManualReviewWithoutCandidates, setAllowManualReviewWithoutCandidates] = useState(false);
  const [studentNameInput, setStudentNameInput] = useState('');
  const [overallMarksAwarded, setOverallMarksAwarded] = useState('');
  const [overallMaxMarks, setOverallMaxMarks] = useState('');
  const [teacherNote, setTeacherNote] = useState('');
  const [objectiveScores, setObjectiveScores] = useState<FrontPageObjectiveScore[]>([]);
  const [selectedPageNumber, setSelectedPageNumber] = useState(1);
  const [previewMode, setPreviewMode] = useState<PreviewMode>('focused');
  const [saving, setSaving] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  useEffect(() => {
    if (!submissionId || !examId) return;
    let cancelled = false;

    const loadWorkspace = async () => {
      try {
        const cachedSubmission = getCachedFrontPageSubmission(submissionId);
        if (cachedSubmission) {
          const initialState = buildInitialFrontPageFormState(questions, cachedSubmission.front_page_totals, null, cachedSubmission.student_name);
          setSubmission(cachedSubmission);
          setStudentNameInput(cachedSubmission.student_name);
          setOverallMarksAwarded(initialState.overallMarksAwarded);
          setOverallMaxMarks(initialState.overallMaxMarks);
          setTeacherNote(initialState.teacherNote);
          setObjectiveScores(initialState.objectiveScores);
          setSelectedPageNumber((current) => (
            cachedSubmission.pages.some((page) => page.page_number === current)
              ? current
              : (cachedSubmission.pages[0]?.page_number ?? 1)
          ));
        }

        const [submissionData, questionData, submissionRows] = await Promise.all([
          loadFrontPageSubmissionCached(submissionId),
          api.listQuestions(examId),
          api.listExamSubmissions(examId),
        ]);
        if (cancelled) return;

        const initialState = buildInitialFrontPageFormState(questionData, submissionData.front_page_totals, null, submissionData.student_name);
        setSubmission(submissionData);
        setExamSubmissions(submissionRows);
        setQuestions(questionData);
        setStudentNameInput(submissionData.student_name);
        setOverallMarksAwarded(initialState.overallMarksAwarded);
        setOverallMaxMarks(initialState.overallMaxMarks);
        setTeacherNote(initialState.teacherNote);
        setObjectiveScores(initialState.objectiveScores);
        setCandidateTotals(null);
        setCandidateError(null);
        setSelectedPageNumber((current) => (
          submissionData.pages.some((page) => page.page_number === current)
            ? current
            : (submissionData.pages[0]?.page_number ?? 1)
        ));
      } catch (error) {
        if (cancelled) return;
        showError(error instanceof Error ? error.message : 'Failed to load front-page totals');
      }
    };

    void loadWorkspace();
    return () => {
      cancelled = true;
    };
  }, [examId, showError, submissionId]);

  useEffect(() => {
    if (!submission) return;
    let cancelled = false;

    const loadCandidates = async () => {
      if (submission.front_page_totals?.confirmed) {
        setIsCandidateLoading(false);
        setCandidateLoadSeconds(0);
        setCandidateError(null);
        setCandidateTotals(null);
        setAllowManualReviewWithoutCandidates(false);
        return;
      }

      try {
        const cachedCandidate = getCachedFrontPageCandidates(submissionId);
        if (cachedCandidate) {
          setCandidateTotals(cachedCandidate);
          setCandidateError(null);
          setIsCandidateLoading(false);
          setCandidateLoadSeconds(0);
          setAllowManualReviewWithoutCandidates(false);
          if (!submission.front_page_totals) {
            const nextState = buildInitialFrontPageFormState(questions, submission.front_page_totals, cachedCandidate, submission.student_name);
            setOverallMarksAwarded(nextState.overallMarksAwarded);
            setOverallMaxMarks(nextState.overallMaxMarks);
            setTeacherNote(nextState.teacherNote);
            setObjectiveScores(nextState.objectiveScores);
          }
          return;
        }

        setIsCandidateLoading(true);
        setCandidateLoadSeconds(0);
        setCandidateError(null);
        setCandidateTotals(null);
        setAllowManualReviewWithoutCandidates(false);
        const candidateData = await loadFrontPageCandidatesCached(submissionId);
        if (cancelled) return;
        setCandidateTotals(candidateData);
        if (!submission.front_page_totals) {
          const nextState = buildInitialFrontPageFormState(questions, submission.front_page_totals, candidateData, submission.student_name);
          setOverallMarksAwarded(nextState.overallMarksAwarded);
          setOverallMaxMarks(nextState.overallMaxMarks);
          setTeacherNote(nextState.teacherNote);
          setObjectiveScores(nextState.objectiveScores);
        }
      } catch (error) {
        if (cancelled) return;
        setCandidateError(error instanceof Error ? error.message : 'Failed to extract front-page totals candidates');
      } finally {
        if (!cancelled) {
          setIsCandidateLoading(false);
        }
      }
    };

    void loadCandidates();
    return () => {
      cancelled = true;
    };
  }, [questions, submission, submissionId]);

  useEffect(() => {
    if (!isCandidateLoading) return undefined;
    const started = Date.now();
    const intervalId = window.setInterval(() => {
      setCandidateLoadSeconds(Math.floor((Date.now() - started) / 1000));
    }, 250);
    return () => window.clearInterval(intervalId);
  }, [isCandidateLoading]);

  const savedTotals = submission?.front_page_totals ?? null;
  const currentOverallRead = parseNumeric(overallMarksAwarded);
  const currentOverallMaxRead = parseNumeric(overallMaxMarks);
  const seededObjectiveScores = useMemo(() => buildSeededObjectiveScores(questions), [questions]);
  const selectedPage = useMemo(
    () => submission?.pages.find((page) => page.page_number === selectedPageNumber) ?? submission?.pages[0] ?? null,
    [selectedPageNumber, submission?.pages],
  );
  const focusedPreviewRect = useMemo(
    () => (selectedPage ? buildFocusedPreviewRect(candidateTotals, selectedPage.page_number) : null),
    [candidateTotals, selectedPage],
  );
  const pageImageUrl = selectedPage ? api.getPageImageUrl(submissionId, selectedPage.page_number) : '';

  const frontPageSubmissions = useMemo(
    () => examSubmissions.filter((candidate) => candidate.capture_mode === 'front_page_totals'),
    [examSubmissions],
  );

  useEffect(() => {
    const pendingFrontPageSubmissionIds = frontPageSubmissions
      .filter((candidate) => candidate.id !== submissionId && !candidate.front_page_totals?.confirmed)
      .map((candidate) => candidate.id);

    if (pendingFrontPageSubmissionIds.length === 0) return;

    let cancelled = false;

    const warmQueue = async () => {
      const warmTargets = pendingFrontPageSubmissionIds.filter((candidateId) => !getCachedFrontPageCandidates(candidateId));
      if (warmTargets.length === 0 || cancelled) return;
      await Promise.allSettled(warmTargets.map((candidateId) => loadFrontPageCandidatesCached(candidateId)));
    };

    void warmQueue();
    return () => {
      cancelled = true;
    };
  }, [frontPageSubmissions, submissionId]);

  const nextFrontPageSubmission = useMemo(() => {
    if (!submission) return null;
    const currentIndex = frontPageSubmissions.findIndex((row) => row.id === submission.id);
    if (currentIndex < 0) return null;

    for (let index = currentIndex + 1; index < frontPageSubmissions.length; index += 1) {
      const candidate = frontPageSubmissions[index];
      if (!candidate.front_page_totals?.confirmed) return candidate;
    }

    return frontPageSubmissions.find((candidate) => !candidate.front_page_totals?.confirmed && candidate.id !== submission.id) ?? null;
  }, [frontPageSubmissions, submission]);

  const queueRemainingCount = useMemo(
    () => frontPageSubmissions.filter((candidate) => !candidate.front_page_totals?.confirmed).length,
    [frontPageSubmissions],
  );

  const candidateOutcomeRows = candidateTotals?.objective_scores ?? [];
  const extractedOutcomeCodes = candidateOutcomeRows.map((row) => row.objective_code.value_text.trim()).filter(Boolean);
  const configuredOutcomeCodes = seededObjectiveScores.map((row) => row.objective_code).filter(Boolean);
  const missingConfiguredOutcomes = configuredOutcomeCodes.filter((code) => !extractedOutcomeCodes.some((item) => normalizeCode(item) === normalizeCode(code)));
  const unexpectedExtractedOutcomes = extractedOutcomeCodes.filter((code) => !configuredOutcomeCodes.some((item) => normalizeCode(item) === normalizeCode(code)));
  const studentNameMismatch = Boolean(
    candidateTotals?.student_name?.value_text?.trim()
    && submission?.student_name?.trim()
    && normalizeCode(candidateTotals.student_name.value_text) !== normalizeCode(submission.student_name)
  );
  const extractedOverall = parseNumeric(candidateTotals?.overall_marks_awarded?.value_text ?? '');
  const extractedOverallMax = parseNumeric(candidateTotals?.overall_max_marks?.value_text ?? '');
  const totalMismatch = extractedOverall != null && currentOverallRead != null && extractedOverall !== currentOverallRead;
  const maxMismatch = extractedOverallMax != null && currentOverallMaxRead != null && extractedOverallMax !== currentOverallMaxRead;
  const reviewFlags = [
    studentNameMismatch ? 'Student name differs from the queued submission name.' : null,
    totalMismatch ? 'Parsed total differs from the current saved/working total.' : null,
    maxMismatch ? 'Parsed max differs from the current saved/working max.' : null,
    missingConfiguredOutcomes.length > 0 ? `Missing expected outcomes: ${missingConfiguredOutcomes.join(', ')}` : null,
    unexpectedExtractedOutcomes.length > 0 ? `Extra parsed outcomes: ${unexpectedExtractedOutcomes.join(', ')}` : null,
    ...(candidateTotals?.warnings ?? []),
  ].filter((item): item is string => Boolean(item));

  const hasPassableInterpretation = Boolean(candidateTotals || savedTotals);
  const queueRemainingAfterThis = Math.max(queueRemainingCount - (savedTotals?.confirmed ? 0 : 1), 0);
  const candidateLoadProgress = useMemo(() => {
    if (!isCandidateLoading) return 100;
    if (candidateLoadSeconds < 3) return 18 + candidateLoadSeconds * 16;
    if (candidateLoadSeconds < 10) return 60 + (candidateLoadSeconds - 3) * 4;
    if (candidateLoadSeconds < 20) return 88 + (candidateLoadSeconds - 10) * 0.4;
    return 92;
  }, [candidateLoadSeconds, isCandidateLoading]);

  useEffect(() => {
    if (previewMode === 'focused' && !focusedPreviewRect) {
      setPreviewMode('full');
    }
  }, [focusedPreviewRect, previewMode]);

  const resetCorrectionForm = () => {
    if (!submission) return;
    const nextState = buildInitialFrontPageFormState(questions, savedTotals, candidateTotals, submission.student_name);
    setStudentNameInput(submission.student_name);
    setOverallMarksAwarded(nextState.overallMarksAwarded);
    setOverallMaxMarks(nextState.overallMaxMarks);
    setTeacherNote(nextState.teacherNote);
    setObjectiveScores(nextState.objectiveScores);
    setIsEditing(false);
  };

  const updateObjective = (index: number, patch: Partial<FrontPageObjectiveScore>) => {
    setObjectiveScores((current) => current.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)));
  };

  const save = async (goNext: boolean) => {
    const normalizedStudentName = studentNameInput.trim();
    if (!normalizedStudentName) {
      showError('Enter a student name.');
      return;
    }

    const overallAwarded = Number(overallMarksAwarded);
    const overallMax = overallMaxMarks.trim() ? Number(overallMaxMarks) : null;
    if (!Number.isFinite(overallAwarded) || overallAwarded < 0) {
      showError('Enter a valid total score.');
      return;
    }
    if (overallMax !== null && (!Number.isFinite(overallMax) || overallMax < 0 || overallAwarded > overallMax)) {
      showError('Total score must be between 0 and the max score.');
      return;
    }

    const cleanedScores = objectiveScores
      .map((row) => ({
        objective_code: row.objective_code.trim(),
        marks_awarded: Number(row.marks_awarded),
        max_marks: row.max_marks == null || row.max_marks === ('' as never) ? null : Number(row.max_marks),
      }))
      .filter((row) => row.objective_code);

    if (cleanedScores.some((row) => !Number.isFinite(row.marks_awarded) || row.marks_awarded < 0)) {
      showError('Each outcome score needs a valid awarded mark.');
      return;
    }
    if (cleanedScores.some((row) => row.max_marks !== null && (!Number.isFinite(row.max_marks) || row.max_marks < 0 || row.marks_awarded > row.max_marks))) {
      showError('Outcome scores must be between 0 and their max values.');
      return;
    }

    try {
      setSaving(true);
      await api.saveFrontPageTotals(submissionId, {
        student_name: normalizedStudentName,
        overall_marks_awarded: overallAwarded,
        overall_max_marks: overallMax,
        objective_scores: cleanedScores,
        teacher_note: teacherNote,
        confirmed: true,
      });

      frontPageSubmissionValueCache.delete(submissionId);
      frontPageSubmissionPromiseCache.delete(submissionId);
      const [refreshedSubmission, refreshedExamSubmissions] = await Promise.all([
        loadFrontPageSubmissionCached(submissionId),
        api.listExamSubmissions(examId),
      ]);
      frontPageSubmissionValueCache.set(submissionId, refreshedSubmission);
      setSubmission(refreshedSubmission);
      setExamSubmissions(refreshedExamSubmissions);
      setStudentNameInput(refreshedSubmission.student_name);
      setIsEditing(false);

      const refreshedNextFrontPageSubmission = nextFrontPageSubmission
        ? refreshedExamSubmissions.find((candidate) => (
          candidate.id === nextFrontPageSubmission.id
          && candidate.capture_mode === 'front_page_totals'
          && !candidate.front_page_totals?.confirmed
        )) ?? null
        : null;

      if (goNext && refreshedNextFrontPageSubmission) {
        await Promise.allSettled([
          loadFrontPageSubmissionCached(refreshedNextFrontPageSubmission.id),
          loadFrontPageCandidatesCached(refreshedNextFrontPageSubmission.id),
        ]);
        showSuccess(`Confirmed ${refreshedSubmission.student_name}. Next up: ${refreshedNextFrontPageSubmission.student_name}.`);
        navigate(`/submissions/${refreshedNextFrontPageSubmission.id}/front-page-totals?examId=${examId}&returnTo=${encodeURIComponent(returnTo)}&returnLabel=${encodeURIComponent(returnLabel)}`);
        return;
      }

      showSuccess(goNext ? 'Validation complete. Queue finished.' : 'Front-page totals saved.');
      if (goNext && !refreshedNextFrontPageSubmission) {
        navigate(returnTo);
      }
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to save front-page totals');
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const metaOrCtrl = event.metaKey || event.ctrlKey;
      if (metaOrCtrl && event.key === 'Enter') {
        event.preventDefault();
        void save(true);
        return;
      }
      if (event.key.toLowerCase() === 'f' && !metaOrCtrl && !event.altKey && !event.shiftKey) {
        const target = event.target as HTMLElement | null;
        const isTypingTarget = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
        if (!isTypingTarget) {
          event.preventDefault();
          setIsEditing(true);
        }
      }
      if (event.key === 'Escape' && isEditing) {
        event.preventDefault();
        resetCorrectionForm();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isEditing, nextFrontPageSubmission, submission, teacherNote, studentNameInput, overallMarksAwarded, overallMaxMarks, objectiveScores]);

  if (!submission) return <p>Loading front-page totals…</p>;

  if (isCandidateLoading) {
    return (
      <div className="workflow-shell workflow-shell--compact">
        <section className="card card--hero stack">
          <p style={{ margin: 0 }}><Link to={returnTo}>← {returnLabel}</Link></p>
          <div className="page-header">
            <div>
              <p className="page-eyebrow">Front-page validation</p>
              <h1 className="page-title">{submission.student_name}</h1>
              <p className="page-subtitle">SuperMarks is reading this paper before review opens.</p>
            </div>
            <div className="page-toolbar">
              <span className="status-pill status-in-progress">Thinking</span>
            </div>
          </div>
        </section>

        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Preparing validation</h2>
              <p className="subtle-text">This will open automatically as soon as the parsed values are ready.</p>
            </div>
            <strong>{Math.round(candidateLoadProgress)}%</strong>
          </div>
          <div className="thinking-indicator" aria-label="SuperMarks is thinking">
            <span />
            <span />
            <span />
          </div>
          <div className="wizard-progress-bar" aria-hidden="true">
            <div className="wizard-progress-fill" style={{ width: `${candidateLoadProgress}%` }} />
          </div>
          <div className="review-readonly-block">
            <strong>Loading parsed values</strong>
            <p className="subtle-text" style={{ marginTop: '.35rem' }}>
              SuperMarks is extracting the student name, total score, and visible outcome scores before review begins.
            </p>
          </div>
        </section>
      </div>
    );
  }

  if (candidateError && !candidateTotals && !allowManualReviewWithoutCandidates) {
    return (
      <div className="workflow-shell workflow-shell--compact">
        <section className="card card--hero stack">
          <p style={{ margin: 0 }}><Link to={returnTo}>← {returnLabel}</Link></p>
          <div className="page-header">
            <div>
              <p className="page-eyebrow">Front-page validation</p>
              <h1 className="page-title">{submission.student_name}</h1>
              <p className="page-subtitle">Parsed values did not load for this paper.</p>
            </div>
          </div>
        </section>

        <section className="card stack">
          <div className="review-readonly-block">
            <strong>Couldn&apos;t finish parsing this paper</strong>
            <p className="subtle-text" style={{ marginTop: '.35rem' }}>{candidateError}</p>
          </div>
          <div className="actions-row" style={{ marginTop: 0 }}>
            <button type="button" className="btn btn-secondary" onClick={() => setSubmission((current) => current ? { ...current } : current)}>
              Retry parsed read
            </button>
            <button type="button" className="btn btn-primary" onClick={() => { setAllowManualReviewWithoutCandidates(true); setIsEditing(true); }}>
              Review manually anyway
            </button>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="workflow-shell workflow-shell--compact">
      <section className="card card--hero stack">
        <p style={{ margin: 0 }}><Link to={returnTo}>← {returnLabel}</Link></p>
        <div className="page-header">
          <div>
            <p className="page-eyebrow">Front-page validation</p>
            <h1 className="page-title">{submission.student_name}</h1>
            <p className="page-subtitle">Look at the paper, compare it to SuperMarks&apos; read, then pass or fail this paper in one move.</p>
          </div>
          <div className="page-toolbar">
            <span className={`status-pill ${savedTotals?.confirmed ? 'status-complete' : 'status-in-progress'}`}>
              {savedTotals?.confirmed ? 'Confirmed' : 'Waiting validation'}
            </span>
            <span className="status-pill status-neutral">
              {queueRemainingAfterThis > 0 ? `${queueRemainingAfterThis} after this` : 'Last paper in queue'}
            </span>
            {nextFrontPageSubmission && <span className="status-pill status-in-progress">Next: {nextFrontPageSubmission.student_name}</span>}
          </div>
        </div>
      </section>

      <div className="front-page-swipe-layout">
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Actual paper</h2>
              <p className="subtle-text">Keep the paper visible while you decide if the interpretation is right.</p>
            </div>
            <span className="status-pill status-neutral">
              {submission.pages.length > 0 ? `${submission.pages.length} page${submission.pages.length === 1 ? '' : 's'}` : 'No pages yet'}
            </span>
          </div>

          {submission.pages.length > 1 && (
            <div className="actions-row" style={{ marginTop: 0 }}>
              {submission.pages.map((page) => (
                <button
                  key={page.id}
                  type="button"
                  className={`btn btn-sm ${selectedPageNumber === page.page_number ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setSelectedPageNumber(page.page_number)}
                >
                  Page {page.page_number}
                </button>
              ))}
            </div>
          )}

          {selectedPage ? (
            <>
              <div className="front-page-swipe-preview-bar">
                {focusedPreviewRect ? (
                  <div className="front-page-swipe-preview-toggle" role="tablist" aria-label="Preview mode">
                    <button
                      type="button"
                      className={`btn btn-sm ${previewMode === 'focused' ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setPreviewMode('focused')}
                    >
                      Focused preview
                    </button>
                    <button
                      type="button"
                      className={`btn btn-sm ${previewMode === 'full' ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => setPreviewMode('full')}
                    >
                      Full page
                    </button>
                  </div>
                ) : (
                  <p className="subtle-text front-page-swipe-preview-note">
                    Full page shown because this paper did not return usable evidence coordinates.
                  </p>
                )}
                {focusedPreviewRect && previewMode === 'focused' && (
                  <span className="status-pill status-in-progress">Centered on parsed evidence</span>
                )}
              </div>

              {previewMode === 'focused' && focusedPreviewRect ? (
                <div className="image-frame front-page-swipe-image front-page-swipe-image--focused">
                  <div className="front-page-swipe-crop-frame">
                    <img
                      src={pageImageUrl}
                      alt={`Focused preview for page ${selectedPage.page_number} for ${submission.student_name}`}
                      className="front-page-swipe-crop-image"
                      style={{
                        width: `${100 / focusedPreviewRect.w}%`,
                        maxWidth: 'none',
                        left: `-${(focusedPreviewRect.x / focusedPreviewRect.w) * 100}%`,
                        top: `-${(focusedPreviewRect.y / focusedPreviewRect.h) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              ) : (
                <div className="image-frame front-page-swipe-image">
                  <img
                    src={pageImageUrl}
                    alt={`Page ${selectedPage.page_number} for ${submission.student_name}`}
                    style={{ maxWidth: '100%', display: 'block', borderRadius: 10 }}
                  />
                </div>
              )}
            </>
          ) : (
            <div className="review-readonly-block">No built page image yet. Add photos or a PDF, then rebuild this paper preview.</div>
          )}
        </section>

        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">SuperMarks read</h2>
              <p className="subtle-text">If this looks right, pass it. If not, fail it and correct inline.</p>
            </div>
            <span className={`status-pill ${reviewFlags.length > 0 ? 'status-flagged' : 'status-complete'}`}>
              {reviewFlags.length > 0 ? `${reviewFlags.length} review flag${reviewFlags.length === 1 ? '' : 's'}` : 'Looks aligned'}
            </span>
          </div>

          <div className="front-page-swipe-summary">
            <div className="front-page-swipe-stat">
              <span className="metric-label">Student</span>
              <strong>{submission.student_name}</strong>
              <span className="subtle-text">
                Parsed: {candidateTotals?.student_name?.value_text?.trim() || 'No parsed name'}
              </span>
            </div>
            <div className="front-page-swipe-stat">
              <span className="metric-label">Total</span>
              <strong>
                {candidateTotals?.overall_marks_awarded?.value_text || formatMaybeNumber(currentOverallRead)}
                {' / '}
                {candidateTotals?.overall_max_marks?.value_text || formatMaybeNumber(currentOverallMaxRead)}
              </strong>
              <span className="subtle-text">
                {candidateTotals?.overall_marks_awarded ? confidenceLabel(candidateTotals.overall_marks_awarded.confidence) : 'Manual fallback'}
              </span>
            </div>
          </div>

          <div className="review-readonly-block">
            <strong>Outcomes</strong>
            {candidateOutcomeRows.length > 0 ? (
              <div className="front-page-swipe-outcomes">
                {candidateOutcomeRows.map((row, index) => (
                  <div key={`candidate-outcome-${index}`} className="front-page-swipe-outcome-chip">
                    <strong>{row.objective_code.value_text || 'Outcome'}</strong>
                    <span>{row.marks_awarded.value_text || '—'} / {row.max_marks?.value_text || '—'}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="subtle-text" style={{ marginTop: '.45rem' }}>No parsed outcome rows. Use fail if you need to enter them manually.</p>
            )}
          </div>

          {candidateError && <div className="review-readonly-block">{candidateError}</div>}
          {reviewFlags.length > 0 && (
            <div className="review-readonly-block">
              <strong>Review notes</strong>
              <ul className="front-page-swipe-flags">
                {reviewFlags.map((flag) => (
                  <li key={flag}>{flag}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="front-page-swipe-actions">
            <button
              type="button"
              className="btn btn-primary front-page-swipe-pass"
              onClick={() => void save(true)}
              disabled={saving || (!isEditing && !hasPassableInterpretation)}
            >
              {saving ? 'Saving…' : 'Pass'}
            </button>
            <button
              type="button"
              className="btn btn-secondary front-page-swipe-fail"
              onClick={() => setIsEditing((current) => !current)}
              disabled={saving}
            >
              {isEditing ? 'Hide correction' : 'Fail'}
            </button>
          </div>

          <p className="subtle-text">
            Shortcut: `Cmd/Ctrl + Enter` confirms and advances. Press `F` to open correction.
          </p>

          {isEditing && (
            <div className="front-page-fail-panel stack">
              <div className="panel-title-row">
                <div>
                  <h3 className="section-title" style={{ marginBottom: 0 }}>Correction panel</h3>
                  <p className="subtle-text" style={{ margin: 0 }}>Fix the student name, outcome rows, or total, then save and continue.</p>
                </div>
                <div className="actions-row" style={{ marginTop: 0 }}>
                  <button type="button" className="btn btn-secondary btn-sm" onClick={resetCorrectionForm} disabled={saving}>
                    Reset
                  </button>
                </div>
              </div>

              <label className="stack">
                Student name
                <input value={studentNameInput} onChange={(event) => setStudentNameInput(event.target.value)} />
              </label>

              <div className="review-field-grid review-field-grid--two-up">
                <label className="stack">
                  Total score
                  <input type="number" min={0} step="0.5" value={overallMarksAwarded} onChange={(event) => setOverallMarksAwarded(event.target.value)} />
                </label>
                <label className="stack">
                  Total max
                  <input type="number" min={0} step="0.5" value={overallMaxMarks} onChange={(event) => setOverallMaxMarks(event.target.value)} />
                </label>
              </div>

              <div className="stack">
                <div className="panel-title-row">
                  <div>
                    <h3 className="section-title" style={{ marginBottom: 0 }}>Outcome scores</h3>
                    <p className="subtle-text" style={{ margin: 0 }}>Edit or add rows only when the paper shows them.</p>
                  </div>
                  <div className="actions-row" style={{ marginTop: 0 }}>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setObjectiveScores((current) => [...current, { objective_code: '', marks_awarded: 0, max_marks: null }])}
                    >
                      Add row
                    </button>
                  </div>
                </div>

                {objectiveScores.length === 0 && <p className="subtle-text">No outcome rows yet.</p>}
                {objectiveScores.map((row, index) => (
                  <div className="front-page-objective-row" key={`objective-${index}`}>
                    <input
                      placeholder="Outcome / category"
                      value={row.objective_code}
                      onChange={(event) => updateObjective(index, { objective_code: event.target.value })}
                    />
                    <input
                      type="number"
                      min={0}
                      step="0.5"
                      placeholder="Awarded"
                      value={row.marks_awarded}
                      onChange={(event) => updateObjective(index, { marks_awarded: Number(event.target.value) })}
                    />
                    <input
                      type="number"
                      min={0}
                      step="0.5"
                      placeholder="Max"
                      value={row.max_marks ?? ''}
                      onChange={(event) => updateObjective(index, { max_marks: event.target.value === '' ? null : Number(event.target.value) })}
                    />
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setObjectiveScores((current) => current.filter((_, rowIndex) => rowIndex !== index))}
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>

              <AutoGrowTextarea
                id="front-page-teacher-note"
                label="Teacher note"
                className="textarea-large"
                value={teacherNote}
                onChange={(event) => setTeacherNote(event.target.value)}
              />

              <div className="actions-row" style={{ marginTop: 0 }}>
                <button type="button" className="btn btn-secondary" onClick={() => void save(false)} disabled={saving}>
                  {saving ? 'Saving…' : 'Save correction'}
                </button>
                <button type="button" className="btn btn-primary" onClick={() => void save(true)} disabled={saving}>
                  {saving ? 'Saving…' : nextFrontPageSubmission ? 'Save correction + next' : 'Save correction + finish'}
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
