import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth";
import "./styles.css";

async function bootstrap() {
  // Dev-only: intercept /api/* with fixture handlers so Cursor preview
  // always uses production API panels (never DemoRolePanel as review surface).
  if (import.meta.env.DEV) {
    const { isApiSimulationEnabled } = await import("./sim/enabled");
    if (isApiSimulationEnabled()) {
      const { installApiSimulation } = await import("./sim/install");
      installApiSimulation();
    }
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </React.StrictMode>,
  );
}

void bootstrap();
