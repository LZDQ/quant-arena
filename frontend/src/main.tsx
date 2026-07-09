import React from "react";
import ReactDOM from "react-dom/client";

import "@fontsource-variable/fraunces/full.css";
import "@fontsource-variable/fraunces/full-italic.css";
import "@fontsource-variable/instrument-sans/index.css";
import "@fontsource-variable/instrument-sans/wght-italic.css";
import "@fontsource-variable/jetbrains-mono/index.css";
import "@fontsource/noto-serif-sc/500.css";
import "@fontsource/noto-serif-sc/700.css";

import { App } from "./App";
import { UiProvider } from "./components/ui";
import "./index.css";
import "./styles/chart.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <UiProvider>
      <App />
    </UiProvider>
  </React.StrictMode>,
);
