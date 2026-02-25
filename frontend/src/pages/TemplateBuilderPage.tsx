import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { RegionCanvas } from '../components/RegionCanvas';
import { useToast } from '../components/ToastProvider';
import type { QuestionRead, Region, SubmissionRead } from '../types/api';

export function TemplateBuilderPage() {
  const params = useParams();
  const submissionId = Number(params.submissionId);
  const [searchParams] = useSearchParams();
  const examId = Number(searchParams.get('examId'));

  const [questions, setQuestions] = useState<QuestionRead[]>([]);
  const [submission, setSubmission] = useState<SubmissionRead | null>(null);
  const [selectedQuestionId, setSelectedQuestionId] = useState<number | null>(null);
  const [selectedPageNumber, setSelectedPageNumber] = useState<number>(1);
  const [regions, setRegions] = useState<Region[]>([]);
  const { showError, showSuccess } = useToast();

  useEffect(() => {
    const load = async () => {
      try {
        const [questionData, submissionData] = await Promise.all([
          api.listQuestions(examId),
          api.getSubmission(submissionId),
        ]);
        setQuestions(questionData);
        setSubmission(submissionData);
        if (questionData.length > 0) {
          setSelectedQuestionId(questionData[0].id);
          setRegions(questionData[0].regions);
        }
        if (submissionData.pages.length > 0) {
          setSelectedPageNumber(submissionData.pages[0].page_number);
        }
      } catch (error) {
        showError(error instanceof Error ? error.message : 'Failed to load template builder');
      }
    };
    if (submissionId && examId) {
      void load();
    }
  }, [submissionId, examId]);

  const selectedQuestion = useMemo(
    () => questions.find((question) => question.id === selectedQuestionId) || null,
    [questions, selectedQuestionId],
  );

  useEffect(() => {
    if (selectedQuestion) {
      setRegions(selectedQuestion.regions.filter((r) => r.page_number === selectedPageNumber));
    }
  }, [selectedQuestionId, selectedPageNumber, questions]);

  const saveRegions = async () => {
    if (!selectedQuestion) return;
    try {
      const byOtherPages = selectedQuestion.regions.filter((region) => region.page_number !== selectedPageNumber);
      const payload = [
        ...byOtherPages,
        ...regions.map((region) => ({
          page_number: selectedPageNumber,
          x: region.x,
          y: region.y,
          w: region.w,
          h: region.h,
        })),
      ];
      const saved = await api.saveRegions(selectedQuestion.id, payload);
      setQuestions((prev) => prev.map((q) => (q.id === selectedQuestion.id ? { ...q, regions: saved } : q)));
      showSuccess('Regions saved');
    } catch (error) {
      showError(error instanceof Error ? error.message : 'Failed to save regions');
    }
  };

  if (!submission) return <p>Loading...</p>;
  const pageUrl = api.getPageImageUrl(submission.id, selectedPageNumber);

  return (
    <div>
      <p><Link to={`/submissions/${submission.id}`}>← Back to Submission</Link></p>
      <h1>Template Builder</h1>
      <div className="actions-row">
        <label htmlFor="template-question">
          Question:
          <select
            id="template-question"
            value={selectedQuestionId ?? ''}
            onChange={(e) => setSelectedQuestionId(Number(e.target.value))}
          >
            {questions.map((question) => (
              <option key={question.id} value={question.id}>{question.label}</option>
            ))}
          </select>
        </label>
        <label htmlFor="template-page">
          Page:
          <select
            id="template-page"
            value={selectedPageNumber}
            onChange={(e) => setSelectedPageNumber(Number(e.target.value))}
          >
            {submission.pages.map((page) => (
              <option key={page.id} value={page.page_number}>Page {page.page_number}</option>
            ))}
          </select>
        </label>
        <button type="button" className="button-primary" onClick={saveRegions} disabled={!selectedQuestion}>Save Regions</button>
      </div>

      {submission.pages.length === 0 ? (
        <p>No pages available. Build pages first.</p>
      ) : (
        <RegionCanvas imageUrl={pageUrl} regions={regions} onChange={setRegions} />
      )}
    </div>
  );
}
