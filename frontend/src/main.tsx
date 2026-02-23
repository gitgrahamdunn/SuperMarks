import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ToastProvider } from './components/ToastProvider';
import { checkBackendApiContract } from './api/client';
import './styles.css';

void checkBackendApiContract().then((result) => {
  if (!result.ok) {
    console.warn('[SuperMarks] Non-blocking backend contract warning', result);
  }
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <App />
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
