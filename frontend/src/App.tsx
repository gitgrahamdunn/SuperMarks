import { Component, ErrorInfo, ReactNode } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { ExamsPage } from './pages/ExamsPage';
import { ExamDetailPage } from './pages/ExamDetailPage';
import { SubmissionDetailPage } from './pages/SubmissionDetailPage';
import { TemplateBuilderPage } from './pages/TemplateBuilderPage';
import { ResultsPage } from './pages/ResultsPage';
import { ExamReviewPage } from './pages/ExamReviewPage';

type RootErrorBoundaryState = {
  error: Error | null;
};

class RootErrorBoundary extends Component<{ children: ReactNode }, RootErrorBoundaryState> {
  state: RootErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): RootErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('Root error boundary caught an error', error, errorInfo);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app-error-screen">
          <div className="app-error-card">
            <h1>Something went wrong</h1>
            <p>The app hit an unexpected error, but your data is still safe.</p>
            <p className="subtle-text">{this.state.error.message || 'Unknown error'}</p>
            <button type="button" onClick={() => window.location.reload()}>Reload</button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default function App() {
  return (
    <RootErrorBoundary>
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
    </RootErrorBoundary>
  );
}
