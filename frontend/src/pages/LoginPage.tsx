import { useState, type FormEvent } from 'react';

import { api } from '../api/client';
import type { AuthProviderRead } from '../types/api';

type LoginPageProps = {
  providers: AuthProviderRead[];
  magicLinkEnabled?: boolean;
};

export function LoginPage({ providers, magicLinkEnabled = false }: LoginPageProps) {
  const [email, setEmail] = useState('');
  const [emailPending, setEmailPending] = useState(false);
  const [emailSent, setEmailSent] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const startLogin = (providerSlug: string) => {
    const callbackUrl = `${window.location.origin}/auth/callback`;
    window.location.assign(api.buildAuthLoginUrl(providerSlug, callbackUrl));
  };

  const requestMagicLink = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage('');
    setEmailPending(true);
    try {
      const callbackUrl = `${window.location.origin}/auth/callback`;
      await api.requestMagicLink(email, callbackUrl);
      setEmailSent(true);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not send sign-in email');
    } finally {
      setEmailPending(false);
    }
  };

  return (
    <main className="contract-error-page">
      <section className="contract-error-card" style={{ maxWidth: 460 }}>
        <h1>Sign in to SuperMarks</h1>
        <p>Use your school or personal account to access your exam workspaces.</p>
        {magicLinkEnabled ? (
          <form onSubmit={requestMagicLink} style={{ display: 'grid', gap: '.75rem', marginTop: '1rem' }}>
            <label style={{ display: 'grid', gap: '.35rem', textAlign: 'left' }}>
              <span>Email link</span>
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                required
              />
            </label>
            <button type="submit" className="btn btn-primary" disabled={emailPending}>
              {emailPending ? 'Sending link…' : 'Email me a sign-in link'}
            </button>
            {emailSent ? <p className="subtle-text">Check your inbox for the sign-in link.</p> : null}
            {errorMessage ? <p className="subtle-text" style={{ color: 'var(--danger-600, #b42318)' }}>{errorMessage}</p> : null}
          </form>
        ) : null}
        <div style={{ display: 'grid', gap: '.75rem', marginTop: '1rem' }}>
          {providers.map((provider) => (
            <button
              key={provider.slug}
              type="button"
              className="btn btn-primary"
              onClick={() => startLogin(provider.slug)}
            >
              Continue with {provider.name}
            </button>
          ))}
        </div>
        {providers.length === 0 && !magicLinkEnabled ? (
          <p className="subtle-text" style={{ marginTop: '1rem' }}>
            No login providers are configured on the backend yet.
          </p>
        ) : null}
      </section>
    </main>
  );
}
