import * as React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("HOST_PROOF_BROWSER_RUNTIME_ERROR: missing #root mount point");
}

createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
