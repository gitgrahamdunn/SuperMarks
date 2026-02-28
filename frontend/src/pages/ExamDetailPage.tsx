import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { BulkUploadPreview, ExamDetail } from '../types/api';

export function ExamDetailPage() {
  const params = useParams();
  const examId = Number(params.examId);
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [bulkFile, setBulkFile] = useState<File | null>(null);
  const [rosterText, setRosterText] = useState('');
  const [preview, setPreview] = useState<BulkUploadPreview | null>(null);
  const [activeCandidateId, setActiveCandidateId] = useState<string>('');
  const [isUploading, setIsUploading] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const { showError, showSuccess, showWarning } = useToast();

  const loadDetail = async () => {
    try {
      setDetail(await api.getExamDetail(examId));
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to load exam');
    }
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

  if (!detail) return <p>Loading...</p>;

  return (
    <div>
      <p><Link to="/">← Back to Exams</Link></p>
      <h1>{detail.exam.name}</h1>

      <div className="grid-2">
        <section className="card">
          <h2>Questions</h2>
          <p className="subtle-text">Questions are generated from the exam key.</p>
          <ul>
            {detail.questions.map((question) => (
              <li key={question.id}>{question.label} (max: {question.max_marks})</li>
            ))}
          </ul>
        </section>

        <section className="card">
          <h2>Submissions</h2>
          <ul>
            {detail.submissions.map((submission) => (
              <li key={submission.id}>
                <Link to={`/submissions/${submission.id}`}>{submission.student_name}</Link> ({submission.status})
              </li>
            ))}
          </ul>
        </section>
      </div>

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
