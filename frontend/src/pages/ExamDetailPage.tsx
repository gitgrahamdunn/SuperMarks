import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import { uploadToBlob } from '../blob/upload';
import { useToast } from '../components/ToastProvider';
import type { BulkUploadPreview, ExamDetail, ParseLatestResponse, QuestionRead, StoredFileRead, SubmissionRead } from '../types/api';

const PARSE_BATCH_SIZE = 3;

export function ExamDetailPage() {
  const params = useParams();
  const examId = Number(params.examId);
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [questions, setQuestions] = useState<QuestionRead[]>([]);
  const [submissions, setSubmissions] = useState<SubmissionRead[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [keyFiles, setKeyFiles] = useState<StoredFileRead[]>([]);
  const [latestParse, setLatestParse] = useState<ParseLatestResponse['job'] | null>(null);
  const [isProcessingParse, setIsProcessingParse] = useState(false);
  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [rosterText, setRosterText] = useState('');
  const [preview, setPreview] = useState<BulkUploadPreview | null>(null);
  const [activeCandidateId, setActiveCandidateId] = useState<string>('');
  const [isUploading, setIsUploading] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);
  const [studentName, setStudentName] = useState('');
  const [studentFiles, setStudentFiles] = useState<File[]>([]);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const { showError, showSuccess, showWarning } = useToast();

  const loadDetail = async () => {
    setIsLoading(true);
    try {
      const [examDetail, fetchedQuestions, uploadedKeyFiles, examSubmissions, latestParseJob] = await Promise.all([
        api.getExamDetail(examId),
        api.listQuestions(examId),
        api.listExamKeyFiles(examId),
        api.listExamSubmissions(examId),
        api.getExamKeyParseLatest(examId),
      ]);
      setDetail(examDetail);
      setQuestions(fetchedQuestions);
      setKeyFiles(uploadedKeyFiles);
      setSubmissions(examSubmissions);
      setLatestParse(latestParseJob.job);
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

  const activeCandidate = useMemo(
    () => preview?.candidates.find((candidate) => candidate.candidate_id === activeCandidateId) ?? preview?.candidates[0] ?? null,
    [preview, activeCandidateId],
  );

  const hasQuestions = questions.length > 0;
  const parseDone = latestParse?.status === 'done';
  const parseHasFailedPages = (latestParse?.failed_pages.length || 0) > 0;
  const parseHasPendingPages = (latestParse?.pending_pages.length || 0) > 0;

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

  const onUploadBulk = async () => {
    if (!bulkFile) {
      showWarning('Select a PDF file first');
      return;
    }
    setIsUploading(true);
    setElapsedSeconds(0);
    setProgressMessage('Rendering pages...');
    try {
      const nextPreview = await api.uploadBulkSubmissionsPdf(examId, bulkFile, rosterText);
      setProgressMessage(`Extracting names (${nextPreview.page_count}/${nextPreview.page_count})...`);
      setPreview(nextPreview);
      setActiveCandidateId(nextPreview.candidates[0]?.candidate_id || '');
      showSuccess(`Detected ${nextPreview.candidates.length} students`);
    } catch (error) {
      if (error instanceof ApiError) {
        showError(`Bulk upload failed: ${error.message}`);
      } else {
        showError(error instanceof Error ? error.message : 'Failed to upload bulk PDF');
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
      await loadDetail();
      navigate(`/exams/${examId}`);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to finalize bulk upload');
    } finally {
      setIsFinalizing(false);
    }
  };

  const onUploadStudentSubmission = async () => {
    if (!studentName.trim() || studentFiles.length === 0) {
      showWarning('Provide student name and at least one file');
      return;
    }

    try {
      const submission = await api.createSubmission(examId, studentName.trim());
      const { token } = await api.getBlobUploadToken();
      const uploaded = await Promise.all(
        studentFiles.map((file) => uploadToBlob(file, `exams/${examId}/submissions/${submission.id}/${crypto.randomUUID()}-${file.name}`, token)),
      );
      await api.registerSubmissionFiles(
        submission.id,
        uploaded.map((file, index) => ({
          original_filename: studentFiles[index].name,
          blob_pathname: file.pathname,
          content_type: file.contentType,
          size_bytes: file.size,
        })),
      );

      showSuccess('Submission files uploaded');
      setStudentName('');
      setStudentFiles([]);
      await loadDetail();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to upload submission files');
    }
  };

  if (isLoading) return <p>Loading exam details…</p>;

  if (notFound) {
    return (
      <div className="card stack">
        <h1>Exam unavailable</h1>
        <p>This exam record no longer exists.</p>
        <p>
          <Link className="btn btn-secondary" to="/">Back to Exams</Link>
        </p>
      </div>
    );
  }

  if (!detail) return <p>Unable to load exam details.</p>;

  return (
    <div>
      <p><Link to="/">← Back to Exams</Link></p>
      <h1>{detail.exam.name}</h1>

      {latestParse && (
        <section className="card stack" style={{ marginBottom: 16 }}>
          <h2>Answer Key Processing</h2>
          <p className="subtle-text">Parsing can continue later from the exam page.</p>
          <p>Status: <strong>{latestParse.status}</strong></p>
          <p>Progress: {latestParse.pages_done}/{latestParse.page_count}</p>
          <p>Failed pages: {latestParse.failed_pages.length}</p>
          <p>Pending pages: {latestParse.pending_pages.length}</p>
          <p>Updated: {new Date(latestParse.updated_at).toLocaleString()}</p>
          {latestParse.totals && (
            <p className="subtle-text">
              Totals — cost: ${latestParse.totals.cost_total.toFixed(6)}, input tokens: {latestParse.totals.input_tokens_total}, output tokens: {latestParse.totals.output_tokens_total}
            </p>
          )}
          {parseHasFailedPages && (
            <p className="warning-text">Failed pages: {latestParse.failed_pages.join(', ')}</p>
          )}
          {parseHasPendingPages && (
            <p className="subtle-text">Pending pages: {latestParse.pending_pages.join(', ')}</p>
          )}
          <div className="actions-row">
            <button type="button" className="btn btn-secondary" onClick={() => void onResumeParsing()} disabled={isProcessingParse}>
              Resume parsing
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void onRetryFailedPages()}
              disabled={isProcessingParse || !parseHasFailedPages}
            >
              Retry failed pages
            </button>
            <button type="button" className={parseDone && hasQuestions ? 'btn btn-primary' : 'btn btn-secondary'} onClick={() => navigate(`/exams/${examId}/review`)} disabled={!hasQuestions}>
              Review criteria
            </button>
            <button type="button" className="btn btn-secondary" onClick={scrollToKeyFiles}>
              View key files
            </button>
          </div>
        </section>
      )}

      <div className="grid-2">
        <section className="card" id="answer-key-files">
          <h2>Answer Key Files</h2>
          {keyFiles.length === 0 && <p className="subtle-text">No key files uploaded yet.</p>}
          {keyFiles.length > 0 && (
            <ul>
              {keyFiles.map((file) => (
                <li key={file.id}>{file.original_filename}</li>
              ))}
            </ul>
          )}
        </section>

        <section className="card">
          <h2>Parse Status</h2>
          {!latestParse && <p>No parse jobs yet</p>}
          {latestParse && <p>{latestParse.status} ({latestParse.pages_done}/{latestParse.page_count} pages)</p>}
        </section>
      </div>

      <div className="grid-2">
        <section className="card">
          <h2>Questions</h2>
          <p className="subtle-text">Questions are generated from the exam key.</p>
          {questions.length === 0 && <p className="subtle-text">No questions parsed yet.</p>}
          <ul>
            {questions.map((question) => (
              <li key={question.id}>{question.label} (max: {question.max_marks})</li>
            ))}
          </ul>
        </section>

        <section className="card">
          <h2>Submissions</h2>
          {submissions.length === 0 && <p className="subtle-text">No submissions yet.</p>}
          <ul>
            {submissions.map((submission) => (
              <li key={submission.id}>
                <Link to={`/submissions/${submission.id}`}>{submission.student_name}</Link> ({submission.status})
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="card stack" style={{ marginTop: 16 }}>
        <h2>Upload Student Submission</h2>
        <input value={studentName} onChange={(event) => setStudentName(event.target.value)} placeholder="Student name" />
        <input type="file" multiple onChange={(event) => setStudentFiles(Array.from(event.target.files || []))} />
        <button type="button" className="btn btn-primary" onClick={onUploadStudentSubmission}>Upload Student Files</button>
      </section>

      <section className="card stack" style={{ marginTop: 16 }}>
        <h2>Bulk Upload Student Tests (PDF)</h2>
        <input type="file" accept="application/pdf" onChange={(event) => setBulkFile(event.target.files?.[0] || null)} />
        <textarea
          placeholder="Optional roster: one student name per line"
          rows={4}
          value={rosterText}
          onChange={(event) => setRosterText(event.target.value)}
        />
        <button type="button" className="btn btn-primary" onClick={onUploadBulk} disabled={isUploading}>
          {isUploading ? 'Uploading...' : 'Upload Bulk PDF'}
        </button>
        {isUploading && (
          <div>
            <progress max={100} value={60} />
            <p>{progressMessage} Elapsed: {elapsedSeconds}s</p>
          </div>
        )}
      </section>

      {preview && (
        <section className="card stack" style={{ marginTop: 16 }}>
          <h3>Bulk upload preview</h3>
          {preview.warnings.length > 0 && (
            <ul>
              {preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          )}
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Confidence</th>
                <th>Page start</th>
                <th>Page end</th>
                <th>Review</th>
              </tr>
            </thead>
            <tbody>
              {preview.candidates.map((candidate) => (
                <tr key={candidate.candidate_id} onClick={() => setActiveCandidateId(candidate.candidate_id)} style={{ cursor: 'pointer' }}>
                  <td><input value={candidate.student_name} onChange={(event) => updateCandidate(candidate.candidate_id, { student_name: event.target.value })} /></td>
                  <td>{Math.round(candidate.confidence * 100)}%</td>
                  <td><input type="number" min={1} value={candidate.page_start} onChange={(event) => updateCandidate(candidate.candidate_id, { page_start: Number(event.target.value) })} /></td>
                  <td><input type="number" min={1} value={candidate.page_end} onChange={(event) => updateCandidate(candidate.candidate_id, { page_end: Number(event.target.value) })} /></td>
                  <td>{candidate.needs_review ? 'Needs review' : 'OK'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {activeCandidate && (
            <div>
              <h4>Candidate preview</h4>
              <img
                alt={`Candidate ${activeCandidate.student_name} first page`}
                src={api.getBulkUploadPageUrl(examId, preview.bulk_upload_id, activeCandidate.page_start)}
                style={{ maxWidth: '100%', border: '1px solid #ddd' }}
              />
              {activeCandidate.name_evidence && (
                <p>
                  Evidence box (normalized): x={activeCandidate.name_evidence.x.toFixed(2)}, y={activeCandidate.name_evidence.y.toFixed(2)}, w={activeCandidate.name_evidence.w.toFixed(2)}, h={activeCandidate.name_evidence.h.toFixed(2)}
                </p>
              )}
            </div>
          )}

          <button type="button" className="btn btn-primary" onClick={onFinalize} disabled={isFinalizing}>
            {isFinalizing ? 'Finalizing...' : 'Finalize'}
          </button>
        </section>
      )}
    </div>
  );
}
