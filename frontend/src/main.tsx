import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { ToastProvider } from './components/ToastProvider';
import { checkBackendApiContract, resetApiContractCheckCache } from './api/client';
import './styles.css';

type ContractMismatchProps = {
  message: string;
  missingPaths: string[];
  diagnostics?: {
    openApiUrl: string;
    statusCode: number | null;
    responseSnippet: string;
    normalizedPathsFound: string[];
    normalizedRequiredPaths: string[];
  };
  onRetry: () => void;
};

function ContractMismatchPage({ message, missingPaths, diagnostics, onRetry }: ContractMismatchProps) {
  return (
    <div className="contract-error-page">
      <div className="contract-error-card">
        <h1>Backend API contract mismatch</h1>
        <p>{message}</p>
        <button type="button" onClick={onRetry}>Retry</button>
        {diagnostics ? (
          <div className="contract-error-diagnostics">
            <h2>Diagnostics</h2>
            <ul>
              <li><strong>OpenAPI URL:</strong> {diagnostics.openApiUrl || '(not set)'}</li>
              <li><strong>HTTP status:</strong> {diagnostics.statusCode ?? 'N/A'}</li>
              <li><strong>Response snippet:</strong> {diagnostics.responseSnippet || '(empty response)'}</li>
              <li>
                <strong>Normalized paths found (first 20):</strong>
                <ul>
                  {diagnostics.normalizedPathsFound.map((path) => (
                    <li key={`found-${path}`}>{path}</li>
                  ))}
                </ul>
              </li>
              <li>
                <strong>Normalized required paths:</strong>
                <ul>
                  {diagnostics.normalizedRequiredPaths.map((path) => (
                    <li key={`required-${path}`}>{path}</li>
                  ))}
                </ul>
              </li>
            </ul>
          </div>
        ) : null}
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

  const renderApp = () => {
    root.render(
      <React.StrictMode>
        <BrowserRouter>
          <ToastProvider>
            <App />
          </ToastProvider>
        </BrowserRouter>
      </React.StrictMode>,
    );
  };

  const renderContractError = async () => {
    const contractCheck = await checkBackendApiContract();

    if (contractCheck.ok) {
      renderApp();
      return;
    }

    root.render(
      <React.StrictMode>
        <ContractMismatchPage
          message={contractCheck.message}
          missingPaths={contractCheck.missingPaths}
          diagnostics={contractCheck.diagnostics}
          onRetry={() => {
            resetApiContractCheckCache();
            void renderContractError();
          }}
        />
      </React.StrictMode>,
    );
  };

  await renderContractError();
}

void bootstrap();
