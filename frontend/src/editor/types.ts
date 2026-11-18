export interface OnlyOfficeConfig {
  documentType?: "word" | "cell" | "slide" | "pdf";
  document: {
    fileType: "pptx";
    key: string;
    title: string;
    url: string;
  };
  editorConfig: {
    mode: "edit" | "view";
    lang?: string;
    user?: { id: string; name: string };
    callbackUrl?: string;
    plugins?: unknown;
  };
  events?: Record<string, unknown>;
}

export type OnlyOfficeEditorInstance = {
  destroyEditor: () => void;
  refreshFile: (config?: OnlyOfficeConfig) => void;
  // 这里不要强依赖 executeMethod（很多版本/版本类型并不提供）
  executeMethod?: (method: string, params?: unknown[]) => unknown;
};

declare global {
  interface Window {
    DocsAPI: {
      DocEditor: new (elementId: string, config: OnlyOfficeConfig) => OnlyOfficeEditorInstance;
    };
  }
}

