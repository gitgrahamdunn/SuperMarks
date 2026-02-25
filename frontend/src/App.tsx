import { Suspense, lazy, useEffect, useState } from 'react';
import { NavLink, Route, Routes, useLocation } from 'react-router-dom';

const ExamsPage = lazy(() => import('./pages/ExamsPage').then((module) => ({ default: module.ExamsPage })));
const ExamDetailPage = lazy(() => import('./pages/ExamDetailPage').then((module) => ({ default: module.ExamDetailPage })));
const ExamReviewPage = lazy(() => import('./pages/ExamReviewPage').then((module) => ({ default: module.ExamReviewPage })));
const SubmissionDetailPage = lazy(() => import('./pages/SubmissionDetailPage').then((module) => ({ default: module.SubmissionDetailPage })));
const TemplateBuilderPage = lazy(() => import('./pages/TemplateBuilderPage').then((module) => ({ default: module.TemplateBuilderPage })));
const ResultsPage = lazy(() => import('./pages/ResultsPage').then((module) => ({ default: module.ResultsPage })));

type Theme = 'light' | 'dark';

function getStoredTheme(): Theme {
  const stored = localStorage.getItem('supermarks-theme');
  return stored === 'dark' ? 'dark' : 'light';
}

export default function App() {
  const location = useLocation();
  const [theme, setTheme] = useState<Theme>(() => getStoredTheme());
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    document.body.classList.toggle('theme-dark', theme === 'dark');
    localStorage.setItem('supermarks-theme', theme);
  }, [theme]);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  const reviewPath = location.pathname.includes('/review') ? location.pathname : null;

  return (
    <div className="layout">
      <a href="#main-content" className="skip-link">Skip to content</a>
      <header className="top-nav card">
        <NavLink to="/" className="brand">SuperMarks</NavLink>
        <button
          type="button"
          className="mobile-nav-toggle button-secondary"
          onClick={() => setNavOpen((prev) => !prev)}
          aria-expanded={navOpen}
          aria-controls="main-navigation"
        >
          Menu
        </button>
        <nav id="main-navigation" className={`nav-links ${navOpen ? 'is-open' : ''}`} aria-label="Primary">
          <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'is-active' : ''}`}>Exams</NavLink>
          {reviewPath && (
            <NavLink to={reviewPath} className={({ isActive }) => `nav-link ${isActive ? 'is-active' : ''}`}>
              Review
            </NavLink>
          )}
        </nav>
        <button
          type="button"
          className="button-secondary theme-toggle"
          onClick={() => setTheme((prev) => (prev === 'light' ? 'dark' : 'light'))}
          aria-label="Toggle theme"
        >
          Theme: {theme === 'light' ? 'Light' : 'Dark'}
        </button>
      </header>
      <main id="main-content" tabIndex={-1}>
        <Suspense fallback={<div className="card">Loading page…</div>}>
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
