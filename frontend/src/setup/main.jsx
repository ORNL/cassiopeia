// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// Entry point of the setup wizard (setup.html), served by `./launch.sh setup`.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import SetupApp from "./SetupApp.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <SetupApp />
  </StrictMode>,
);
