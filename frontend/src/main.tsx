import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ToastProvider } from './components/ToastProvider';
import { checkBackendApiContract } from './api/client';
import './styles.css';

function ContractMismatchPage({ message, missingPaths }: { message: string; missingPaths: string[] }) {
  return (
    <div className="contract-error-page">
      <div className="contract-error-card">
        <h1>Backend API contract mismatch</h1>
        <p>{message}</p>
        <ul>
          {missingPaths.map((path) => (
            <li key={path}>{path}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

async function bootstrap() {
  const root = ReactDOM.createRoot(document.getElementById('root')!);
  const contractCheck = await checkBackendApiContract();

  if (!contractCheck.ok) {
    root.render(
      <React.StrictMode>
        <ContractMismatchPage message={contractCheck.message} missingPaths={contractCheck.missingPaths} />
      </React.StrictMode>,
    );
    return;
  }

  root.render(
    <React.StrictMode>
      <BrowserRouter>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </React.StrictMode>,
  );
}

void bootstrap();
