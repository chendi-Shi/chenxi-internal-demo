declare module '*.css';

interface ImportMetaEnv {
  readonly VITE_MODELSCOPE_DEMO?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
