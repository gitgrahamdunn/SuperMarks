import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ToastProvider } from './components/ToastProvider';
import { checkBackendApiContract } from './api/client';
import { ErrorBoundary } from './components/ErrorBoundary';
import './styles.css';

void checkBackendApiContract()
  .then((result) => {
    if (!result.ok) {
      console.error('[SuperMarks] Backend contract check failed', {
        message: result.message,
        missingPaths: result.missingPaths,
        diagnostics: result.diagnostics,
      });
    }
  })
  .catch((error) => {
    console.error('[SuperMarks] Backend contract check crashed', error);
  });

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
