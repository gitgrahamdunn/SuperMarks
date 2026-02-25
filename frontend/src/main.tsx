import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ToastProvider } from './components/ToastProvider';
import { checkBackendApiContract, getApiConfigError } from './api/client';
import { ErrorBoundary } from './components/ErrorBoundary';
import './styles.css';

const apiConfigError = getApiConfigError();

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
      <ErrorBoundary>
        <BrowserRouter>
          <ToastProvider>
            <App />
          </ToastProvider>
        </BrowserRouter>
      </ErrorBoundary>
    </React.StrictMode>,
  );
}
