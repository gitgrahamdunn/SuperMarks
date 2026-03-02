import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { StoredFileRead, SubmissionRead } from '../types/api';

const statusOrder = ['UPLOADED', 'PAGES_READY', 'CROPS_READY', 'TRANSCRIBED', 'GRADED'];

export function SubmissionDetailPage() {
  const params = useParams();
  const submissionId = Number(params.submissionId);
  const [submission, setSubmission] = useState<SubmissionRead | null>(null);
  const [storedFiles, setStoredFiles] = useState<StoredFileRead[]>([]);
  const { showError, showSuccess } = useToast();

  const loadSubmission = async () => {
    try {
      const [submissionDetail, files] = await Promise.all([api.getSubmission(submissionId), api.listSubmissionFiles(submissionId)]);
      setSubmission(submissionDetail);
      setStoredFiles(files);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to load submission');
    }
  };

  useEffect(() => {
    if (submissionId) {
      void loadSubmission();
    }
  }, [submissionId]);

  const currentStep = useMemo(() => statusOrder.indexOf(submission?.status || ''), [submission?.status]);

  const runAction = async (action: 'build-pages' | 'build-crops' | 'transcribe' | 'grade') => {
    try {
      if (action === 'build-pages') await api.buildPages(submissionId);
      if (action === 'build-crops') await api.buildCrops(submissionId);
      if (action === 'transcribe') await api.transcribe(submissionId);
      if (action === 'grade') await api.grade(submissionId);
      showSuccess(`Action ${action} completed`);
      await loadSubmission();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Action failed');
    }
  };


  const openSignedFile = async (pathname: string) => {
    try {
      const result = await api.getSignedBlobUrl(pathname);
      window.open(result.url, '_blank', 'noopener,noreferrer');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to open file');
    }
  };

  if (!submission) return <p>Loading...</p>;

  return (
    <div>
      <h1>Submission: {submission.student_name}</h1>
      <p>Status: <strong>{submission.status}</strong></p>

      <div className="actions-row">
        <button onClick={() => runAction('build-pages')} disabled={currentStep > 0}>Build Pages</button>
        <button onClick={() => runAction('build-crops')} disabled={currentStep < 1}>Build Crops</button>
        <button onClick={() => runAction('transcribe')} disabled={currentStep < 2}>Transcribe</button>
        <button onClick={() => runAction('grade')} disabled={currentStep < 3}>Grade</button>
      </div>

      <div className="actions-row">
        <Link to={`/submissions/${submission.id}/template-builder?examId=${submission.exam_id}`}>Template Builder</Link>
        <Link to={`/submissions/${submission.id}/results?examId=${submission.exam_id}`}>Results</Link>
      </div>


      <h2>Uploaded Files</h2>
      <ul>
        {submission.files.map((file) => (
          <li key={file.id}>
            {file.original_filename}
            <button type="button" onClick={() => void openSignedFile(file.stored_path)}>View</button>
          </li>
        ))}
      </ul>

      <h2>Pages</h2>
      {submission.pages.length === 0 && <p>No pages yet. Build pages first.</p>}
      <div className="thumb-grid">
        {submission.pages.map((page) => (
          <img key={page.id} src={api.getPageImageUrl(submission.id, page.page_number)} alt={`Page ${page.page_number}`} className="thumb" />
        ))}
      </div>
    </div>
  );
}
