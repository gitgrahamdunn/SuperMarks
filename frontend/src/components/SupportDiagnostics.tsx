import { useCallback, useState } from 'react';
import {
  checkBackendApiContract,
  getBackendVersion,
  getClientDiagnostics,
  pingApiHealth,
  resetApiContractCheckCache,
} from '../api/client';

type ProbeState =
  | { status: 'idle' }
  | { status: 'loading' }
  | {
    status: 'ready';
    checkedAt: string;
    health: { status: number | null; body: string };
    version: { status: number | null; version?: string; bodySnippet: string };
    contract: { ok: true } | { ok: false; message: string; openApiUrl: string; openApiStatus: number | null };
  };

const clientDiagnostics = getClientDiagnostics();

export function SupportDiagnostics() {
  const [probeState, setProbeState] = useState<ProbeState>({ status: 'idle' });

  const runProbe = useCallback(async () => {
    setProbeState({ status: 'loading' });
    resetApiContractCheckCache();

    const [healthResult, versionResult, contractResult] = await Promise.all([
      pingApiHealth()
        .then((result) => ({ status: result.status, body: result.body }))
        .catch((error: unknown) => ({
          status: null,
          body: error instanceof Error ? error.message : String(error),
        })),
      getBackendVersion()
        .then((result) => ({ status: result.status, version: result.version, bodySnippet: result.bodySnippet }))
        .catch((error: unknown) => ({
          status: null,
          version: undefined,
          bodySnippet: error instanceof Error ? error.message : String(error),
        })),
      checkBackendApiContract()
        .then((result) => (
          result.ok
            ? { ok: true as const }
            : {
              ok: false as const,
              message: result.message,
              openApiUrl: result.diagnostics.openApiUrl,
              openApiStatus: result.diagnostics.statusCode,
            }
        ))
        .catch((error: unknown) => ({
          ok: false as const,
          message: error instanceof Error ? error.message : String(error),
          openApiUrl: '',
          openApiStatus: null,
        })),
    ]);

    setProbeState({
      status: 'ready',
      checkedAt: new Date().toISOString(),
      health: healthResult,
      version: versionResult,
      contract: contractResult,
    });
  }, []);

  return (
    <details
      style={{
        border: '1px solid var(--border-soft)',
        borderRadius: '0.9rem',
        padding: '0.6rem 0.8rem',
        background: 'linear-gradient(180deg, var(--surface-raised), var(--surface-muted))',
        minWidth: 'min(100%, 28rem)',
        maxWidth: '42rem',
        flex: '1 1 28rem',
      }}
      onToggle={(event) => {
        const element = event.currentTarget;
        if (element.open && probeState.status === 'idle') {
          void runProbe();
        }
      }}
    >
      <summary style={{ cursor: 'pointer', fontWeight: 700 }}>
        Support diagnostics
      </summary>
      <div style={{ display: 'grid', gap: '0.6rem', marginTop: '0.8rem' }}>
        <p className="subtle-text" style={{ margin: 0 }}>
          Backend reachability, version, and contract checks for support/debugging.
        </p>
        <div style={{ display: 'grid', gap: '0.3rem' }}>
          <div><strong>Frontend build:</strong> <code>{clientDiagnostics.appVersion}</code></div>
          <div><strong>API base:</strong> <code>{clientDiagnostics.apiBaseUrl}</code></div>
          <div><strong>API key attached:</strong> {clientDiagnostics.authHeaderAttached ? 'yes' : 'no'}</div>
        </div>
        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
          <button type="button" onClick={() => void runProbe()} disabled={probeState.status === 'loading'}>
            {probeState.status === 'loading' ? 'Checking…' : 'Run checks'}
          </button>
          {probeState.status === 'ready' ? (
            <span className="subtle-text">Last checked: {new Date(probeState.checkedAt).toLocaleString()}</span>
          ) : null}
        </div>
        {probeState.status === 'ready' ? (
          <div style={{ display: 'grid', gap: '0.6rem' }}>
            <div>
              <strong>Health:</strong>{' '}
              {probeState.health.status === null ? 'request failed' : `HTTP ${probeState.health.status}`}
              <div className="subtle-text" style={{ marginTop: '0.2rem', wordBreak: 'break-word' }}>
                <code>{probeState.health.body || '<empty>'}</code>
              </div>
            </div>
            <div>
              <strong>Backend version:</strong>{' '}
              {probeState.version.status === null
                ? 'request failed'
                : probeState.version.version
                  ? `${probeState.version.version} (HTTP ${probeState.version.status})`
                  : `HTTP ${probeState.version.status}`}
              <div className="subtle-text" style={{ marginTop: '0.2rem', wordBreak: 'break-word' }}>
                <code>{probeState.version.bodySnippet || '<empty>'}</code>
              </div>
            </div>
            <div>
              <strong>Contract:</strong>{' '}
              {probeState.contract.ok ? 'ok' : probeState.contract.message}
              {!probeState.contract.ok ? (
                <div className="subtle-text" style={{ marginTop: '0.2rem', wordBreak: 'break-word' }}>
                  <code>
                    {probeState.contract.openApiUrl || '<missing-openapi-url>'}
                    {probeState.contract.openApiStatus === null ? '' : ` [HTTP ${probeState.contract.openApiStatus}]`}
                  </code>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </details>
  );
}
