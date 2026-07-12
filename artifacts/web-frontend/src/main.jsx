import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";

if (window.location.hostname.endsWith(".replit.app")) {
  window.location.replace(
    "https://agentbio.groundlogic.ai" +
      window.location.pathname +
      window.location.search +
      window.location.hash
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
