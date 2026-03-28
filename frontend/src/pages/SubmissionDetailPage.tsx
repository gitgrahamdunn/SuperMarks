import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useToast } from '../components/ToastProvider';
import type { SubmissionRead } from '../types/api';

const statusOrder = ['UPLOADED', 'PAGES_READY', 'CROPS_READY', 'TRANSCRIBED', 'GRADED'];

export function SubmissionDetailPage() {
  const params = useParams();
  const submissionId = Number(params.submissionId);
  const [searchParams] = useSearchParams();
  const [submission, setSubmission] = useState<SubmissionRead | null>(null);
  const returnTo = searchParams.get('returnTo')?.trim();
  const returnLabel = searchParams.get('returnLabel')?.trim() || 'Back to exam workspace';
  const { showError, showSuccess } = useToast();

  const loadSubmission = async () => {
    try {
      const submissionDetail = await api.getSubmission(submissionId);
      setSubmission(submissionDetail);
    } catch (error) {
      showError(error instanceof Error ? error.message : 'We couldn’t load this submission.');
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
      showSuccess('The requested step is complete.');
      await loadSubmission();
    } catch (error) {
      showError(error instanceof Error ? error.message : 'We couldn’t complete that step.');
    }
  };


  const openSignedFile = async (pathname: string) => {
    try {
      const result = await api.getSignedBlobUrl(pathname);
      window.open(result.url, '_blank', 'noopener,noreferrer');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'We couldn’t open that file.');
    }
  };

  if (!submission) return <p>Loading submission…</p>;

  return (
    <div className="workflow-shell">
      <section className="card card--hero stack">
        <p style={{ margin: 0 }}><Link to={returnTo || `/exams/${submission.exam_id}`}>← {returnTo ? returnLabel : 'Back to Exam'}</Link></p>
        <div className="page-header">
          <div>
            <p className="page-eyebrow">Submission</p>
            <h1 className="page-title">{submission.student_name}</h1>
            <p className="page-subtitle">Inspect pipeline readiness, verify the uploaded source pages, and stay clear about whether this paper belongs to the front-page totals lane or the question-level marking lane.</p>
          </div>
          <div className="page-toolbar">
            <span className={`status-pill ${currentStep >= 4 ? 'status-complete' : currentStep >= 2 ? 'status-in-progress' : 'status-ready'}`}>{submission.status}</span>
            <span className="status-pill status-neutral">{submission.capture_mode === 'front_page_totals' ? 'Front-page totals' : 'Question-level'}</span>
            <Link className="btn btn-primary" to={submission.capture_mode === 'front_page_totals' ? `/submissions/${submission.id}/front-page-totals?examId=${submission.exam_id}` : `/submissions/${submission.id}/mark?examId=${submission.exam_id}`}>
              {submission.capture_mode === 'front_page_totals' ? 'Front-page totals' : 'Marking workspace'}
            </Link>
          </div>
        </div>
        <div className="submission-stage-track">
          {statusOrder.map((status, index) => (
            <article
              key={status}
              className={`stage-card ${index < currentStep ? 'is-complete' : ''} ${index === currentStep ? 'is-current' : ''} ${index > currentStep ? 'is-upcoming' : ''}`}
            >
              <p className="stage-name">Stage {index + 1}</p>
              <p className="stage-value">{status.replace('_', ' ')}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">Workflow lane</h2>
            <p className="subtle-text">
              {submission.capture_mode === 'front_page_totals'
                ? 'This paper belongs to the front-page totals lane. Stay in totals capture and confirmation instead of drifting into question-level prep steps.'
                : 'This paper belongs to the question-level lane. Prepare pages/crops/transcription as needed, then finish teacher entry question by question.'}
            </p>
          </div>
          <span className={`status-pill ${submission.capture_mode === 'front_page_totals' ? 'status-ready' : 'status-in-progress'}`}>
            {submission.capture_mode === 'front_page_totals' ? 'Front-page totals workflow' : 'Question-level workflow'}
          </span>
        </div>
        <div className="actions-row">
          <Link className="btn btn-primary" to={submission.capture_mode === 'front_page_totals' ? `/submissions/${submission.id}/front-page-totals?examId=${submission.exam_id}` : `/submissions/${submission.id}/mark?examId=${submission.exam_id}`}>
            {submission.capture_mode === 'front_page_totals'
              ? (submission.front_page_totals ? 'Review front-page totals' : 'Capture front-page totals')
              : 'Open marking workspace'}
          </Link>
          <Link className="btn btn-secondary" to={`/submissions/${submission.id}/results?examId=${submission.exam_id}`}>Results</Link>
        </div>
      </section>

      <section className="card stack">
        <div className="panel-title-row">
          <div>
            <h2 className="section-title">Pipeline actions</h2>
            <p className="subtle-text">Run a missing step manually when you need to unblock a submission.</p>
          </div>
        </div>
        <div className="actions-row">
          <button className="btn btn-secondary" onClick={() => runAction('build-pages')} disabled={currentStep > 0 || submission.capture_mode === 'front_page_totals'}>Build pages</button>
          <button className="btn btn-secondary" onClick={() => runAction('build-crops')} disabled={currentStep < 1 || submission.capture_mode === 'front_page_totals'}>Build crops</button>
          <button className="btn btn-secondary" onClick={() => runAction('transcribe')} disabled={currentStep < 2 || submission.capture_mode === 'front_page_totals'}>Transcribe</button>
          <button className="btn btn-primary" onClick={() => runAction('grade')} disabled={currentStep < 3 || submission.capture_mode === 'front_page_totals'}>Grade</button>
        </div>
        <div className="actions-row">
          <Link className="btn btn-secondary" to={submission.capture_mode === 'front_page_totals' ? `/submissions/${submission.id}/front-page-totals?examId=${submission.exam_id}` : `/submissions/${submission.id}/mark?examId=${submission.exam_id}`}>
            {submission.capture_mode === 'front_page_totals' ? 'Front-page totals' : 'Marking Workspace'}
          </Link>
          {submission.capture_mode !== 'front_page_totals' && <Link className="btn btn-secondary" to={`/submissions/${submission.id}/template-builder?examId=${submission.exam_id}`}>Template Builder</Link>}
          <Link className="btn btn-secondary" to={`/submissions/${submission.id}/results?examId=${submission.exam_id}`}>Results</Link>
        </div>
      </section>

      <div className="workflow-grid">
        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Uploaded files</h2>
              <p className="subtle-text">Original student upload sources.</p>
            </div>
            <span className="status-pill status-ready">{submission.files.length} file{submission.files.length === 1 ? '' : 's'}</span>
          </div>
          <ul className="file-list-clean">
            {submission.files.map((file) => (
              <li key={file.id}>
                <span>{file.original_filename}</span>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => void openSignedFile(file.stored_path)}>View</button>
              </li>
            ))}
          </ul>
        </section>

        <section className="card stack">
          <div className="panel-title-row">
            <div>
              <h2 className="section-title">Pages</h2>
              <p className="subtle-text">Rendered pages available for crop extraction and review.</p>
            </div>
            <span className={`status-pill ${submission.pages.length > 0 ? 'status-ready' : 'status-blocked'}`}>{submission.pages.length} page{submission.pages.length === 1 ? '' : 's'}</span>
          </div>
          {submission.pages.length === 0 && <p className="subtle-text">No pages yet. Build pages first.</p>}
          <div className="thumb-grid">
            {submission.pages.map((page) => (
              <img key={page.id} src={api.getPageImageUrl(submission.id, page.page_number)} alt={`Page ${page.page_number}`} className="thumb" />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
