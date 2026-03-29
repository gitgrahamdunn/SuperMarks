import { useState, type FormEvent } from 'react';

import { api } from '../api/client';
import type { AuthProviderRead } from '../types/api';

type LoginPageProps = {
  providers: AuthProviderRead[];
  magicLinkEnabled?: boolean;
  devLoginEnabled?: boolean;
  forceDevMode?: boolean;
  onAuthenticated?: () => Promise<void> | void;
};

export function LoginPage({
  providers,
  magicLinkEnabled = false,
  devLoginEnabled = false,
  forceDevMode = false,
  onAuthenticated,
}: LoginPageProps) {
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
    <main className="login-landing-page">
      <section className="login-landing-shell">
        <div className="login-landing-hero">
          <p className="page-eyebrow">SuperMarks</p>
          <h1 className="page-title">Turn graded papers into organized results in minutes.</h1>
          <p className="page-subtitle">
            Upload graded tests, confirm names and totals, and export a clean table your class can use right away.
          </p>
          <div className="login-landing-feature-grid">
            <article className="metric-card">
              <p className="metric-label">Upload</p>
              <p className="login-landing-feature-title">Bring in photos, PDFs, or scans</p>
              <p className="metric-meta">SuperMarks builds a workspace around the papers you already have.</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">Review</p>
              <p className="login-landing-feature-title">Check names and totals quickly</p>
              <p className="metric-meta">Work through flagged papers and confirm what should count.</p>
            </article>
            <article className="metric-card">
              <p className="metric-label">Export</p>
              <p className="login-landing-feature-title">Share a clean test table</p>
              <p className="metric-meta">Download or share results without rebuilding the table by hand.</p>
            </article>
          </div>
        </div>

        <section className="login-landing-card">
          <h2
            className="section-title"
            onClick={maybeRevealDevMode}
            style={{ cursor: devLoginEnabled ? 'pointer' : 'default' }}
          >
            Sign in
          </h2>
          <p className="subtle-text">Access your saved tests, class lists, review queue, and exports.</p>

          {magicLinkEnabled ? (
            <form onSubmit={requestMagicLink} className="login-form-stack">
              <label className="login-form-label">
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
              {errorMessage ? <p className="login-error-text">{errorMessage}</p> : null}
            </form>
          ) : null}

          {(forceDevMode || devModeRevealed) && devLoginEnabled ? (
            <form onSubmit={requestDevLogin} className="login-form-stack">
              <label className="login-form-label">
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

          <div className="login-provider-stack">
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
      </section>
    </main>
  );
}
