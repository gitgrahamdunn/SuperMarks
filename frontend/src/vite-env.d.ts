/// <reference types="vite/client" />

declare const __APP_BUILD_TS__: number;

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_BACKEND_API_KEY?: string;
  readonly VITE_BUILD_ID?: string;
}
