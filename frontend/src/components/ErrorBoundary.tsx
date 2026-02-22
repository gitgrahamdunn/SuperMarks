import React from 'react';

type ErrorBoundaryProps = {
  children: React.ReactNode;
};

type ErrorBoundaryState = {
  hasError: boolean;
  errorMessage: string;
};

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  public constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, errorMessage: '' };
  }

  public static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      errorMessage: error.message || 'Unexpected application error during startup.',
    };
  }

  public componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    console.error('[SuperMarks] Unhandled app error', { error, errorInfo });
  }

  public render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '1rem', fontFamily: 'sans-serif' }}>
          <h1>Something went wrong</h1>
          <p>The app hit an unexpected startup error.</p>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{this.state.errorMessage}</pre>
        </div>
      );
    }

    return this.props.children;
  }
}
