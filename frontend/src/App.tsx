import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { Modal } from './components/Modal';
import { clearClientLogs, getClientLogs, hasCapturedClientErrors, subscribeToClientLogs } from './logs/clientLogStore';

const ExamsPage = lazy(async () => ({ default: (await import('./pages/ExamsPage')).ExamsPage }));
const ExamDetailPage = lazy(async () => ({ default: (await import('./pages/ExamDetailPage')).ExamDetailPage }));
const ExamReviewPage = lazy(async () => ({ default: (await import('./pages/ExamReviewPage')).ExamReviewPage }));
const SubmissionDetailPage = lazy(async () => ({ default: (await import('./pages/SubmissionDetailPage')).SubmissionDetailPage }));
const TemplateBuilderPage = lazy(async () => ({ default: (await import('./pages/TemplateBuilderPage')).TemplateBuilderPage }));
const ResultsPage = lazy(async () => ({ default: (await import('./pages/ResultsPage')).ResultsPage }));

type Theme = 'light' | 'dark';
const THEME_STORAGE_KEY = 'supermarks-theme';

function resolveFrontendVersionLabel(): string {
  const configured = import.meta.env.VITE_APP_VERSION?.trim() || import.meta.env.VITE_BUILD_ID?.trim();
  if (configured) {
    return configured;
  }
  return `v${import.meta.env.MODE} (build ${new Date(__APP_BUILD_TS__).toISOString()})`;
}

function getInitialTheme(): Theme {
  const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  return storedTheme === 'dark' ? 'dark' : 'light';
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => getInitialTheme());
  const [logsOpen, setLogsOpen] = useState(false);
  const [clientLogs, setClientLogs] = useState(() => getClientLogs());
  const [showClientErrorBanner, setShowClientErrorBanner] = useState(() => hasCapturedClientErrors());
  const frontendVersion = useMemo(() => resolveFrontendVersionLabel(), []);
  const location = useLocation();

  useEffect(() => {
    document.body.classList.toggle('theme-dark', theme === 'dark');
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => subscribeToClientLogs(() => {
    setClientLogs(getClientLogs());
    setShowClientErrorBanner(hasCapturedClientErrors());
  }), []);

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
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.25rem' }}>
          <div className="actions-row" style={{ marginTop: 0 }}>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setLogsOpen(true)}
            >
              Open Logs ({clientLogs.length})
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setTheme((prev) => (prev === 'light' ? 'dark' : 'light'))}
              aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
            >
              Theme: {theme}
            </button>
          </div>
          <small className="subtle-text">{frontendVersion}</small>
        </div>
      </header>

      {location.pathname === '/' && showClientErrorBanner && (
        <p className="warning-text">
          A client error occurred. Open Logs for details.
        </p>
      )}

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

      {logsOpen && (
        <Modal title="Client Logs" onClose={() => setLogsOpen(false)}>
          <div className="stack">
            <h2 style={{ margin: 0 }}>Client Logs</h2>
            <p className="subtle-text" style={{ margin: 0 }}>
              Captures window errors and unhandled promise rejections for frontend debugging.
            </p>
            <div className="actions-row" style={{ marginTop: 0 }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => {
                  clearClientLogs();
                  setShowClientErrorBanner(false);
                }}
              >
                Clear logs
              </button>
              <button type="button" className="btn btn-primary" onClick={() => setLogsOpen(false)}>
                Close
              </button>
            </div>
            <div className="client-log-list">
              {clientLogs.length === 0 && <p className="subtle-text">No client logs captured yet.</p>}
              {clientLogs.map((entry, index) => (
                <article key={`${entry.timestamp}-${entry.type}-${index}`} className="client-log-item">
                  <p><strong>{entry.type}</strong>{entry.count && entry.count > 1 ? ` (x${entry.count})` : ''}</p>
                  <p>{entry.message}</p>
                  <p className="subtle-text">{new Date(entry.timestamp).toLocaleString()}</p>
                  {entry.filename && (
                    <p className="subtle-text">
                      {entry.filename}:{entry.lineno ?? '?'}:{entry.colno ?? '?'}
                    </p>
                  )}
                  {entry.stack && <pre>{entry.stack}</pre>}
                </article>
              ))}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
