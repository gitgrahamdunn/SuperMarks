import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ToastProvider } from './components/ToastProvider';
import { checkBackendApiContract, getApiConfigError } from './api/client';
import { logEvent } from './logs/clientLogStore';
import './styles.css';

const apiConfigError = getApiConfigError();
const BUILD_MARKER = String(__APP_BUILD_TS__);
const BUILD_RELOAD_STORAGE_KEY = 'supermarks-build-reload';

function renderHostedApiConfigMessage(errorMessage: string): React.JSX.Element {
  const host = window.location.hostname.trim().toLowerCase();
  const isHosted = host !== 'localhost' && host !== '127.0.0.1';

  return (
    <div className="contract-error-page">
      <div className="contract-error-card">
        <h1>Invalid VITE_API_BASE_URL configuration.</h1>
        <p>{errorMessage}</p>
        {isHosted ? (
          <>
            <p>Hosted SuperMarks frontends are not self-contained. They need a public backend URL ending in <code>/api</code>.</p>
            <p>For the current local-backend deployment model, point <code>VITE_API_BASE_URL</code> at the machine&apos;s public Funnel URL instead of expecting a hosted preview backend.</p>
          </>
        ) : null}
      </div>
    </div>
  );
}

async function fetchLatestBuildMarker(): Promise<string | null> {
  try {
    const response = await fetch('/', {
      cache: 'no-store',
      headers: {
        Accept: 'text/html',
      },
    });
    if (!response.ok) {
      return null;
    }
    const html = await response.text();
    const match = html.match(/<meta\s+name=["']supermarks-build["']\s+content=["']([^"']+)["']/i);
    return match?.[1] ?? null;
  } catch {
    return null;
  }
}

async function reloadIfBuildIsStale(): Promise<void> {
  const latestBuildMarker = await fetchLatestBuildMarker();
  if (!latestBuildMarker || latestBuildMarker === BUILD_MARKER) {
    sessionStorage.removeItem(BUILD_RELOAD_STORAGE_KEY);
    return;
  }

  const previousReloadMarker = sessionStorage.getItem(BUILD_RELOAD_STORAGE_KEY);
  if (previousReloadMarker === latestBuildMarker) {
    return;
  }

  sessionStorage.setItem(BUILD_RELOAD_STORAGE_KEY, latestBuildMarker);
  window.location.reload();
}

window.addEventListener('error', (event) => {
  logEvent({
    type: 'window.error',
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
    stack: event.error?.stack,
  });
});

window.addEventListener('unhandledrejection', (event) => {
  const err = event.reason;
  logEvent({
    type: 'unhandledrejection',
    message: String(err?.message || err),
    stack: err?.stack,
  });
});

console.log('[SuperMarks] API_BASE=', import.meta.env.VITE_API_BASE_URL || '<missing>');
console.log('[SuperMarks] HAS_API_KEY=', Boolean(import.meta.env.VITE_BACKEND_API_KEY));
void reloadIfBuildIsStale();

if (!apiConfigError) {
  void checkBackendApiContract().then((result) => {
    if (!result.ok) {
      console.warn('[SuperMarks] Non-blocking backend contract warning', result);
    }
  });
}

if (import.meta.env.PROD && apiConfigError) {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      {renderHostedApiConfigMessage(apiConfigError)}
    </React.StrictMode>,
  );
} else {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <BrowserRouter
        future={{
          v7_startTransition: true,
          v7_relativeSplatPath: true,
        }}
      >
        <ErrorBoundary>
          <ToastProvider>
            <App />
          </ToastProvider>
        </ErrorBoundary>
      </BrowserRouter>
    </React.StrictMode>,
  );
}
