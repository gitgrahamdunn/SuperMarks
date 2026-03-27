import { useState, type FormEvent } from 'react';

import { api } from '../api/client';
import type { AuthProviderRead } from '../types/api';

type LoginPageProps = {
  providers: AuthProviderRead[];
  magicLinkEnabled?: boolean;
  devLoginEnabled?: boolean;
  onAuthenticated?: () => Promise<void> | void;
};

export function LoginPage({ providers, magicLinkEnabled = false, devLoginEnabled = false, onAuthenticated }: LoginPageProps) {
  const [email, setEmail] = useState('');
  const [emailPending, setEmailPending] = useState(false);
  const [emailSent, setEmailSent] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [devRevealTapCount, setDevRevealTapCount] = useState(0);
  const [devModeRevealed, setDevModeRevealed] = useState(false);
  const [devKey, setDevKey] = useState('');
  const [devPending, setDevPending] = useState(false);

  const startLogin = (providerSlug: string) => {
    const callbackUrl = `${window.location.origin}/auth/callback`;
    window.location.assign(api.buildAuthLoginUrl(providerSlug, callbackUrl));
  };

  const maybeRevealDevMode = () => {
    if (!devLoginEnabled) return;
    setDevRevealTapCount((current) => {
      const next = current + 1;
      if (next >= 5) {
        setDevModeRevealed(true);
        return 0;
      }
      return next;
    });
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

  const requestDevLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage('');
    setDevPending(true);
    try {
      const response = await api.loginWithDevKey(devKey);
      api.persistAuthToken(response.token);
      await onAuthenticated?.();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Developer sign-in failed');
    } finally {
      setDevPending(false);
    }
  };

  return (
    <main className="contract-error-page">
      <section className="contract-error-card" style={{ maxWidth: 460 }}>
        <h1 onClick={maybeRevealDevMode} style={{ cursor: devLoginEnabled ? 'pointer' : 'default' }}>Sign in to SuperMarks</h1>
        <p>Sign in to access your marking workspace, class results, and saved review queues.</p>
        {magicLinkEnabled ? (
          <form onSubmit={requestMagicLink} style={{ display: 'grid', gap: '.75rem', marginTop: '1rem' }}>
            <label style={{ display: 'grid', gap: '.35rem', textAlign: 'left' }}>
              <span>Email address</span>
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
              {emailPending ? 'Sending sign-in link…' : 'Send sign-in link'}
            </button>
            {emailSent ? <p className="subtle-text">Check your inbox for a secure sign-in link.</p> : null}
            {errorMessage ? <p className="subtle-text" style={{ color: 'var(--danger-600, #b42318)' }}>{errorMessage}</p> : null}
          </form>
        ) : null}
        {devModeRevealed && devLoginEnabled ? (
          <form onSubmit={requestDevLogin} style={{ display: 'grid', gap: '.75rem', marginTop: '1rem' }}>
            <label style={{ display: 'grid', gap: '.35rem', textAlign: 'left' }}>
              <span>Developer access key</span>
              <input
                type="password"
                value={devKey}
                onChange={(event) => setDevKey(event.target.value)}
                autoComplete="off"
                required
              />
            </label>
            <button type="submit" className="btn btn-secondary" disabled={devPending}>
              {devPending ? 'Signing in…' : 'Continue'}
            </button>
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
            No sign-in methods are available right now.
          </p>
        ) : null}
      </section>
    </main>
  );
}
