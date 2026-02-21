import { useMemo, useState } from 'react';

interface DebugPanelProps {
  summary: string;
  details: unknown;
}

function formatDebugDetails(details: unknown): string {
  if (details == null) {
    return '<empty>';
  }

  if (typeof details === 'string') {
    try {
      const parsed = JSON.parse(details);
      return JSON.stringify(parsed, null, 2);
    } catch {
      return details;
    }
  }

  return JSON.stringify(details, null, 2);
}

export function DebugPanel({ summary, details }: DebugPanelProps) {
  const [isCopying, setIsCopying] = useState(false);
  const [copied, setCopied] = useState(false);

  const formattedDetails = useMemo(() => formatDebugDetails(details), [details]);

  const handleCopy = async () => {
    try {
      setIsCopying(true);
      await navigator.clipboard.writeText(formattedDetails);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    } finally {
      setIsCopying(false);
    }
  };

  return (
    <div className="debug-panel">
      <p className="subtle-text debug-panel-summary">{summary}</p>
      <details>
        <summary>Error Details</summary>
        <div className="debug-panel-actions">
          <button type="button" onClick={() => void handleCopy()} disabled={isCopying}>
            {copied ? 'Copied' : 'Copy to Clipboard'}
          </button>
        </div>
        <pre className="debug-panel-content">{formattedDetails}</pre>
      </details>
    </div>
  );
}
