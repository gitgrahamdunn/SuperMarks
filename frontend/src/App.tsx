import { Suspense, lazy, useEffect, useState } from 'react';
import { NavLink, Route, Routes, useLocation } from 'react-router-dom';

const ExamsPage = lazy(async () => ({ default: (await import('./pages/ExamsPage')).ExamsPage }));
const ClassListsPage = lazy(async () => ({ default: (await import('./pages/ClassListsPage')).ClassListsPage }));
const ExamDetailPage = lazy(async () => ({ default: (await import('./pages/ExamDetailPage')).ExamDetailPage }));
const ExamReviewPage = lazy(async () => ({ default: (await import('./pages/ExamReviewPage')).ExamReviewPage }));
const SubmissionDetailPage = lazy(async () => ({ default: (await import('./pages/SubmissionDetailPage')).SubmissionDetailPage }));
const SubmissionMarkingPage = lazy(async () => ({ default: (await import('./pages/SubmissionMarkingPage')).SubmissionMarkingPage }));
const SubmissionFrontPageTotalsPage = lazy(async () => ({ default: (await import('./pages/SubmissionFrontPageTotalsPage')).SubmissionFrontPageTotalsPage }));
const TemplateBuilderPage = lazy(async () => ({ default: (await import('./pages/TemplateBuilderPage')).TemplateBuilderPage }));
const ResultsPage = lazy(async () => ({ default: (await import('./pages/ResultsPage')).ResultsPage }));

type Theme = 'light' | 'dark';
const THEME_STORAGE_KEY = 'supermarks-theme';

function getInitialTheme(): Theme {
  const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  return storedTheme === 'dark' ? 'dark' : 'light';
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => getInitialTheme());
  const location = useLocation();
  const isFocusedWorkflowRoute = location.pathname.startsWith('/submissions/');

  useEffect(() => {
    document.body.classList.toggle('theme-dark', theme === 'dark');
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  return (
    <div className="layout app-shell">
      <a href="#main-content" className="skip-link">Skip to content</a>
      <header className={`top-nav ${isFocusedWorkflowRoute ? 'top-nav--focused' : ''}`.trim()}>
        <div className="top-nav-left">
          <div className="brand-lockup">
            <NavLink to="/" className="brand">SuperMarks</NavLink>
          </div>
          {!isFocusedWorkflowRoute && (
            <nav aria-label="Main navigation" className="main-nav-links">
              <NavLink
                to="/"
                end
                className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
              >
                Home
              </NavLink>
              <NavLink
                to="/class-lists"
                className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
              >
                Class lists
              </NavLink>
            </nav>
          )}
        </div>
      </header>

      <main id="main-content" tabIndex={-1}>
        <Suspense fallback={
          <section className="card page-loading-shell" aria-label="Loading page">
            <div className="skeleton page-loading-shell-title" />
            <div className="skeleton page-loading-shell-row" />
            <div className="skeleton page-loading-shell-row page-loading-shell-row--short" />
          </section>
        }>
          <Routes>
            <Route path="/" element={<ExamsPage />} />
            <Route path="/class-lists" element={<ClassListsPage />} />
            <Route path="/exams/:examId" element={<ExamDetailPage />} />
            <Route path="/exams/:examId/review" element={<ExamReviewPage />} />
            <Route path="/submissions/:submissionId" element={<SubmissionDetailPage />} />
            <Route path="/submissions/:submissionId/mark" element={<SubmissionMarkingPage />} />
            <Route path="/submissions/:submissionId/front-page-totals" element={<SubmissionFrontPageTotalsPage />} />
            <Route path="/submissions/:submissionId/template-builder" element={<TemplateBuilderPage />} />
            <Route path="/submissions/:submissionId/results" element={<ResultsPage />} />
          </Routes>
        </Suspense>
      </main>

      {!isFocusedWorkflowRoute && (
        <footer className="app-shell-footer">
          <button
            type="button"
            className={`theme-switch ${theme === 'dark' ? 'is-dark' : ''}`}
            onClick={() => setTheme((prev) => (prev === 'light' ? 'dark' : 'light'))}
            aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
            aria-pressed={theme === 'dark'}
          >
            <span className="theme-switch-icon" aria-hidden="true">{theme === 'light' ? '☀' : '☾'}</span>
            <span className="theme-switch-track" aria-hidden="true">
              <span className="theme-switch-thumb" />
            </span>
          </button>
        </footer>
      )}
    </div>
  );
}
