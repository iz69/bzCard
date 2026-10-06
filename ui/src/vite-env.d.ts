/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BUILD_VERSION?: string;
}

interface Window {
  __BZCARD_CONFIG__?: {
    uiBasePath?: string;
    apiBasePath?: string;
  };
}
