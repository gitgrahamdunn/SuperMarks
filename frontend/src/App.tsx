import { NavLink, Route, Routes } from 'react-router-dom';
import { ExamsPage } from './pages/ExamsPage';
import { ExamDetailPage } from './pages/ExamDetailPage';
import { SubmissionDetailPage } from './pages/SubmissionDetailPage';
import { TemplateBuilderPage } from './pages/TemplateBuilderPage';
import { ResultsPage } from './pages/ResultsPage';
import { ExamReviewPage } from './pages/ExamReviewPage';

export default function App() {
  return (
    <div className="layout">
      <header>
        <NavLink to="/" className="brand">SuperMarks MVP</NavLink>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ExamsPage />} />
          <Route path="/exams/:examId" element={<ExamDetailPage />} />
          <Route path="/exams/:examId/review" element={<ExamReviewPage />} />
          <Route path="/submissions/:submissionId" element={<SubmissionDetailPage />} />
          <Route path="/submissions/:submissionId/template-builder" element={<TemplateBuilderPage />} />
          <Route path="/submissions/:submissionId/results" element={<ResultsPage />} />
        </Routes>
      </main>
    </div>
  );
}
