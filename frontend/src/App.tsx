import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Route, Routes, useLocation, Navigate } from 'react-router-dom';
import { api } from './api/client';
import type { AuthStatusRead } from './types/api';

const ExamsPage = lazy(async () => ({ default: (await import('./pages/ExamsPage')).ExamsPage }));
const ClassListsPage = lazy(async () => ({ default: (await import('./pages/ClassListsPage')).ClassListsPage }));
const ExamDetailPage = lazy(async () => ({ default: (await import('./pages/ExamDetailPage')).ExamDetailPage }));
const ExamReviewPage = lazy(async () => ({ default: (await import('./pages/ExamReviewPage')).ExamReviewPage }));
const SubmissionDetailPage = lazy(async () => ({ default: (await import('./pages/SubmissionDetailPage')).SubmissionDetailPage }));
const SubmissionMarkingPage = lazy(async () => ({ default: (await import('./pages/SubmissionMarkingPage')).SubmissionMarkingPage }));
const SubmissionFrontPageTotalsPage = lazy(async () => ({ default: (await import('./pages/SubmissionFrontPageTotalsPage')).SubmissionFrontPageTotalsPage }));
const TemplateBuilderPage = lazy(async () => ({ default: (await import('./pages/TemplateBuilderPage')).TemplateBuilderPage }));
const ResultsPage = lazy(async () => ({ default: (await import('./pages/ResultsPage')).ResultsPage }));
const LoginPage = lazy(async () => ({ default: (await import('./pages/LoginPage')).LoginPage }));
const AuthCallbackPage = lazy(async () => ({ default: (await import('./pages/AuthCallbackPage')).AuthCallbackPage }));

function preloadRouteModules() {
  void import('./pages/ExamsPage');
  void import('./pages/ClassListsPage');
  void import('./pages/ExamDetailPage');
  void import('./pages/ExamReviewPage');
  void import('./pages/SubmissionDetailPage');
  void import('./pages/SubmissionMarkingPage');
  void import('./pages/SubmissionFrontPageTotalsPage');
  void import('./pages/TemplateBuilderPage');
  void import('./pages/ResultsPage');
  void import('./pages/LoginPage');
  void import('./pages/AuthCallbackPage');
}

type Theme = 'light' | 'dark';
const THEME_STORAGE_KEY = 'supermarks-theme';

function getInitialTheme(): Theme {
  const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  return storedTheme === 'dark' ? 'dark' : 'light';
}

function LoadingShell() {
  return (
    <section className="card page-loading-shell" aria-label="Loading page">
      <div className="skeleton page-loading-shell-title" />
      <div className="skeleton page-loading-shell-row" />
      <div className="skeleton page-loading-shell-row page-loading-shell-row--short" />
    </section>
  );
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => getInitialTheme());
  const [authState, setAuthState] = useState<AuthStatusRead | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const location = useLocation();
  const isFocusedWorkflowRoute = location.pathname.startsWith('/submissions/');
  const isAuthCallbackRoute = location.pathname.startsWith('/auth/callback');
  const isDevLoginRoute = location.pathname === '/dev-login';

  const refreshAuthState = useCallback(async () => {
    setAuthLoading(true);
    try {
      const next = await api.getAuthStatus();
      setAuthState(next);
    } finally {
      setAuthLoading(false);
    }
  }, []);

  useEffect(() => {
    document.body.classList.toggle('theme-dark', theme === 'dark');
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    const idleCallback = window.requestIdleCallback;
    if (typeof idleCallback === 'function') {
      const id = idleCallback(() => preloadRouteModules());
      return () => window.cancelIdleCallback(id);
    }
    const timeoutId = window.setTimeout(() => preloadRouteModules(), 150);
    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    void refreshAuthState();
  }, [refreshAuthState]);

  useEffect(() => {
    setAccountMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [accountMenuOpen]);

  const authEnabled = Boolean(authState?.auth_enabled);
  const shouldRenderLogin = !isAuthCallbackRoute && !isDevLoginRoute && authEnabled && !authLoading && !authState?.authenticated;
  const accountDisplayName = authState?.user?.given_name
    || authState?.user?.full_name
    || authState?.user?.email
    || 'Signed in';
  const accountInitial = useMemo(
    () => (accountDisplayName.trim().charAt(0) || 'S').toUpperCase(),
    [accountDisplayName],
  );

  const handleLogout = useCallback(async () => {
    setLoggingOut(true);
    try {
      await api.logout();
    } catch {
      // swallow logout cleanup errors; local token clearing still matters
    } finally {
      api.clearStoredAuthToken();
      setAuthState((previous) => previous
        ? { ...previous, authenticated: false, auth_method: 'anonymous', user: null }
        : previous);
      setLoggingOut(false);
    }
  }, []);

  if (authLoading && !isAuthCallbackRoute) {
    return <LoadingShell />;
  }

  if (shouldRenderLogin) {
    return (
      <Suspense fallback={<LoadingShell />}>
        <LoginPage
          providers={authState?.providers ?? []}
          magicLinkEnabled={authState?.magic_link_enabled ?? false}
          devLoginEnabled={authState?.dev_login_enabled ?? false}
          onAuthenticated={refreshAuthState}
        />
      </Suspense>
    );
  }

  if (isDevLoginRoute && authEnabled && !authLoading && !authState?.authenticated) {
    return (
      <Suspense fallback={<LoadingShell />}>
        <LoginPage
          providers={authState?.providers ?? []}
          magicLinkEnabled={authState?.magic_link_enabled ?? false}
          devLoginEnabled={authState?.dev_login_enabled ?? false}
          forceDevMode
          onAuthenticated={refreshAuthState}
        />
      </Suspense>
    );
  }

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
        {!isFocusedWorkflowRoute && authEnabled && authState?.user ? (
          <div className="top-nav-right">
            <div className="account-menu" ref={accountMenuRef}>
              <button
                type="button"
                className="account-menu-trigger"
                aria-haspopup="menu"
                aria-expanded={accountMenuOpen}
                aria-label="Open account menu"
                onClick={() => setAccountMenuOpen((current) => !current)}
              >
                <span className="account-avatar" aria-hidden="true">{accountInitial}</span>
              </button>
              {accountMenuOpen ? (
                <div className="account-menu-panel" role="menu">
                  <div className="account-menu-summary">
                    <div className="account-menu-name">{accountDisplayName}</div>
                    {authState.user.email ? <div className="account-menu-email">{authState.user.email}</div> : null}
                  </div>
                  <button type="button" className="account-menu-action" role="menuitem" onClick={() => void handleLogout()} disabled={loggingOut}>
                    {loggingOut ? 'Signing out…' : 'Sign out'}
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </header>

      <main id="main-content" tabIndex={-1}>
        <Suspense fallback={<LoadingShell />}>
          <Routes>
            <Route path="/auth/callback" element={<AuthCallbackPage onAuthenticated={refreshAuthState} />} />
            <Route path="/dev-login" element={authEnabled && authState?.authenticated ? <Navigate to="/" replace /> : <LoadingShell />} />
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
