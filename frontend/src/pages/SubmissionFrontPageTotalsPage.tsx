import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { AutoGrowTextarea } from '../components/AutoGrowTextarea';
import { useToast } from '../components/ToastProvider';
import type { FrontPageCandidateValue, FrontPageObjectiveScore, FrontPageTotalsCandidate, QuestionRead, SubmissionRead } from '../types/api';

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

function isBlank(value: string | null | undefined): boolean {
  return !value || !value.trim();
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

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function confidenceTone(confidence: number | undefined): string {
  if ((confidence ?? 0) >= 0.85) return 'status-complete';
  if ((confidence ?? 0) >= 0.6) return 'status-in-progress';
  return 'status-ready';
}

function confidenceLabel(confidence: number | undefined): string {
  if (confidence == null) return 'No confidence';
  const percent = Math.round(confidence * 100);
  if (confidence < 0.6) return `${percent}% low confidence`;
  return `${percent}%`;
}

function normalizeCode(value: string): string {
  return value.trim().toLowerCase();
}

function numbersMatch(left: number | null, right: number | null): boolean {
  return left != null && right != null && Math.abs(left - right) < 0.001;
}

function candidateEvidenceBlocks(candidate: FrontPageCandidateValue | null | undefined): FrontPageCandidateValue['evidence'] {
  return candidate?.evidence?.filter((item) => item.quote?.trim()) ?? [];
}

function evidenceSummary(candidate: FrontPageCandidateValue | null | undefined): string {
  const evidence = candidateEvidenceBlocks(candidate);
  if (!evidence.length) return 'No quote evidence captured';
  return evidence.map((item) => `p.${item.page_number} “${item.quote}”`).join(' · ');
}

function sumValues(values: Array<number | null | undefined>): number | null {
  const numeric = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  if (numeric.length === 0) return null;
  return Number(numeric.reduce((sum, value) => sum + value, 0).toFixed(2));
}

function mergeCandidateIntoObjectiveScores(
  currentScores: FrontPageObjectiveScore[],
  candidateTotals: FrontPageTotalsCandidate | null,
  options?: { fillExistingAwardedWhenZero?: boolean },
): FrontPageObjectiveScore[] {
  if (!candidateTotals?.objective_scores.length) return currentScores;

  const merged = [...currentScores];
  const indexByCode = new Map(
    merged
      .map((row, index) => [row.objective_code.trim().toLowerCase(), index] as const)
      .filter(([code]) => code),
  );

  for (const candidateRow of candidateTotals.objective_scores) {
    const candidateCode = candidateRow.objective_code.value_text.trim();
    if (!candidateCode) continue;

    const normalizedCode = candidateCode.toLowerCase();
    const candidateAwarded = parseNumeric(candidateRow.marks_awarded.value_text);
    const candidateMax = candidateRow.max_marks?.value_text ? parseNumeric(candidateRow.max_marks.value_text) : null;
    const existingIndex = indexByCode.get(normalizedCode);

    if (existingIndex != null) {
      const existing = merged[existingIndex];
      merged[existingIndex] = {
        ...existing,
        objective_code: existing.objective_code.trim() || candidateCode,
        marks_awarded: options?.fillExistingAwardedWhenZero && existing.marks_awarded === 0 && candidateAwarded != null
          ? candidateAwarded
          : existing.marks_awarded,
        max_marks: existing.max_marks ?? candidateMax,
      };
      continue;
    }

    merged.push({
      objective_code: candidateCode,
      marks_awarded: candidateAwarded ?? 0,
      max_marks: candidateMax,
    });
    indexByCode.set(normalizedCode, merged.length - 1);
  }

  return merged;
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

  let teacherNote = totals?.teacher_note || '';
  if (!totals && candidateTotals?.student_name?.value_text && candidateTotals.student_name.value_text !== studentName) {
    teacherNote = `Extractor saw student name: ${candidateTotals.student_name.value_text}.`;
  }

  const objectiveScores = totals?.objective_scores?.length
    ? totals.objective_scores
    : mergeCandidateIntoObjectiveScores(seededObjectiveScores, candidateTotals, { fillExistingAwardedWhenZero: true });

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
  const [submission, setSubmission] = useState<SubmissionRead | null>(null);
  const [examSubmissions, setExamSubmissions] = useState<SubmissionRead[]>([]);
  const [questions, setQuestions] = useState<QuestionRead[]>([]);
  const [overallMarksAwarded, setOverallMarksAwarded] = useState('');
  const [overallMaxMarks, setOverallMaxMarks] = useState('');
  const [teacherNote, setTeacherNote] = useState('');
  const [objectiveScores, setObjectiveScores] = useState<FrontPageObjectiveScore[]>([]);
  const [candidateTotals, setCandidateTotals] = useState<FrontPageTotalsCandidate | null>(null);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [isExportingSummary, setIsExportingSummary] = useState(false);
  const [isExportingTotals, setIsExportingTotals] = useState(false);
  const { showError, showSuccess } = useToast();

  useEffect(() => {
    if (!submissionId || !examId) return;
    void (async () => {
      try {
        const [submissionData, totals, questionData, submissionRows, candidateData] = await Promise.all([
          api.getSubmission(submissionId),
          api.getFrontPageTotals(submissionId),
          api.listQuestions(examId),
          api.listExamSubmissions(examId),
          api.getFrontPageTotalsCandidates(submissionId).catch((error: unknown) => {
            setCandidateError(error instanceof Error ? error.message : 'Failed to extract front-page totals candidates');
            return null;
          }),
        ]);
        const initialState = buildInitialFrontPageFormState(questionData, totals, candidateData, submissionData.student_name);
        setSubmission(submissionData);
        setQuestions(questionData);
        setExamSubmissions(submissionRows);
        setCandidateTotals(candidateData);
        setOverallMarksAwarded(initialState.overallMarksAwarded);
        setOverallMaxMarks(initialState.overallMaxMarks);
        setTeacherNote(initialState.teacherNote);
        setObjectiveScores(initialState.objectiveScores);
      } catch (error) {
        showError(error instanceof Error ? error.message : 'Failed to load front-page totals');
      }
    })();
  }, [examId, showError, submissionId]);

  const seededObjectiveScores = useMemo(() => buildSeededObjectiveScores(questions), [questions]);
  const seededObjectiveMap = useMemo(() => new Map(seededObjectiveScores.map((row) => [row.objective_code, row])), [seededObjectiveScores]);
  const savedTotals = submission?.front_page_totals ?? null;
  const hasSavedTotals = useMemo(() => Boolean(savedTotals), [savedTotals]);
  const currentOverallRead = parseNumeric(overallMarksAwarded);
  const currentOverallMaxRead = parseNumeric(overallMaxMarks);

  const frontPageSubmissions = useMemo(
    () => examSubmissions.filter((candidate) => candidate.capture_mode === 'front_page_totals'),
    [examSubmissions],
  );

  const currentFrontPageIndex = useMemo(
    () => frontPageSubmissions.findIndex((candidate) => candidate.id === submissionId),
    [frontPageSubmissions, submissionId],
  );

  const remainingFrontPageQueue = useMemo(
    () => frontPageSubmissions.filter((candidate) => !candidate.front_page_totals?.confirmed),
    [frontPageSubmissions],
  );

  const previousFrontPageSubmission = useMemo(
    () => currentFrontPageIndex > 0 ? frontPageSubmissions[currentFrontPageIndex - 1] : null,
    [currentFrontPageIndex, frontPageSubmissions],
  );

  const nextFrontPageSubmission = useMemo(() => {
    if (!submission) return null;
    const currentIndex = frontPageSubmissions.findIndex((row) => row.id === submission.id);
    if (currentIndex < 0) return null;

    for (let index = currentIndex + 1; index < frontPageSubmissions.length; index += 1) {
      const candidate = frontPageSubmissions[index];
      if (!candidate.front_page_totals?.confirmed) {
        return candidate;
      }
    }

    return frontPageSubmissions.find((candidate) => !candidate.front_page_totals?.confirmed && candidate.id !== submission.id) ?? null;
  }, [frontPageSubmissions, submission]);

  const comparisonRows = useMemo(() => {
    const currentRows = objectiveScores
      .map((row) => ({
        objective_code: row.objective_code.trim(),
        marks_awarded: Number(row.marks_awarded),
        max_marks: row.max_marks == null ? null : Number(row.max_marks),
      }))
      .filter((row) => row.objective_code);
    const codes = new Set<string>([
      ...currentRows.map((row) => row.objective_code),
      ...((savedTotals?.objective_scores ?? []).map((row) => row.objective_code)),
      ...seededObjectiveScores.map((row) => row.objective_code),
      ...(candidateTotals?.objective_scores.map((row) => row.objective_code.value_text.trim()).filter(Boolean) ?? []),
    ]);

    return [...codes]
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }))
      .map((code) => {
        const normalizedCode = normalizeCode(code);
        const readRow = currentRows.find((row) => normalizeCode(row.objective_code) === normalizedCode);
        const savedRow = savedTotals?.objective_scores.find((row) => normalizeCode(row.objective_code) === normalizedCode) ?? null;
        const seededRow = [...seededObjectiveMap.entries()].find(([key]) => normalizeCode(key) === normalizedCode)?.[1] ?? null;
        const extractedRow = candidateTotals?.objective_scores.find((row) => normalizeCode(row.objective_code.value_text) === normalizedCode) ?? null;
        const extractedAwarded = extractedRow ? parseNumeric(extractedRow.marks_awarded.value_text) : null;
        const extractedMax = extractedRow?.max_marks?.value_text ? parseNumeric(extractedRow.max_marks.value_text) : null;
        const extractedConfidence = extractedRow
          ? Math.min(extractedRow.objective_code.confidence, extractedRow.marks_awarded.confidence, extractedRow.max_marks?.confidence ?? 1)
          : null;
        const matchesSaved = numbersMatch(readRow?.marks_awarded ?? null, savedRow?.marks_awarded ?? null)
          && numbersMatch(readRow?.max_marks ?? null, savedRow?.max_marks ?? null);
        const matchesExtracted = numbersMatch(readRow?.marks_awarded ?? null, extractedAwarded)
          && ((readRow?.max_marks ?? null) == null || extractedMax == null || numbersMatch(readRow?.max_marks ?? null, extractedMax));

        return {
          code,
          readAwarded: readRow ? Number(readRow.marks_awarded) : null,
          readMax: readRow?.max_marks ?? seededRow?.max_marks ?? null,
          savedAwarded: savedRow ? Number(savedRow.marks_awarded) : null,
          savedMax: savedRow?.max_marks ?? null,
          extractedAwarded,
          extractedMax,
          extractedConfidence,
          configuredMax: seededRow?.max_marks ?? null,
          hasSeededConfig: Boolean(seededRow),
          hasTeacherEntry: Boolean(readRow),
          hasSavedValue: Boolean(savedRow),
          hasExtractedValue: Boolean(extractedRow),
          extractedEvidence: extractedRow ? [
            ...candidateEvidenceBlocks(extractedRow.objective_code),
            ...candidateEvidenceBlocks(extractedRow.marks_awarded),
            ...candidateEvidenceBlocks(extractedRow.max_marks),
          ] : [],
          matchesSaved,
          matchesExtracted,
        };
      });
  }, [candidateTotals, objectiveScores, savedTotals, seededObjectiveMap, seededObjectiveScores]);

  const objectiveMismatchCount = comparisonRows.filter((row) => !row.matchesSaved || !row.matchesExtracted).length;
  const missingObjectiveRows = comparisonRows.filter((row) => row.hasSeededConfig && !row.hasExtractedValue);
  const unexpectedExtractedObjectiveRows = comparisonRows.filter((row) => row.hasExtractedValue && !row.hasSeededConfig);
  const lowConfidenceObjectiveRows = comparisonRows.filter((row) => row.hasExtractedValue && (row.extractedConfidence ?? 1) < 0.6);
  const lowConfidenceFieldCount = [
    candidateTotals?.student_name && candidateTotals.student_name.confidence < 0.6 ? 1 : 0,
    candidateTotals?.overall_marks_awarded && candidateTotals.overall_marks_awarded.confidence < 0.6 ? 1 : 0,
    candidateTotals?.overall_max_marks && candidateTotals.overall_max_marks.confidence < 0.6 ? 1 : 0,
    ...comparisonRows.map((row) => row.hasExtractedValue && (row.extractedConfidence ?? 1) < 0.6 ? 1 : 0),
  ].reduce((sum, count) => sum + count, 0);

  const teacherObjectiveTotal = sumValues(comparisonRows.map((row) => row.readAwarded));
  const extractedObjectiveTotal = sumValues(comparisonRows.map((row) => row.extractedAwarded));
  const savedObjectiveTotal = sumValues(comparisonRows.map((row) => row.savedAwarded));

  const teacherOverallVsObjectivesMismatch = currentOverallRead != null && teacherObjectiveTotal != null && !numbersMatch(currentOverallRead, teacherObjectiveTotal);
  const extractedOverallVsObjectivesMismatch = candidateTotals?.overall_marks_awarded && extractedObjectiveTotal != null
    ? !numbersMatch(parseNumeric(candidateTotals.overall_marks_awarded.value_text), extractedObjectiveTotal)
    : false;
  const savedOverallVsObjectivesMismatch = savedTotals && savedObjectiveTotal != null
    ? !numbersMatch(savedTotals.overall_marks_awarded, savedObjectiveTotal)
    : false;

  const studentNameMismatch = Boolean(candidateTotals?.student_name?.value_text?.trim() && candidateTotals.student_name.value_text.trim() !== submission?.student_name.trim());
  const overallMismatch = Boolean(
    (candidateTotals?.overall_marks_awarded && !numbersMatch(currentOverallRead, parseNumeric(candidateTotals.overall_marks_awarded.value_text)))
    || (candidateTotals?.overall_max_marks && currentOverallMaxRead != null && parseNumeric(candidateTotals.overall_max_marks.value_text) != null && !numbersMatch(currentOverallMaxRead, parseNumeric(candidateTotals.overall_max_marks.value_text)))
    || (savedTotals && (!numbersMatch(currentOverallRead, savedTotals.overall_marks_awarded) || ((currentOverallMaxRead ?? null) !== (savedTotals.overall_max_marks ?? null) && !(currentOverallMaxRead == null && savedTotals.overall_max_marks == null))))
  );

  const reviewFlags = [
    studentNameMismatch ? 'Student name mismatch' : null,
    overallMismatch ? 'Overall totals mismatch' : null,
    objectiveMismatchCount > 0 ? `${objectiveMismatchCount} objective row${objectiveMismatchCount === 1 ? '' : 's'} differ across states` : null,
    missingObjectiveRows.length > 0 ? `${missingObjectiveRows.length} expected objective row${missingObjectiveRows.length === 1 ? '' : 's'} missing from extractor output` : null,
    unexpectedExtractedObjectiveRows.length > 0 ? `${unexpectedExtractedObjectiveRows.length} extra extracted objective row${unexpectedExtractedObjectiveRows.length === 1 ? '' : 's'} are not in exam config` : null,
    lowConfidenceFieldCount > 0 ? `${lowConfidenceFieldCount} low-confidence extracted field${lowConfidenceFieldCount === 1 ? '' : 's'}` : null,
    teacherOverallVsObjectivesMismatch ? 'Teacher overall total does not match teacher objective totals' : null,
    extractedOverallVsObjectivesMismatch ? 'Extractor overall total does not match extracted objective totals' : null,
    savedOverallVsObjectivesMismatch ? 'Saved total does not match saved objective totals' : null,
    (candidateTotals?.warnings.length ?? 0) > 0 ? `${candidateTotals?.warnings.length ?? 0} extractor warning${(candidateTotals?.warnings.length ?? 0) === 1 ? '' : 's'}` : null,
  ].filter((item): item is string => Boolean(item));
  const issueCount = reviewFlags.length;

  const formIsEdited = useMemo(() => {
    if (!candidateTotals && !savedTotals) return false;

    const overallEditedFromSaved = savedTotals
      ? !numbersMatch(currentOverallRead, savedTotals.overall_marks_awarded)
        || !numbersMatch(currentOverallMaxRead, savedTotals.overall_max_marks ?? null)
      : false;

    const overallEditedFromExtracted = candidateTotals
      ? (!isBlank(overallMarksAwarded) && !numbersMatch(currentOverallRead, parseNumeric(candidateTotals.overall_marks_awarded?.value_text ?? '')))
        || (!isBlank(overallMaxMarks) && !numbersMatch(currentOverallMaxRead, parseNumeric(candidateTotals.overall_max_marks?.value_text ?? '')))
      : false;

    const noteEdited = Boolean(savedTotals ? teacherNote !== (savedTotals.teacher_note || '') : teacherNote.trim());
    const objectiveEdited = comparisonRows.some((row) => !row.matchesSaved || !row.matchesExtracted);

    return overallEditedFromSaved || overallEditedFromExtracted || noteEdited || objectiveEdited;
  }, [candidateTotals, comparisonRows, currentOverallMaxRead, currentOverallRead, overallMarksAwarded, overallMaxMarks, savedTotals, teacherNote]);

  const queueProgressLabel = useMemo(() => {
    if (frontPageSubmissions.length === 0 || currentFrontPageIndex < 0) return 'Queue position unavailable';
    return `Paper ${currentFrontPageIndex + 1} of ${frontPageSubmissions.length}`;
  }, [currentFrontPageIndex, frontPageSubmissions.length]);

  const queueRemainingAfterCurrent = useMemo(() => {
    return remainingFrontPageQueue.filter((candidate) => candidate.id !== submissionId).length;
  }, [remainingFrontPageQueue, submissionId]);
  const queueWillBeClearAfterConfirm = queueRemainingAfterCurrent === 0;

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

  const onExportClassSummary = async () => {
    try {
      setIsExportingSummary(true);
      const { blob, filename } = await api.downloadExamSummaryCsv(examId);
      downloadBlob(blob, filename);
      showSuccess('Class summary CSV downloaded.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export class summary CSV');
    } finally {
      setIsExportingSummary(false);
    }
  };

  const onExportTotalsCsv = async () => {
    try {
      setIsExportingTotals(true);
      const { blob, filename } = await api.downloadExamExportCsv(examId);
      downloadBlob(blob, filename);
      showSuccess('Totals CSV downloaded.');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to export totals CSV');
    } finally {
      setIsExportingTotals(false);
    }
  };

  const updateObjective = (index: number, patch: Partial<FrontPageObjectiveScore>) => {
    setObjectiveScores((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
  };

  const goToFrontPageSubmission = (targetSubmissionId: number) => {
    navigate(`/submissions/${targetSubmissionId}/front-page-totals?examId=${examId}&returnTo=${encodeURIComponent(returnTo)}&returnLabel=${encodeURIComponent(returnLabel)}`);
  };

  const applyCandidatesToForm = () => {
    if (!candidateTotals) return;
    if (isBlank(overallMarksAwarded) && candidateTotals.overall_marks_awarded?.value_text) {
      setOverallMarksAwarded(candidateTotals.overall_marks_awarded.value_text);
    }
    if (isBlank(overallMaxMarks) && candidateTotals.overall_max_marks?.value_text) {
      setOverallMaxMarks(candidateTotals.overall_max_marks.value_text);
    }
    if (candidateTotals.objective_scores.length > 0) {
      setObjectiveScores((current) => mergeCandidateIntoObjectiveScores(current, candidateTotals));
    }
    if (candidateTotals.student_name?.value_text && candidateTotals.student_name.value_text !== submission?.student_name) {
      setTeacherNote((current) => {
        const prefix = `Extractor saw student name: ${candidateTotals.student_name?.value_text}.`;
        return current.includes(prefix) ? current : `${prefix}${current ? `\n\n${current}` : ''}`;
      });
    }
  };

  const applyAllCandidatesToForm = () => {
    if (!candidateTotals) return;
    if (candidateTotals.overall_marks_awarded?.value_text) {
      setOverallMarksAwarded(candidateTotals.overall_marks_awarded.value_text);
    }
    if (candidateTotals.overall_max_marks?.value_text) {
      setOverallMaxMarks(candidateTotals.overall_max_marks.value_text);
    }
    setObjectiveScores((current) => {
      const seeded = current.length > 0 ? current : seededObjectiveScores;
      return mergeCandidateIntoObjectiveScores(
        seeded.map((row) => ({ ...row })),
        candidateTotals,
        { fillExistingAwardedWhenZero: true },
      ).map((row) => {
        const extracted = candidateTotals.objective_scores.find((candidateRow) => normalizeCode(candidateRow.objective_code.value_text) === normalizeCode(row.objective_code));
        return extracted
          ? {
            ...row,
            objective_code: extracted.objective_code.value_text || row.objective_code,
            marks_awarded: parseNumeric(extracted.marks_awarded.value_text) ?? row.marks_awarded,
            max_marks: extracted.max_marks?.value_text ? parseNumeric(extracted.max_marks.value_text) : row.max_marks,
          }
          : row;
      });
    });
    if (candidateTotals.student_name?.value_text && candidateTotals.student_name.value_text !== submission?.student_name) {
      setTeacherNote((current) => {
        const prefix = `Extractor saw student name: ${candidateTotals.student_name?.value_text}.`;
        return current.includes(prefix) ? current : `${prefix}${current ? `\n\n${current}` : ''}`;
      });
    }
  };

  const reseedFromExamConfig = () => {
    setObjectiveScores((current) => {
      if (current.length === 0) return seededObjectiveScores;
      const currentByCode = new Map(current.map((row) => [row.objective_code.trim(), row]));
      const merged = seededObjectiveScores.map((row) => {
        const existing = currentByCode.get(row.objective_code);
        return existing
          ? {
            ...existing,
            objective_code: row.objective_code,
            max_marks: existing.max_marks ?? row.max_marks,
          }
          : row;
      });
      const extras = current.filter((row) => row.objective_code.trim() && !seededObjectiveMap.has(row.objective_code.trim()));
      return [...merged, ...extras];
    });
  };

  const save = async (goNext = false) => {
    const overallAwarded = Number(overallMarksAwarded);
    const overallMax = overallMaxMarks.trim() ? Number(overallMaxMarks) : null;
    if (!Number.isFinite(overallAwarded) || overallAwarded < 0) {
      showError('Enter a valid overall total.');
      return;
    }
    if (overallMax !== null && (!Number.isFinite(overallMax) || overallMax < 0 || overallAwarded > overallMax)) {
      showError('Overall total must be between 0 and the entered max.');
      return;
    }

    const cleanedScores = objectiveScores
      .map((row) => ({
        objective_code: row.objective_code.trim(),
        marks_awarded: Number(row.marks_awarded),
        max_marks: row.max_marks == null || row.max_marks === undefined || row.max_marks === ('' as never) ? null : Number(row.max_marks),
      }))
      .filter((row) => row.objective_code);

    if (cleanedScores.some((row) => !Number.isFinite(row.marks_awarded) || row.marks_awarded < 0)) {
      showError('Each objective total needs a valid awarded mark.');
      return;
    }
    if (cleanedScores.some((row) => row.max_marks !== null && (!Number.isFinite(row.max_marks) || row.max_marks < 0 || row.marks_awarded > row.max_marks))) {
      showError('Objective totals must be between 0 and their entered max values.');
      return;
    }

    try {
      setSaving(true);
      await api.saveFrontPageTotals(submissionId, {
        overall_marks_awarded: overallAwarded,
        overall_max_marks: overallMax,
        objective_scores: cleanedScores,
        teacher_note: teacherNote,
        confirmed: true,
      });
      const [refreshedSubmission, refreshedExamSubmissions] = await Promise.all([
        api.getSubmission(submissionId),
        api.listExamSubmissions(examId),
      ]);
      setSubmission(refreshedSubmission);
      setExamSubmissions(refreshedExamSubmissions);

      const refreshedNextFrontPageSubmission = refreshedExamSubmissions.find((candidate) => (
        candidate.capture_mode === 'front_page_totals'
        && !candidate.front_page_totals?.confirmed
        && candidate.id !== submissionId
      )) ?? null;

      if (goNext && refreshedNextFrontPageSubmission) {
        showSuccess(`Confirmed ${submission?.student_name}. Next up: ${refreshedNextFrontPageSubmission.student_name}.`);
        navigate(`/submissions/${refreshedNextFrontPageSubmission.id}/front-page-totals?examId=${examId}&returnTo=${encodeURIComponent(returnTo)}&returnLabel=${encodeURIComponent(returnLabel)}`);
        return;
      }

      showSuccess(goNext ? 'Front-page totals confirmed. Queue complete.' : 'Front-page totals saved and confirmed.');
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
    if (!formIsEdited) return undefined;

    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [formIsEdited]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const metaOrCtrl = event.metaKey || event.ctrlKey;
      if (metaOrCtrl && event.key.toLowerCase() === 's') {
        event.preventDefault();
        void save(false);
        return;
      }
      if (metaOrCtrl && event.key === 'Enter') {
        event.preventDefault();
        void save(true);
        return;
      }
      if (event.altKey && event.key.toLowerCase() === 'n' && nextFrontPageSubmission && !formIsEdited) {
        event.preventDefault();
        goToFrontPageSubmission(nextFrontPageSubmission.id);
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [formIsEdited, nextFrontPageSubmission]);

  if (!submission) return <p>Loading front-page totals…</p>;

  return (
    <div className="workflow-shell workflow-shell--compact">
      <section className="card card--hero stack">
        <p style={{ margin: 0 }}><Link to={returnTo}>← {returnLabel}</Link></p>
        <div className="page-header">
          <div>
            <p className="page-eyebrow">Front-page totals</p>
            <h1 className="page-title">{submission.student_name}</h1>
            <p className="page-subtitle">Check the parsed name and totals, confirm this paper, then move to the next one.</p>
          </div>
          <div className="page-toolbar">
            <span className={`status-pill ${hasSavedTotals ? 'status-complete' : 'status-ready'}`}>{hasSavedTotals ? 'Confirmed' : 'Needs capture'}</span>
            <span className={`status-pill ${formIsEdited ? 'status-in-progress' : 'status-neutral'}`}>{formIsEdited ? 'Unsaved edits' : 'Ready to confirm'}</span>
            {nextFrontPageSubmission && <span className="status-pill status-in-progress">Next: {nextFrontPageSubmission.student_name}</span>}
            {!nextFrontPageSubmission && hasSavedTotals && (
              <button type="button" className="btn btn-secondary" onClick={() => void onExportClassSummary()} disabled={isExportingSummary}>
                {isExportingSummary ? 'Exporting…' : 'Export class summary CSV'}
              </button>
            )}
            <Link className="btn btn-secondary" to={`/submissions/${submissionId}/results?examId=${examId}`}>Open results</Link>
          </div>
        </div>
        <div className="metric-grid">
          <article className="metric-card">
            <p className="metric-label">Queue remaining</p>
            <p className="metric-value">{remainingFrontPageQueue.length}</p>
            <p className="metric-meta">Papers still waiting in this exam</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Extractor read</p>
            <p className="metric-value">{candidateTotals?.overall_marks_awarded?.value_text || '—'}</p>
            <p className="metric-meta">{candidateTotals?.overall_max_marks?.value_text ? `Out of ${candidateTotals.overall_max_marks.value_text}` : 'No parsed max yet'}</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Current total</p>
            <p className="metric-value">{formatMaybeNumber(currentOverallRead)}</p>
            <p className="metric-meta">{currentOverallMaxRead != null ? `Out of ${currentOverallMaxRead}` : 'No max entered yet'}</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Saved state</p>
            <p className="metric-value">{savedTotals ? 'Confirmed' : 'Not saved'}</p>
            <p className="metric-meta">{savedTotals ? `Results/export use the saved totals below${savedTotals.reviewed_at ? ` · ${formatTimestamp(savedTotals.reviewed_at)}` : ''}` : 'Nothing confirmed yet for export/results'}</p>
          </article>
        </div>

        {false && (
        <section className="front-page-queue-strip" aria-label="Front-page totals queue continuity">
          <div className="front-page-queue-strip__summary">
            <span className="status-pill status-neutral">{queueProgressLabel}</span>
            <span className={`status-pill ${queueRemainingAfterCurrent > 0 ? 'status-in-progress' : 'status-complete'}`}>
              {queueRemainingAfterCurrent > 0 ? `${queueRemainingAfterCurrent} after this` : 'This is the last unconfirmed paper'}
            </span>
            <span className={`status-pill ${formIsEdited ? 'status-flagged' : 'status-complete'}`}>
              {formIsEdited ? 'Unsaved teacher edits' : 'Safe to move on'}
            </span>
          </div>
          <div className="front-page-queue-strip__cards">
            <div className="front-page-queue-card">
              <p className="metric-label">Previous</p>
              <strong>{previousFrontPageSubmission?.student_name || '—'}</strong>
              <p className="subtle-text">{previousFrontPageSubmission?.front_page_totals?.confirmed ? 'Already confirmed' : previousFrontPageSubmission ? 'Earlier in this lane' : 'Start of queue'}</p>
              {previousFrontPageSubmission && (
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => goToFrontPageSubmission(previousFrontPageSubmission!.id)}>
                  Open previous
                </button>
              )}
            </div>
            <div className="front-page-queue-card front-page-queue-card--active">
              <p className="metric-label">Current paper</p>
              <strong>{submission!.student_name}</strong>
              <p className="subtle-text">{formIsEdited ? 'You have teacher edits waiting to be confirmed.' : hasSavedTotals ? 'Saved output already exists for results/export.' : 'Ready for confirmation.'}</p>
              <div className="front-page-shortcut-row">
                <span className="front-page-shortcut-chip">Ctrl/Cmd+S confirm</span>
                <span className="front-page-shortcut-chip">Ctrl/Cmd+Enter confirm + next</span>
              </div>
            </div>
            <div className="front-page-queue-card">
              <p className="metric-label">Next unconfirmed</p>
              <strong>{nextFrontPageSubmission?.student_name || 'Queue clear'}</strong>
              <p className="subtle-text">{nextFrontPageSubmission ? 'Stay in the lane and keep moving after this paper.' : 'No more front-page totals papers waiting. Export is the next move.'}</p>
              {nextFrontPageSubmission && (
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => goToFrontPageSubmission(nextFrontPageSubmission!.id)} disabled={formIsEdited} title={formIsEdited ? 'Confirm or clear teacher edits before skipping ahead.' : undefined}>
                  Jump to next
                </button>
              )}
              {!nextFrontPageSubmission && hasSavedTotals && (
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => void onExportTotalsCsv()} disabled={isExportingTotals || formIsEdited}>
                  {isExportingTotals ? 'Exporting…' : 'Export totals CSV'}
                </button>
              )}
            </div>
          </div>
        </section>
        )}
      </section>

      {false && issueCount > 0 && (
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Review flags</h2>
              <p className="subtle-text">Make mismatches obvious before you confirm and move on.</p>
            </div>
            <span className="status-pill status-flagged">{issueCount} review area{issueCount === 1 ? '' : 's'}</span>
          </div>
          <div className="stack" style={{ gap: '.55rem' }}>
            {reviewFlags.map((flag) => (
              <div key={flag} className="review-readonly-block">
                <strong>{flag}</strong>
              </div>
            ))}
            {studentNameMismatch && (
              <div className="review-readonly-block">
                <strong>Student name mismatch</strong>
                <div style={{ marginTop: '.4rem' }}>Saved submission name: {submission!.student_name}</div>
                <div>Extractor read: {candidateTotals?.student_name?.value_text || '—'}</div>
                <div className="subtle-text" style={{ marginTop: '.3rem' }}>Evidence: {evidenceSummary(candidateTotals?.student_name)}</div>
                <div className="subtle-text" style={{ marginTop: '.3rem' }}>If the paper name is wrong or partial, keep the saved submission name and leave a note. If the upload was assigned to the wrong student, stop here and correct the submission identity before continuing.</div>
              </div>
            )}
            {overallMismatch && (
              <div className="review-readonly-block">
                <strong>Overall totals mismatch</strong>
                <div className="front-page-compare-grid" style={{ marginTop: '.5rem' }}>
                  <div>
                    <div className="metric-label">Teacher entry</div>
                    <div>{formatMaybeNumber(currentOverallRead)} / {formatMaybeNumber(currentOverallMaxRead)}</div>
                  </div>
                  <div>
                    <div className="metric-label">Extractor</div>
                    <div>{formatMaybeNumber(parseNumeric(candidateTotals?.overall_marks_awarded?.value_text ?? ''))} / {formatMaybeNumber(parseNumeric(candidateTotals?.overall_max_marks?.value_text ?? ''))}</div>
                  </div>
                  <div>
                    <div className="metric-label">Saved</div>
                    <div>{formatMaybeNumber(savedTotals?.overall_marks_awarded ?? null)} / {formatMaybeNumber(savedTotals?.overall_max_marks ?? null)}</div>
                  </div>
                </div>
                <div className="subtle-text" style={{ marginTop: '.3rem' }}>Evidence: {evidenceSummary(candidateTotals?.overall_marks_awarded)}</div>
              </div>
            )}
            {(teacherOverallVsObjectivesMismatch || extractedOverallVsObjectivesMismatch || savedOverallVsObjectivesMismatch) && (
              <div className="review-readonly-block">
                <strong>Overall vs objective rollup mismatch</strong>
                <div className="front-page-compare-grid" style={{ marginTop: '.5rem' }}>
                  <div>
                    <div className="metric-label">Teacher</div>
                    <div>{formatMaybeNumber(currentOverallRead)} overall · {formatMaybeNumber(teacherObjectiveTotal)} from objectives</div>
                  </div>
                  <div>
                    <div className="metric-label">Extractor</div>
                    <div>{formatMaybeNumber(parseNumeric(candidateTotals?.overall_marks_awarded?.value_text ?? ''))} overall · {formatMaybeNumber(extractedObjectiveTotal)} from objectives</div>
                  </div>
                  <div>
                    <div className="metric-label">Saved</div>
                    <div>{formatMaybeNumber(savedTotals?.overall_marks_awarded ?? null)} overall · {formatMaybeNumber(savedObjectiveTotal)} from objectives</div>
                  </div>
                </div>
              </div>
            )}
            {missingObjectiveRows.length > 0 && (
              <div className="review-readonly-block">
                <strong>Missing objective rows from extraction</strong>
                <div className="subtle-text" style={{ marginTop: '.3rem' }}>{missingObjectiveRows.map((row) => row.code).join(', ')}</div>
              </div>
            )}
            {unexpectedExtractedObjectiveRows.length > 0 && (
              <div className="review-readonly-block">
                <strong>Extra extracted objective rows not in exam config</strong>
                <div className="subtle-text" style={{ marginTop: '.3rem' }}>{unexpectedExtractedObjectiveRows.map((row) => row.code).join(', ')}</div>
              </div>
            )}
            {lowConfidenceObjectiveRows.length > 0 && (
              <div className="review-readonly-block">
                <strong>Low-confidence objective rows</strong>
                <div className="subtle-text" style={{ marginTop: '.3rem' }}>{lowConfidenceObjectiveRows.map((row) => `${row.code} (${confidenceLabel(row.extractedConfidence ?? undefined)})`).join(' · ')}</div>
              </div>
            )}
            {(candidateTotals?.warnings.length ?? 0) > 0 && (
              <div className="review-readonly-block">
                <strong>Extractor warnings</strong>
                <div className="subtle-text" style={{ marginTop: '.3rem' }}>{candidateTotals?.warnings.join(' · ')}</div>
              </div>
            )}
          </div>
        </section>
      )}

      <div className="workflow-grid">
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Paper</h2>
              <p className="subtle-text">Keep the paper visible while you confirm the parsed result.</p>
            </div>
            <span className="status-pill status-neutral">Visual check</span>
          </div>
          {submission.pages.length > 0 ? (
            <div className="image-frame">
              <img
                src={api.getPageImageUrl(submissionId, 1)}
                alt={`Front page for ${submission!.student_name}`}
                style={{ maxWidth: '100%', display: 'block', borderRadius: 10 }}
              />
            </div>
          ) : (
            <div className="review-readonly-block">No built page image yet. You can still confirm totals from the uploaded paper or submission file.</div>
          )}
          <p className="subtle-text">The extractor only targets the student name, overall total, and visible objective totals on the front page.</p>
        </section>

        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Parsed result</h2>
              <p className="subtle-text">These are suggestions from the paper. Apply them, adjust them, then confirm.</p>
            </div>
            <div className="actions-row" style={{ marginTop: 0 }}>
              {candidateTotals && <span className="status-pill status-neutral">{candidateTotals.source}</span>}
              <button type="button" className="btn btn-secondary btn-sm" onClick={applyCandidatesToForm} disabled={!candidateTotals}>Fill blanks</button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={applyAllCandidatesToForm} disabled={!candidateTotals}>Use parsed values</button>
            </div>
          </div>
          {candidateError && <div className="review-readonly-block">{candidateError}</div>}
          {!candidateError && !candidateTotals && <p className="subtle-text">Loading candidates…</p>}
          {candidateTotals && (
            <div className="stack" style={{ gap: '.75rem' }}>
              {candidateTotals.student_name && (
                <div className="review-readonly-block">
                  <div className="panel-title-row"><strong>Student</strong><span className={`status-pill ${studentNameMismatch ? 'status-flagged' : confidenceTone(candidateTotals.student_name.confidence)}`}>{studentNameMismatch ? 'Mismatch' : confidenceLabel(candidateTotals.student_name.confidence)}</span></div>
                  <div>{candidateTotals.student_name.value_text || '—'}</div>
                  <div className="subtle-text" style={{ marginTop: '.35rem' }}>Saved submission name: {submission!.student_name}</div>
                  <div className="subtle-text" style={{ marginTop: '.35rem' }}>Evidence summary: {evidenceSummary(candidateTotals.student_name)}</div>
                  {candidateEvidenceBlocks(candidateTotals.student_name).map((item, index) => (
                    <div key={`student-evidence-${index}`} className="code-box" style={{ marginTop: '.45rem' }}>
                      p.{item.page_number}: “{item.quote}”
                    </div>
                  ))}
                </div>
              )}
              {candidateTotals.overall_marks_awarded && (
                <div className="review-readonly-block">
                  <div className="panel-title-row"><strong>Overall total</strong><span className={`status-pill ${overallMismatch ? 'status-flagged' : confidenceTone(candidateTotals.overall_marks_awarded.confidence)}`}>{overallMismatch ? 'Review' : confidenceLabel(candidateTotals.overall_marks_awarded.confidence)}</span></div>
                  <div>{candidateTotals.overall_marks_awarded.value_text || '—'} / {candidateTotals.overall_max_marks?.value_text || '—'}</div>
                  <div className="subtle-text" style={{ marginTop: '.35rem' }}>Teacher entry: {formatMaybeNumber(currentOverallRead)} / {formatMaybeNumber(currentOverallMaxRead)}</div>
                  <div className="subtle-text">Saved output: {formatMaybeNumber(savedTotals?.overall_marks_awarded ?? null)} / {formatMaybeNumber(savedTotals?.overall_max_marks ?? null)}</div>
                  <div className="subtle-text">Evidence summary: {evidenceSummary(candidateTotals.overall_marks_awarded)}</div>
                  {candidateEvidenceBlocks(candidateTotals.overall_marks_awarded).map((item, index) => (
                    <div key={`overall-evidence-${index}`} className="code-box" style={{ marginTop: '.45rem' }}>
                      p.{item.page_number}: “{item.quote}”
                    </div>
                  ))}
                </div>
              )}
              {candidateTotals.objective_scores.map((row, index) => {
                const compareRow = comparisonRows.find((item) => normalizeCode(item.code) === normalizeCode(row.objective_code.value_text));
                const confidence = Math.min(row.objective_code.confidence, row.marks_awarded.confidence, row.max_marks?.confidence ?? 1);
                const hasMismatch = compareRow ? (!compareRow.matchesExtracted || !compareRow.matchesSaved) : false;
                return (
                  <div className="review-readonly-block" key={`candidate-objective-${index}`}>
                    <div className="panel-title-row"><strong>{row.objective_code.value_text || 'Objective row'}</strong><span className={`status-pill ${hasMismatch ? 'status-flagged' : confidenceTone(confidence)}`}>{hasMismatch ? 'Review' : confidenceLabel(confidence)}</span></div>
                    <div>{row.marks_awarded.value_text || '—'} / {row.max_marks?.value_text || '—'}</div>
                    <div className="subtle-text" style={{ marginTop: '.35rem' }}>Teacher entry: {formatMaybeNumber(compareRow?.readAwarded ?? null)} / {formatMaybeNumber(compareRow?.readMax ?? null)}</div>
                    <div className="subtle-text">Saved output: {formatMaybeNumber(compareRow?.savedAwarded ?? null)} / {formatMaybeNumber(compareRow?.savedMax ?? null)}</div>
                    <div className="subtle-text">Evidence summary: {compareRow?.extractedEvidence.length ? compareRow.extractedEvidence.map((item) => `p.${item.page_number} “${item.quote}”`).join(' · ') : 'No quote evidence captured'}</div>
                    {[...candidateEvidenceBlocks(row.objective_code), ...candidateEvidenceBlocks(row.marks_awarded), ...candidateEvidenceBlocks(row.max_marks)].map((item, evidenceIndex) => (
                      <div key={`objective-evidence-${index}-${evidenceIndex}`} className="code-box" style={{ marginTop: '.45rem' }}>
                        p.{item.page_number}: “{item.quote}”
                      </div>
                    ))}
                  </div>
                );
              })}
              {candidateTotals.warnings.length > 0 && <div className="review-readonly-block"><strong>Warnings:</strong> {candidateTotals.warnings.join(' · ')}</div>}
            </div>
          )}

          <div className="review-field-grid review-field-grid--two-up">
            <label className="stack">
              Overall total
              <input type="number" min={0} step="0.5" value={overallMarksAwarded} onChange={(event) => setOverallMarksAwarded(event.target.value)} />
            </label>
            <label className="stack">
              Overall max
              <input type="number" min={0} step="0.5" value={overallMaxMarks} onChange={(event) => setOverallMaxMarks(event.target.value)} />
            </label>
          </div>

          <div className="stack">
            <div className="panel-title-row">
              <div>
                <h3 className="section-title" style={{ marginBottom: 0 }}>Objective totals</h3>
                <p className="subtle-text" style={{ margin: 0 }}>Optional. Leave blank if the paper only shows one overall total.</p>
              </div>
              <div className="actions-row" style={{ marginTop: 0 }}>
                <button type="button" className="btn btn-secondary btn-sm" onClick={reseedFromExamConfig} disabled={seededObjectiveScores.length === 0}>Re-seed</button>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setObjectiveScores((current) => [...current, { objective_code: '', marks_awarded: 0, max_marks: null }])}>Add row</button>
              </div>
            </div>

            {objectiveScores.length === 0 && <p className="subtle-text">No objective totals entered.</p>}
            {objectiveScores.map((row, index) => {
              const compareRow = comparisonRows.find((item) => normalizeCode(item.code) === normalizeCode(row.objective_code));
              const rowStatus = compareRow ? (compareRow.matchesSaved && compareRow.matchesExtracted ? 'status-complete' : 'status-flagged') : 'status-neutral';
              return (
                <div className="front-page-objective-row" key={`objective-inline-${index}`}>
                  <div className="stack" style={{ gap: '.25rem' }}>
                    <input placeholder="Objective / category" value={row.objective_code} onChange={(event) => updateObjective(index, { objective_code: event.target.value })} />
                    {compareRow && <span className={`status-pill ${rowStatus}`}>{compareRow.matchesSaved && compareRow.matchesExtracted ? 'Aligned' : 'Edited'}</span>}
                  </div>
                  <input type="number" min={0} step="0.5" placeholder="Awarded" value={row.marks_awarded} onChange={(event) => updateObjective(index, { marks_awarded: Number(event.target.value) })} />
                  <input type="number" min={0} step="0.5" placeholder="Max" value={row.max_marks ?? ''} onChange={(event) => updateObjective(index, { max_marks: event.target.value === '' ? null : Number(event.target.value) })} />
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => setObjectiveScores((current) => current.filter((_, rowIndex) => rowIndex !== index))}>Remove</button>
                </div>
              );
            })}
          </div>

          <AutoGrowTextarea
            id="front-page-teacher-note"
            label="Teacher note"
            className="textarea-large"
            value={teacherNote}
            onChange={(event) => setTeacherNote(event.target.value)}
          />

          <div className="review-next-action-banner">
            <div>
              <strong>{nextFrontPageSubmission ? 'Confirm and continue' : 'Confirm and finish'}</strong>
              <p className="subtle-text" style={{ marginTop: '.2rem' }}>
                {nextFrontPageSubmission
                  ? `Save this paper, then move directly to ${nextFrontPageSubmission.student_name}.`
                  : 'Save this final paper, then export the class table.'}
              </p>
            </div>
            <div className="actions-row" style={{ marginTop: 0 }}>
              <button type="button" className="btn btn-secondary" onClick={() => void save(false)} disabled={saving}>{saving ? 'Saving…' : 'Accept'}</button>
              <button type="button" className="btn btn-primary" onClick={() => void save(true)} disabled={saving}>
                {saving ? 'Saving…' : nextFrontPageSubmission ? 'Accept + next' : 'Accept + finish'}
              </button>
            </div>
          </div>
        </section>

        {false && (
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">State lanes</h2>
              <p className="subtle-text">Keep extracted, teacher-entered, and saved/exported values visibly separate.</p>
            </div>
            <span className={`status-pill ${savedTotals ? 'status-complete' : 'status-ready'}`}>{savedTotals ? 'Saved output exists' : 'No saved output yet'}</span>
          </div>

          <div className="front-page-state-summary-grid">
            <div className={`front-page-state-summary-card ${studentNameMismatch ? 'is-flagged' : 'is-aligned'}`}>
              <p className="metric-label">Student identity</p>
              <strong>{studentNameMismatch ? 'Mismatch surfaced' : 'Aligned'}</strong>
              <p className="subtle-text">Saved: {submission!.student_name}</p>
              <p className="subtle-text">Extracted: {candidateTotals?.student_name?.value_text?.trim() || '—'}</p>
            </div>
            <div className={`front-page-state-summary-card ${overallMismatch ? 'is-flagged' : 'is-aligned'}`}>
              <p className="metric-label">Overall total</p>
              <strong>{overallMismatch ? 'Needs review' : 'Aligned'}</strong>
              <p className="subtle-text">Teacher: {formatMaybeNumber(currentOverallRead)} / {formatMaybeNumber(currentOverallMaxRead)}</p>
              <p className="subtle-text">Extracted: {formatMaybeNumber(parseNumeric(candidateTotals?.overall_marks_awarded?.value_text ?? ''))} / {formatMaybeNumber(parseNumeric(candidateTotals?.overall_max_marks?.value_text ?? ''))}</p>
            </div>
            <div className={`front-page-state-summary-card ${objectiveMismatchCount > 0 ? 'is-flagged' : 'is-aligned'}`}>
              <p className="metric-label">Objective rows</p>
              <strong>{objectiveMismatchCount > 0 ? `${objectiveMismatchCount} mismatch${objectiveMismatchCount === 1 ? '' : 'es'}` : 'Aligned'}</strong>
              <p className="subtle-text">Teacher rows: {comparisonRows.filter((row) => row.hasTeacherEntry).length}</p>
              <p className="subtle-text">Extracted rows: {comparisonRows.filter((row) => row.hasExtractedValue).length}</p>
            </div>
          </div>

          <div className="review-readonly-block">
            <strong>Overall</strong>
            <div className="front-page-compare-grid" style={{ marginTop: '.6rem' }}>
              <div>
                <div className="metric-label">Extracted</div>
                <div>{formatMaybeNumber(parseNumeric(candidateTotals?.overall_marks_awarded?.value_text ?? ''))} / {formatMaybeNumber(parseNumeric(candidateTotals?.overall_max_marks?.value_text ?? ''))}</div>
              </div>
              <div>
                <div className="metric-label">Teacher entry</div>
                <div>{formatMaybeNumber(currentOverallRead)} / {formatMaybeNumber(currentOverallMaxRead)}</div>
              </div>
              <div>
                <div className="metric-label">Saved/exported</div>
                <div>{formatMaybeNumber(savedTotals?.overall_marks_awarded ?? null)} / {formatMaybeNumber(savedTotals?.overall_max_marks ?? null)}</div>
              </div>
            </div>
          </div>

          <div className="review-readonly-block">
            <strong>Lineage</strong>
            <div className="front-page-compare-grid" style={{ marginTop: '.6rem' }}>
              <div>
                <div className="metric-label">Extracted source</div>
                <div>{candidateTotals?.source || '—'}</div>
                <div className="subtle-text">Raw OCR/vision suggestion only</div>
              </div>
              <div>
                <div className="metric-label">Teacher note in form</div>
                <div>{teacherNote.trim() || '—'}</div>
                <div className="subtle-text">Working edits not exported until confirmed</div>
              </div>
              <div>
                <div className="metric-label">Saved/export lineage</div>
                <div>{savedTotals ? 'Confirmed totals saved' : 'Nothing confirmed yet'}</div>
                <div className="subtle-text">{savedTotals?.reviewed_at ? `Reviewed ${formatTimestamp(savedTotals!.reviewed_at)}` : 'No reviewed timestamp yet'}</div>
              </div>
            </div>
          </div>

          {comparisonRows.length > 0 && (
            <div className="stack" style={{ gap: '.45rem' }}>
              {comparisonRows.map((row) => (
                <div key={`compare-${row.code}`} className="review-readonly-block">
                  <div className="panel-title-row" style={{ marginBottom: '.35rem' }}>
                    <strong>{row.code}</strong>
                    <span className={`status-pill ${row.matchesSaved && row.matchesExtracted ? 'status-complete' : 'status-flagged'}`}>{row.matchesSaved && row.matchesExtracted ? 'Aligned' : 'Needs review'}</span>
                  </div>
                  <div className="front-page-compare-grid" style={{ marginTop: '.45rem' }}>
                    <div>
                      <div className="metric-label">Extracted</div>
                      <div>{formatMaybeNumber(row.extractedAwarded)} / {formatMaybeNumber(row.extractedMax)}</div>
                    </div>
                    <div>
                      <div className="metric-label">Teacher entry</div>
                      <div>{formatMaybeNumber(row.readAwarded)} / {formatMaybeNumber(row.readMax)}</div>
                    </div>
                    <div>
                      <div className="metric-label">Saved/exported</div>
                      <div>{formatMaybeNumber(row.savedAwarded)} / {formatMaybeNumber(row.savedMax)}</div>
                    </div>
                  </div>
                  <div className="subtle-text" style={{ marginTop: '.45rem' }}>Configured max from exam: {formatMaybeNumber(row.configuredMax)}{row.hasSeededConfig ? '' : ' · not in exam config'}</div>
                </div>
              ))}
            </div>
          )}
        </section>
        )}
      </div>

      {false && (
      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">Confirm this paper</h2>
            <p className="subtle-text">Use the parsed values if they look right, make any edits needed, then confirm and continue.</p>
          </div>
        </div>

        <div className="review-field-grid review-field-grid--two-up">
          <label className="stack">
            Overall total
            <input type="number" min={0} step="0.5" value={overallMarksAwarded} onChange={(event) => setOverallMarksAwarded(event.target.value)} />
          </label>
          <label className="stack">
            Overall max (optional)
            <input type="number" min={0} step="0.5" value={overallMaxMarks} onChange={(event) => setOverallMaxMarks(event.target.value)} />
          </label>
        </div>

        <div className="review-readonly-block">
          <strong>Quick check</strong>
          <div className="front-page-compare-grid" style={{ marginTop: '.6rem' }}>
            <div>
              <div className="metric-label">Teacher</div>
              <div>{formatMaybeNumber(currentOverallRead)} overall / {formatMaybeNumber(teacherObjectiveTotal)} from objective rows</div>
            </div>
            <div>
              <div className="metric-label">Extractor</div>
              <div>{formatMaybeNumber(parseNumeric(candidateTotals?.overall_marks_awarded?.value_text ?? ''))} overall / {formatMaybeNumber(extractedObjectiveTotal)} from objective rows</div>
            </div>
            <div>
              <div className="metric-label">Saved</div>
              <div>{formatMaybeNumber(savedTotals?.overall_marks_awarded ?? null)} overall / {formatMaybeNumber(savedObjectiveTotal)} from objective rows</div>
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="panel-title-row">
            <div>
              <h3 className="section-title" style={{ marginBottom: 0 }}>Objective / category totals</h3>
              <p className="subtle-text" style={{ margin: 0 }}>Leave these blank if the paper only shows one overall total.</p>
            </div>
            <div className="actions-row" style={{ marginTop: 0 }}>
              <button type="button" className="btn btn-secondary btn-sm" onClick={reseedFromExamConfig} disabled={seededObjectiveScores.length === 0}>Re-seed from exam config</button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setObjectiveScores((current) => [...current, { objective_code: '', marks_awarded: 0, max_marks: null }])}>Add total</button>
            </div>
          </div>

          {objectiveScores.length === 0 && <p className="subtle-text">No objective totals entered yet.</p>}
          {objectiveScores.map((row, index) => {
            const compareRow = comparisonRows.find((item) => normalizeCode(item.code) === normalizeCode(row.objective_code));
            const rowStatus = compareRow ? (compareRow.matchesSaved && compareRow.matchesExtracted ? 'status-complete' : 'status-flagged') : 'status-neutral';
            return (
              <div className="front-page-objective-row" key={`objective-${index}`}>
                <div className="stack" style={{ gap: '.35rem' }}>
                  <input placeholder="Objective / category" value={row.objective_code} onChange={(event) => updateObjective(index, { objective_code: event.target.value })} />
                  {compareRow && <span className={`status-pill ${rowStatus}`}>{compareRow.matchesSaved && compareRow.matchesExtracted ? 'Aligned across states' : 'Edited / mismatch present'}</span>}
                </div>
                <input type="number" min={0} step="0.5" placeholder="Awarded" value={row.marks_awarded} onChange={(event) => updateObjective(index, { marks_awarded: Number(event.target.value) })} />
                <input type="number" min={0} step="0.5" placeholder="Max (optional)" value={row.max_marks ?? ''} onChange={(event) => updateObjective(index, { max_marks: event.target.value === '' ? null : Number(event.target.value) })} />
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setObjectiveScores((current) => current.filter((_, rowIndex) => rowIndex !== index))}>Remove</button>
              </div>
            );
          })}
        </div>

        <AutoGrowTextarea
          id="front-page-teacher-note"
          label="Teacher note"
          className="textarea-large"
          value={teacherNote}
          onChange={(event) => setTeacherNote(event.target.value)}
        />

        <div className="review-next-action-banner">
          <div>
            <strong>Confirmation lane</strong>
            <p className="subtle-text" style={{ marginTop: '.2rem' }}>
              {nextFrontPageSubmission
                ? 'Confirm this submission, then jump straight to the next front-page totals paper. Saved output is what results/export will use.'
                : 'Confirm this final paper, then export the class table. Saved output is what results/export will use.'}
            </p>
            <p className="subtle-text" style={{ marginTop: '.35rem' }}>Shortcuts: Ctrl/Cmd+S confirms this paper. Ctrl/Cmd+Enter confirms and advances. Alt+N jumps to the next queued paper only when there are no unsaved teacher edits.</p>
          </div>
          <div className="actions-row" style={{ marginTop: 0 }}>
            <button type="button" className="btn btn-secondary" onClick={() => void save(false)} disabled={saving}>{saving ? 'Saving…' : 'Confirm totals'}</button>
            {queueWillBeClearAfterConfirm && hasSavedTotals && (
              <button type="button" className="btn btn-secondary" onClick={() => void onExportClassSummary()} disabled={isExportingSummary || saving || formIsEdited}>
                {isExportingSummary ? 'Exporting…' : 'Export class summary CSV'}
              </button>
            )}
            <button type="button" className="btn btn-primary" onClick={() => void save(true)} disabled={saving}>
              {saving ? 'Saving…' : nextFrontPageSubmission ? `Confirm + next (${nextFrontPageSubmission!.student_name})` : 'Confirm + finish queue'}
            </button>
          </div>
        </div>
      </section>
      )}
    </div>
  );
}
