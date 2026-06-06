import { ViewSpecView, type ViewSpecActionIntent } from "./generated/ViewSpecView";

declare global {
  interface Window {
    __viewspecActions?: ViewSpecActionIntent[];
  }
}

export function App() {
  const recordAction = (intent: ViewSpecActionIntent) => {
    window.__viewspecActions = [...(window.__viewspecActions ?? []), intent];
  };

  return <ViewSpecView onAction={recordAction} />;
}
