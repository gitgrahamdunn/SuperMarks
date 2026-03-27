import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

type AuthCallbackPageProps = {
  onAuthenticated: () => Promise<void>;
};

function extractTokenFromLocation(): string {
  const hash = window.location.hash.replace(/^#/, '');
  const hashParams = new URLSearchParams(hash);
  return hashParams.get('sm_token')?.trim() || '';
}

export function AuthCallbackPage({ onAuthenticated }: AuthCallbackPageProps) {
  const navigate = useNavigate();
  const [error, setError] = useState<string>('');

  useEffect(() => {
    let cancelled = false;

    const finalize = async () => {
      const token = extractTokenFromLocation();
      if (!token) {
        if (!cancelled) setError('OAuth callback did not include an app session token.');
        return;
      }
      api.persistAuthToken(token);
      try {
        await onAuthenticated();
        if (!cancelled) {
          window.history.replaceState({}, document.title, '/');
          navigate('/', { replace: true });
        }
      } catch (authError) {
        api.clearStoredAuthToken();
        if (!cancelled) {
          setError(authError instanceof Error ? authError.message : 'Could not finalize sign-in.');
        }
      }
    };

    void finalize();
    return () => {
      cancelled = true;
    };
  }, [navigate, onAuthenticated]);

  return (
    <main className="contract-error-page">
      <section className="contract-error-card" style={{ maxWidth: 460 }}>
        <h1>{error ? 'Sign-in failed' : 'Finishing sign-in…'}</h1>
        <p>{error || 'SuperMarks is finalizing your account session.'}</p>
      </section>
    </main>
  );
}
