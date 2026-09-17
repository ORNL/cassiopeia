// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// The active domain pack, as described by GET /api/domain.
//
// Everything community-specific the dashboard shows — profile facets and
// their vocabularies, sources, wording, evaluator stages — comes from this
// manifest. Pack-specific React components come from `@domain-ui`, which the
// Vite build resolves to domains/<pack>/ui/index.jsx (see vite.config.js).

import { createContext, useContext, useEffect, useState } from "react";
import PropTypes from "prop-types";
import packUi from "@domain-ui";

const DomainContext = createContext(null);

export function DomainProvider({ children }) {
  const [domain, setDomain] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/domain")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((manifest) => {
        if (manifest.ui?.document_title) document.title = manifest.ui.document_title;
        setDomain({ ...manifest, plugins: packUi });
      })
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <DomainStatus text={`Could not load the domain configuration (${error}).`} />;
  if (!domain) return <DomainStatus text="Loading…" />;
  return <DomainContext.Provider value={domain}>{children}</DomainContext.Provider>;
}

DomainProvider.propTypes = { children: PropTypes.node };

export function useDomain() {
  return useContext(DomainContext);
}

/** Label for a facet value, falling back to the raw value in readable form. */
export function valueLabel(facet, value) {
  const item = facet.vocabulary.find((v) => v.value === value);
  if (item) return item.label;
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Facets the researcher picks values for (hidden and select-all facets excluded). */
export function selectableFacets(domain) {
  return domain.facets.filter((f) => f.widget !== "none" && !f.select_all);
}

export function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

function DomainStatus({ text }) {
  return (
    <div style={{
      minHeight: "100vh", background: "#0c0f1a", color: "#64748b",
      display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "'IBM Plex Sans', system-ui, sans-serif", fontSize: 14,
    }}>
      {text}
    </div>
  );
}

DomainStatus.propTypes = { text: PropTypes.string.isRequired };
