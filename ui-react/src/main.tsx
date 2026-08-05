import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { LogtoProvider, type LogtoConfig } from "@logto/react";
import "./index.css";
import App from "./App.tsx";
import { config } from "./config";

const logtoConfig: LogtoConfig = {
  endpoint: config.logtoEndpoint,
  appId: config.logtoAppId,
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LogtoProvider config={logtoConfig}>
      <App />
    </LogtoProvider>
  </StrictMode>,
);
