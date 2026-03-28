import { createContext, useCallback, useContext, useMemo, useState } from 'react';

interface Toast {
  id: number;
  message: string;
  type: 'success' | 'error' | 'warning';
  actionLabel?: string;
  onAction?: (() => void | Promise<void>) | undefined;
}

interface ToastContextValue {
  showSuccess: (message: string, action?: Pick<Toast, 'actionLabel' | 'onAction'>) => void;
  showError: (message: string, action?: Pick<Toast, 'actionLabel' | 'onAction'>) => void;
  showWarning: (message: string, action?: Pick<Toast, 'actionLabel' | 'onAction'>) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const addToast = useCallback((message: string, type: Toast['type'], action?: Pick<Toast, 'actionLabel' | 'onAction'>) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, type, ...action }]);
    setTimeout(() => {
      dismissToast(id);
    }, 4200);
  }, [dismissToast]);

  const value = useMemo(
    () => ({
      showSuccess: (message: string, action?: Pick<Toast, 'actionLabel' | 'onAction'>) => addToast(message, 'success', action),
      showError: (message: string, action?: Pick<Toast, 'actionLabel' | 'onAction'>) => addToast(message, 'error', action),
      showWarning: (message: string, action?: Pick<Toast, 'actionLabel' | 'onAction'>) => addToast(message, 'warning', action),
    }),
    [addToast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.type}`}>
            <span>{toast.message}</span>
            {toast.actionLabel && toast.onAction && (
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  dismissToast(toast.id);
                  void toast.onAction?.();
                }}
              >
                {toast.actionLabel}
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider');
  }
  return context;
}
