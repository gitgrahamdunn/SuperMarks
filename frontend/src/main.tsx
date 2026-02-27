import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ToastProvider } from './components/ToastProvider';
import { checkBackendApiContract, getApiConfigError } from './api/client';
import './styles.css';

const apiConfigError = getApiConfigError();

console.log('[SuperMarks] API_BASE=', import.meta.env.VITE_API_BASE_URL || '<missing>');
console.log('[SuperMarks] HAS_API_KEY=', Boolean(import.meta.env.VITE_BACKEND_API_KEY));

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
      <div className="contract-error-page">
        <div className="contract-error-card">
          <h1>Missing VITE_API_BASE_URL (must be https://.../api).</h1>
          <p>{apiConfigError}</p>
        </div>
      </div>
    </React.StrictMode>,
  );
} else {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <BrowserRouter>
        <ErrorBoundary>
          <ToastProvider>
            <App />
          </ToastProvider>
        </ErrorBoundary>
      </BrowserRouter>
    </React.StrictMode>,
  );
}
