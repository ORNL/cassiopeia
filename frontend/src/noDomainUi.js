// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// Used as `@domain-ui` when the active pack ships no React components.
//
// A pack's ui/index.jsx default-exports the same shape:
//   proposalBadges: { [evaluatorKey]: Component }  — rendered in the card header
//   proposalPanels: { [evaluatorKey]: Component }  — rendered below the critique
// Each component receives the evaluator result as its `result` prop.
export default {};
