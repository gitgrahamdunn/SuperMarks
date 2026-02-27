import { Suspense, lazy, useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';

const ExamsPage = lazy(async () => ({ default: (await import('./pages/ExamsPage')).ExamsPage }));
const ExamDetailPage = lazy(async () => ({ default: (await import('./pages/ExamDetailPage')).ExamDetailPage }));
const ExamReviewPage = lazy(async () => ({ default: (await import('./pages/ExamReviewPage')).ExamReviewPage }));
const SubmissionDetailPage = lazy(async () => ({ default: (await import('./pages/SubmissionDetailPage')).SubmissionDetailPage }));
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

  useEffect(() => {
    document.body.classList.toggle('theme-dark', theme === 'dark');
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  return (
    <div className="layout">
      <a href="#main-content" className="skip-link">Skip to content</a>
      <header className="top-nav">
        <div className="top-nav-left">
          <NavLink to="/" className="brand">SuperMarks</NavLink>
          <nav aria-label="Main navigation" className="main-nav-links">
            <NavLink
              to="/"
              end
              className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
            >
              Exams
            </NavLink>
          </nav>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => setTheme((prev) => (prev === 'light' ? 'dark' : 'light'))}
          aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
        >
          Theme: {theme}
        </button>
      </header>

      <main id="main-content" tabIndex={-1}>
        <Suspense fallback={<p className="subtle-text">Loading page…</p>}>
          <Routes>
            <Route path="/" element={<ExamsPage />} />
            <Route path="/exams/:examId" element={<ExamDetailPage />} />
            <Route path="/exams/:examId/review" element={<ExamReviewPage />} />
            <Route path="/submissions/:submissionId" element={<SubmissionDetailPage />} />
            <Route path="/submissions/:submissionId/template-builder" element={<TemplateBuilderPage />} />
            <Route path="/submissions/:submissionId/results" element={<ResultsPage />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
