import YAML from "yaml";
import cytoscape from "cytoscape";
import "./styles.css";

const views = [
  ["timeline", "Timeline"],
  ["graph", "Graph"],
  ["sessions", "Actor Sessions"],
  ["cases", "Cases"],
  ["manifest", "Manifest"],
  ["raw", "Raw"],
];

const state = {
  runs: new Map(),
  activeRunId: "",
  activeView: "timeline",
  selectedNodeId: "",
  query: "",
  messages: [],
};

let graph = null;
const warnedMissingEdgeSources = new Set();
const app = document.querySelector("#app");
if (!app) {
  throw new Error("ERP Trace Visualizer requires a #app root element.");
}

render();

function render() {
  const model = getActiveModel();
  ensureValidNodeSelection(model);
  if (state.activeView !== "graph") {
    destroyGraph();
  }

  app.innerHTML = `
    <div class="app-shell">
      ${renderHeader(model)}
      ${renderLoader()}
      ${renderMessages(model)}
      ${renderTabs(model)}
      ${renderWorkspace(model)}
    </div>
  `;

  bindEvents();

  if (state.activeView === "graph") {
    requestAnimationFrame(() => renderGraph(model));
  }
}

function renderHeader(model) {
  const runOptions = [...state.runs.keys()]
    .map((runId) => `<option value="${escapeAttr(runId)}" ${runId === state.activeRunId ? "selected" : ""}>${escapeHtml(runId)}</option>`)
    .join("");

  return `
    <header class="topbar">
      <div>
        <h1>ERP Trace Visualizer</h1>
        <p>Local trace inspection for execution traces and post-processing manifests.</p>
      </div>
      <div class="topbar-actions">
        <label class="field-label" for="runSelect">Run</label>
        <select id="runSelect" ${state.runs.size === 0 ? "disabled" : ""}>
          ${runOptions || `<option>No run loaded</option>`}
        </select>
      </div>
    </header>
    ${model ? renderSummary(model) : ""}
  `;
}

function renderSummary(model) {
  return `
    <section class="summary-grid" aria-label="Run summary">
      ${metric("Run", model.runId)}
      ${metric("Cases", model.cases.length)}
      ${metric("Planned Steps", model.nodes.length)}
      ${metric("Dependencies", model.edges.length)}
      ${metric("Waves", model.waves.length)}
      ${metric("Actor Sessions", model.sessions.length)}
    </section>
  `;
}

function metric(label, value) {
  return `
    <article class="metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `;
}

function renderLoader() {
  return `
    <section class="loader-panel">
      <div class="file-drop">
        <label class="field-label" for="traceFiles">Trace files</label>
        <input id="traceFiles" type="file" multiple accept=".yaml,.yml,.json,application/yaml,text/yaml,application/json" />
      </div>
      <div class="paste-grid">
        <label>
          <span class="field-label">Execution trace</span>
          <textarea id="executionPaste" spellcheck="false" placeholder="Paste execution trace YAML or JSON"></textarea>
        </label>
        <label>
          <span class="field-label">Post-processing manifest</span>
          <textarea id="manifestPaste" spellcheck="false" placeholder="Paste manifest YAML or JSON"></textarea>
        </label>
      </div>
      <div class="loader-actions">
        <button id="loadPaste" type="button">Load pasted traces</button>
        <button id="clearRuns" type="button" class="ghost">Clear</button>
      </div>
    </section>
  `;
}

function renderMessages(model) {
  const messages = [...state.messages];
  if (model) {
    if (!model.execution) {
      messages.push({ type: "warning", text: "No execution trace loaded for this run." });
    }
    if (!model.manifest) {
      messages.push({ type: "warning", text: "No post-processing manifest loaded for this run." });
    }
    messages.push(...model.warnings.map((text) => ({ type: "warning", text })));
  }

  if (messages.length === 0) {
    return "";
  }

  return `
    <section class="message-stack" aria-live="polite">
      ${messages
        .map((message) => `<div class="message ${escapeAttr(message.type)}">${escapeHtml(message.text)}</div>`)
        .join("")}
    </section>
  `;
}

function renderTabs(model) {
  const disabled = model ? "" : "disabled";
  return `
    <section class="control-bar">
      <div class="tabs" role="tablist">
        ${views
          .map(
            ([id, label]) => `
              <button type="button" data-view="${id}" class="${state.activeView === id ? "active" : ""}" ${disabled}>
                ${escapeHtml(label)}
              </button>
            `,
          )
          .join("")}
      </div>
      <label class="search-box">
        <span>Search</span>
        <input id="searchInput" value="${escapeAttr(state.query)}" placeholder="case, node, tool, input value" ${disabled} />
      </label>
    </section>
  `;
}

function renderWorkspace(model) {
  if (!model) {
    return `
      <main class="empty-state">
        <h2>No trace loaded</h2>
        <p>Upload files or paste YAML/JSON to begin.</p>
      </main>
    `;
  }

  return `
    <main class="workspace">
      <section class="view-pane">
        ${renderActiveView(model)}
      </section>
      <aside class="detail-pane">
        ${renderDetail(model)}
      </aside>
    </main>
  `;
}

function renderActiveView(model) {
  if (state.activeView === "graph") {
    return renderGraphView(model);
  }
  if (state.activeView === "sessions") {
    return renderSessions(model);
  }
  if (state.activeView === "cases") {
    return renderCases(model);
  }
  if (state.activeView === "manifest") {
    return renderManifest(model);
  }
  if (state.activeView === "raw") {
    return renderRaw(model);
  }
  return renderTimeline(model);
}

function renderTimeline(model) {
  if (!model.execution) {
    return renderBlank("No execution trace", "Load an execution trace to see case timelines.");
  }

  const rows = model.caseRows.filter((row) => row.caseMatches || row.nodes.some((node) => matchesNode(node, model)));
  if (rows.length === 0) {
    return renderBlank("No matching planned steps", "Search did not match loaded trace data.");
  }

  return `
    <div class="section-title">
      <h2>Case Timeline</h2>
      <span>${rows.length} case rows</span>
    </div>
    <div class="timeline">
      ${rows
        .map((row) => {
          const visibleNodes = row.nodes.filter((node) => row.caseMatches || matchesNode(node, model));
          return `
            <section class="timeline-row">
              <div class="case-rail">
                <strong>${escapeHtml(row.caseId)}</strong>
                <span>${escapeHtml(row.processType || "case")}</span>
                <small>${escapeHtml(row.caseScenarioType || "")}</small>
              </div>
              <div class="step-track">
                ${visibleNodes.map((node) => renderStepCard(node, model)).join("")}
              </div>
            </section>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderStepCard(node, model) {
  const schedule = model.scheduleByNode.get(node.planned_step_id);
  const selected = node.planned_step_id === state.selectedNodeId ? "selected" : "";
  const manifestTimestamp = model.timestampByNode.get(node.planned_step_id);
  return `
    <button type="button" class="step-card ${selected}" data-node-id="${escapeAttr(node.planned_step_id)}">
      <span class="step-card-top">
        <strong>${breakable(node.planned_step_id)}</strong>
        <em>${escapeHtml(schedule?.wave_id || "unscheduled")}</em>
      </span>
      <span class="step-type">${breakable(node.step_type)}</span>
      <span class="step-meta">${breakable(node.tool_name)}</span>
      <span class="step-meta">${breakable(node.synthetic_actor_id)} / ${breakable(node.actor_session_id)}</span>
      <span class="step-time">${escapeHtml(formatRange(manifestTimestamp?.planned_synthetic_start || node.planned_synthetic_time?.start, manifestTimestamp?.planned_synthetic_end || node.planned_synthetic_time?.end))}</span>
    </button>
  `;
}

function renderGraphView(model) {
  if (!model.execution) {
    return renderBlank("No execution graph", "Load an execution trace to see dependencies.");
  }

  return `
    <div class="section-title">
      <h2>Dependency Graph</h2>
      <span>${model.visibleNodes.length} visible planned steps</span>
    </div>
    <div id="graphCanvas" class="graph-canvas" aria-label="Dependency graph"></div>
  `;
}

function renderGraph(model) {
  destroyGraph();
  const container = document.querySelector("#graphCanvas");
  if (!container || !model?.execution) {
    return;
  }

  const visibleIds = new Set(model.visibleNodes.map((node) => node.planned_step_id));
  const elements = [
    ...model.visibleNodes.map((node) => ({
      data: {
        id: node.planned_step_id,
        label: `${node.planned_step_id}\n${node.step_type}`,
        caseId: node.case_id,
        selected: node.planned_step_id === state.selectedNodeId,
      },
    })),
    ...model.edges
      .filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to))
      .map((edge) => ({
        data: {
          id: `${edge.from}->${edge.to}`,
          source: edge.from,
          target: edge.to,
          label: edge.type,
        },
      })),
  ];

  graph = cytoscape({
    container,
    elements,
    style: [
      {
        selector: "node",
        style: {
          "background-color": "#2f7f75",
          "border-color": "#dff3ef",
          "border-width": 2,
          color: "#182033",
          "font-size": 11,
          "font-weight": 700,
          label: "data(label)",
          "text-halign": "center",
          "text-valign": "center",
          "text-wrap": "wrap",
          "text-max-width": 120,
          height: 58,
          width: 116,
          shape: "round-rectangle",
        },
      },
      {
        selector: "node[?selected]",
        style: {
          "background-color": "#d46f3d",
          "border-color": "#f7cfb8",
          "border-width": 4,
        },
      },
      {
        selector: "edge",
        style: {
          "curve-style": "bezier",
          "line-color": "#9ba8b8",
          "target-arrow-color": "#9ba8b8",
          "target-arrow-shape": "triangle",
          width: 2,
          label: "data(label)",
          "font-size": 9,
          color: "#56647a",
          "text-background-color": "#f9fbff",
          "text-background-opacity": 1,
          "text-background-padding": 2,
        },
      },
    ],
    layout: {
      name: elements.some((element) => element.data.source) ? "breadthfirst" : "grid",
      directed: true,
      padding: 28,
      spacingFactor: 1.35,
    },
  });

  graph.on("tap", "node", (event) => {
    state.selectedNodeId = event.target.id();
    render();
  });
}

function renderSessions(model) {
  if (!model.execution) {
    return renderBlank("No actor sessions", "Load an execution trace to inspect actor sessions.");
  }

  return `
    <div class="section-title">
      <h2>Actor Sessions</h2>
      <span>${model.sessions.length} records</span>
    </div>
    ${renderTable(
      ["actor_session_id", "synthetic_actor_id", "technical_sap_user_id", "username_env_var", "login_url_env_var"],
      model.sessions.map((session) => [
        session.actor_session_id,
        session.synthetic_actor_id,
        session.technical_sap_user_id,
        session.username_env_var,
        session.login_url_env_var,
      ]),
    )}
  `;
}

function renderCases(model) {
  if (!model.execution) {
    return renderBlank("No cases", "Load an execution trace to inspect input cases.");
  }

  const caseRows = model.cases.flatMap((item) => {
    const lineItems = Array.isArray(item.line_items) && item.line_items.length > 0 ? item.line_items : [{}];
    return lineItems.map((lineItem) => [
      item.case_id,
      item.process_type,
      item.case_scenario_type,
      lineItem.line_id,
      lineItem.material_id,
      lineItem.vendor_id,
      lineItem.plant,
      lineItem.purchasing_org,
      lineItem.storage_location,
      lineItem.quantity,
      lineItem.target_price,
    ]);
  });

  return `
    <div class="section-title">
      <h2>Cases and Input Data</h2>
      <span>${caseRows.length} line items</span>
    </div>
    ${renderTable(
      [
        "case_id",
        "process",
        "case_scenario_type",
        "line_id",
        "material",
        "vendor",
        "plant",
        "purchasing_org",
        "storage",
        "quantity",
        "target_price",
      ],
      caseRows,
    )}
  `;
}

function renderManifest(model) {
  if (!model.manifest) {
    return renderBlank("No manifest", "Load a post-processing manifest to inspect post-processing truth.");
  }

  const manifest = model.manifest;
  return `
    <div class="section-title">
      <h2>Post-Processing Manifest</h2>
      <span>${escapeHtml(manifest.manifest_version || "unknown version")}</span>
    </div>
    <div class="manifest-grid">
      ${renderDataSection(
        "Timestamp Policy",
        renderKeyValueRows(manifest.timestamp_policy || {}),
      )}
      ${renderDataSection(
        "Actor Projection",
        renderTable(
          ["synthetic_actor_id", "technical_sap_user_id", "actor_session_id", "expose_as"],
          (manifest.actor_projection || []).map((item) => [
            item.synthetic_actor_id,
            item.technical_sap_user_id,
            item.actor_session_id,
            item.expose_as,
          ]),
        ),
      )}
      ${renderDataSection(
        "Planned Step Timestamps",
        renderTable(
          ["planned_step_id", "case_id", "step_type", "start", "end", "planned_date_inputs"],
          (manifest.planned_step_timestamps || []).map((item) => [
            item.planned_step_id,
            item.case_id,
            item.step_type,
            item.planned_synthetic_start,
            item.planned_synthetic_end,
            item.planned_date_inputs,
          ]),
        ),
      )}
      ${renderDataSection(
        "Required SAP Object Keys",
        renderTable(
          ["planned_step_id", "case_id", "required_sap_object_keys"],
          (manifest.required_sap_object_keys || []).map((item) => [
            item.planned_step_id,
            item.case_id,
            item.required_sap_object_keys,
          ]),
        ),
      )}
      ${renderDataSection(
        "Object Lineage",
        renderTable(
          ["case_id", "chain"],
          (manifest.object_lineage || []).map((item) => [item.case_id, item.chain]),
        ),
      )}
      ${renderDataSection(
        "Planned Date Input Overrides",
        renderTable(
          ["planned_step_id", "case_id", "step_type", "object_type", "field", "planned_value", "runtime_policy", "reason"],
          (manifest.planned_date_input_overrides || []).map((item) => [
            item.planned_step_id,
            item.case_id,
            item.step_type,
            item.object_type,
            item.field,
            item.planned_value,
            item.runtime_value_policy,
            item.reason,
          ]),
        ),
      )}
      ${renderDataSection(
        "Exports",
        renderTable(
          ["id", "description"],
          (manifest.post_processing_exports || []).map((item) => [item.id, item.description]),
        ),
      )}
      ${renderDataSection(
        "Failed Process Case Policy",
        renderKeyValueRows(manifest.failed_process_case_policy || {}),
      )}
    </div>
  `;
}

function renderRaw(model) {
  return `
    <div class="section-title">
      <h2>Raw Parsed Data</h2>
      <span>JSON view</span>
    </div>
    <div class="raw-grid">
      ${renderDataSection("Execution Trace", renderJsonBlock(model.execution || null))}
      ${renderDataSection("Post-Processing Manifest", renderJsonBlock(model.manifest || null))}
    </div>
  `;
}

function renderDetail(model) {
  if (!model.execution) {
    return `
      <div class="detail-header">
        <h2>Run Detail</h2>
        <span>${escapeHtml(model.runId)}</span>
      </div>
      ${renderKeyValueRows({
        run_id: model.runId,
        has_execution_trace: Boolean(model.execution),
        has_manifest: Boolean(model.manifest),
      })}
    `;
  }

  const node = model.nodeById.get(state.selectedNodeId) || model.nodes[0];
  if (!node) {
    return renderBlank("No planned step selected", "Trace has no dependency graph planned steps.");
  }

  const schedule = model.scheduleByNode.get(node.planned_step_id);
  const timestamp = model.timestampByNode.get(node.planned_step_id);
  const expectedKeys = model.expectedKeysByNode.get(node.planned_step_id) || [];
  const dateOverrides = model.dateOverridesByNode.get(node.planned_step_id) || [];
  const incoming = model.edges.filter((edge) => edge.to === node.planned_step_id);
  const outgoing = model.edges.filter((edge) => edge.from === node.planned_step_id);

  return `
    <div class="detail-header">
      <div>
        <h2>${escapeHtml(node.planned_step_id)}</h2>
        <span>${escapeHtml(node.step_type)}</span>
      </div>
      <button type="button" class="ghost compact" data-clear-selection>Clear</button>
    </div>
    ${renderDataSection(
      "Execution Node",
      renderKeyValueRows({
        case_id: node.case_id,
        tool_name: node.tool_name,
        synthetic_actor_id: node.synthetic_actor_id,
        technical_sap_user_id: node.technical_sap_user_id,
        actor_session_id: node.actor_session_id,
        wave_id: schedule?.wave_id || "",
        startup_order: schedule?.startup_order || "",
        planned_start: node.planned_synthetic_time?.start,
        planned_end: node.planned_synthetic_time?.end,
      }),
    )}
    ${renderDataSection("Inputs", renderJsonBlock(node.inputs || {}))}
    ${renderDataSection("Required SAP Object Keys", renderJsonBlock(node.required_sap_object_keys || []))}
    ${renderDataSection("Planned Date Inputs", renderJsonBlock(node.planned_date_inputs || {}))}
    ${renderDataSection("Labels", renderJsonBlock(node.labels || {}))}
    ${renderDataSection("Incoming Dependencies", renderJsonBlock(incoming))}
    ${renderDataSection("Outgoing Dependencies", renderJsonBlock(outgoing))}
    ${renderDataSection("Manifest Timestamp", renderJsonBlock(timestamp || null))}
    ${renderDataSection("Manifest Required SAP Object Keys", renderJsonBlock(expectedKeys))}
    ${renderDataSection("Manifest Planned Date Input Overrides", renderJsonBlock(dateOverrides))}
    ${renderDataSection("Full Planned Step Object", renderJsonBlock(node))}
  `;
}

function renderBlank(title, body) {
  return `
    <div class="blank">
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(body)}</p>
    </div>
  `;
}

function renderDataSection(title, body) {
  return `
    <section class="data-section">
      <h3>${escapeHtml(title)}</h3>
      ${body}
    </section>
  `;
}

function renderTable(headers, rows) {
  if (!rows || rows.length === 0) {
    return `<p class="muted">No records.</p>`;
  }
  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows
            .map(
              (row) => `
                <tr>${row.map((cell) => `<td>${formatTableCell(cell)}</td>`).join("")}</tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderKeyValueRows(value) {
  const entries = Object.entries(value || {});
  if (entries.length === 0) {
    return `<p class="muted">No data.</p>`;
  }
  return `
    <dl class="kv-list">
      ${entries
        .map(
          ([key, item]) => `
            <div>
              <dt>${escapeHtml(key)}</dt>
              <dd>${formatTableCell(item)}</dd>
            </div>
          `,
        )
        .join("")}
    </dl>
  `;
}

function renderJsonBlock(value) {
  if (value === null || value === undefined) {
    return `<p class="muted">No data.</p>`;
  }
  return `<pre class="json-block">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
}

function bindEvents() {
  document.querySelector("#runSelect")?.addEventListener("change", (event) => {
    state.activeRunId = event.target.value;
    state.selectedNodeId = "";
    render();
  });

  document.querySelector("#traceFiles")?.addEventListener("change", async (event) => {
    await loadFiles([...event.target.files]);
    event.target.value = "";
  });

  document.querySelector("#loadPaste")?.addEventListener("click", loadPastedArtifacts);

  document.querySelector("#clearRuns")?.addEventListener("click", () => {
    state.runs.clear();
    state.activeRunId = "";
    state.selectedNodeId = "";
    state.messages = [];
    render();
  });

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeView = button.dataset.view;
      render();
    });
  });

  document.querySelector("#searchInput")?.addEventListener("input", (event) => {
    state.query = event.target.value;
    render();
  });

  document.querySelectorAll("[data-node-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedNodeId = button.dataset.nodeId;
      render();
    });
  });

  document.querySelector("[data-clear-selection]")?.addEventListener("click", () => {
    state.selectedNodeId = "";
    render();
  });
}

async function loadFiles(files) {
  if (files.length === 0) {
    return;
  }
  const parsed = [];
  const messages = [];

  for (const file of files) {
    try {
      parsed.push({
        value: parseArtifactText(await file.text(), file.name),
        sourceName: file.name,
        sourceType: "file",
      });
    } catch (error) {
      messages.push({ type: "error", text: `${file.name}: ${error.message}` });
    }
  }

  addArtifacts(parsed, messages);
}

function loadPastedArtifacts() {
  const executionText = document.querySelector("#executionPaste")?.value || "";
  const manifestText = document.querySelector("#manifestPaste")?.value || "";
  const parsed = [];
  const messages = [];

  if (executionText.trim()) {
    try {
      parsed.push({
        value: parseArtifactText(executionText, "pasted execution trace"),
        sourceName: "pasted execution trace",
        sourceType: "paste",
      });
    } catch (error) {
      messages.push({ type: "error", text: `Pasted execution trace: ${error.message}` });
    }
  }

  if (manifestText.trim()) {
    try {
      parsed.push({
        value: parseArtifactText(manifestText, "pasted post-processing manifest"),
        sourceName: "pasted post-processing manifest",
        sourceType: "paste",
      });
    } catch (error) {
      messages.push({ type: "error", text: `Pasted manifest: ${error.message}` });
    }
  }

  if (parsed.length === 2) {
    const [first, second] = parsed;
    const firstRunId = first.value?.run_id;
    const secondRunId = second.value?.run_id;
    if (firstRunId && secondRunId && firstRunId !== secondRunId) {
      messages.push({
        type: "warning",
        text: `Pasted run_id mismatch: ${firstRunId} vs ${secondRunId}. They will load as separate runs.`,
      });
    }
  }

  addArtifacts(parsed, messages);
}

function addArtifacts(parsed, messages) {
  state.messages = [...messages];

  parsed.forEach(({ value, sourceName, sourceType }) => {
    const type = classifyArtifact(value);
    if (!type) {
      state.messages.push({ type: "warning", text: `${sourceName}: artifact type not recognized.` });
      return;
    }

    const runId = normalizeRunId(value.run_id, sourceName);
    const run = state.runs.get(runId) || {
      runId,
      execution: null,
      manifest: null,
      sources: [],
      warnings: [],
    };

    if (run[type]) {
      run.warnings.push(`${sourceName}: replaced existing ${type.replace("_", " ")} for run ${runId}.`);
    }

    run[type] = value;
    run.sources.push({ sourceName, sourceType, type });
    state.runs.set(runId, run);
    state.activeRunId = state.activeRunId || runId;
  });

  if (!state.runs.has(state.activeRunId)) {
    state.activeRunId = [...state.runs.keys()][0] || "";
  }

  state.selectedNodeId = "";
  render();
}

function parseArtifactText(text, name) {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error("input is empty");
  }

  try {
    return JSON.parse(trimmed);
  } catch (jsonError) {
    try {
      return YAML.parse(trimmed);
    } catch (yamlError) {
      throw new Error(`could not parse JSON or YAML (${jsonError.message}; ${yamlError.message})`);
    }
  }
}

function classifyArtifact(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "";
  }
  if (value.dependency_graph && value.execution_schedule) {
    return "execution";
  }
  if (value.timestamp_policy && Array.isArray(value.planned_step_timestamps)) {
    return "manifest";
  }
  return "";
}

function normalizeRunId(runId, sourceName) {
  if (typeof runId === "string" && runId.trim()) {
    return runId.trim();
  }
  return `missing-run-id:${sourceName}`;
}

function getActiveModel() {
  const run = state.runs.get(state.activeRunId);
  if (!run) {
    return null;
  }

  const execution = run.execution;
  const manifest = run.manifest;
  const nodes = execution?.dependency_graph?.planned_steps || [];
  const edges = (execution?.dependency_graph?.dependencies || []).map((edge) => ({
    ...edge,
    from: edge.from_planned_step_id,
    to: edge.to_planned_step_id,
  }));
  const missingEdgeSourceCount = edges.filter((edge) => !edge.from).length;
  if (missingEdgeSourceCount > 0) {
    warnMissingEdgeSources(run.runId, missingEdgeSourceCount);
  }
  const sessions = execution?.actor_sessions || [];
  const cases = execution?.cases || [];
  const waves = execution?.execution_schedule?.waves || [];

  const nodeById = new Map(nodes.map((node) => [node.planned_step_id, node]));
  const caseById = new Map(cases.map((item) => [item.case_id, item]));
  const scheduleByNode = buildScheduleIndex(waves);
  const timestampByNode = indexByFirst(manifest?.planned_step_timestamps || [], "planned_step_id");
  const expectedKeysByNode = groupBy(manifest?.required_sap_object_keys || [], "planned_step_id");
  const dateOverridesByNode = groupBy(manifest?.planned_date_input_overrides || [], "planned_step_id");
  const lineageByCase = indexByFirst(manifest?.object_lineage || [], "case_id");

  const caseRows = buildCaseRows({ nodes, cases, caseById });
  const visibleNodes = nodes.filter((node) => isVisibleNode(node, caseById.get(node.case_id)));

  return {
    ...run,
    execution,
    manifest,
    nodes,
    edges,
    sessions,
    cases,
    waves,
    nodeById,
    caseById,
    scheduleByNode,
    timestampByNode,
    expectedKeysByNode,
    dateOverridesByNode,
    lineageByCase,
    caseRows,
    visibleNodes,
    warnings: buildRunWarnings(run, execution, manifest),
  };
}

function ensureValidNodeSelection(model) {
  if (!model?.execution) {
    state.selectedNodeId = "";
    return;
  }
  if (!model.nodeById.has(state.selectedNodeId)) {
    state.selectedNodeId = model.nodes[0]?.planned_step_id || "";
  }
}

function warnMissingEdgeSources(runId, count) {
  const key = `${runId}:${count}`;
  if (warnedMissingEdgeSources.has(key)) {
    return;
  }
  warnedMissingEdgeSources.add(key);
  console.warn(`ERP trace visualizer: ${count} edge(s) in run ${runId} have no source.`);
}

function buildScheduleIndex(waves) {
  const byNode = new Map();
  waves.forEach((wave) => {
    (wave.planned_steps || []).forEach((node) => {
      byNode.set(node.planned_step_id, {
        wave_id: wave.wave_id,
        sequence_no: wave.sequence_no,
        startup_order: node.startup_order,
      });
    });
  });
  return byNode;
}

function buildCaseRows({ nodes, cases, caseById }) {
  const grouped = groupBy(nodes, "case_id");
  const caseIds = new Set([...cases.map((item) => item.case_id), ...grouped.keys()]);

  return [...caseIds].map((caseId) => {
    const caseRecord = caseById.get(caseId) || {};
    const rowNodes = [...(grouped.get(caseId) || [])].sort(compareNodesByTime);
    return {
      caseId,
      processType: caseRecord.process_type || "",
      caseScenarioType: caseRecord.case_scenario_type || "",
      caseMatches: matchesText([caseId, caseRecord], state.query),
      nodes: rowNodes,
    };
  });
}

function buildRunWarnings(run, execution, manifest) {
  const warnings = [...run.warnings];
  if (execution && manifest && execution.run_id && manifest.run_id && execution.run_id !== manifest.run_id) {
    warnings.push(`run_id mismatch: execution=${execution.run_id}, manifest=${manifest.run_id}.`);
  }
  if (execution && manifest && execution.config_hash && manifest.config_hash && execution.config_hash !== manifest.config_hash) {
    warnings.push("config_hash mismatch between execution trace and manifest.");
  }
  return warnings;
}

function compareNodesByTime(left, right) {
  const leftTime = Date.parse(left.planned_synthetic_time?.start || "");
  const rightTime = Date.parse(right.planned_synthetic_time?.start || "");
  if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime) && leftTime !== rightTime) {
    return leftTime - rightTime;
  }
  return String(left.planned_step_id).localeCompare(String(right.planned_step_id));
}

function isVisibleNode(node, caseRecord) {
  if (!state.query.trim()) {
    return true;
  }
  return matchesText([node, caseRecord], state.query);
}

function matchesNode(node, model) {
  return isVisibleNode(node, model.caseById.get(node.case_id));
}

function matchesText(values, query) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  return values
    .map((value) => (typeof value === "string" ? value : JSON.stringify(value || "")))
    .join(" ")
    .toLowerCase()
    .includes(normalized);
}

function groupBy(items, key) {
  const grouped = new Map();
  items.forEach((item) => {
    const value = item?.[key] || "";
    if (!grouped.has(value)) {
      grouped.set(value, []);
    }
    grouped.get(value).push(item);
  });
  return grouped;
}

function indexByFirst(items, key) {
  const index = new Map();
  items.forEach((item) => {
    const value = item?.[key] || "";
    if (!index.has(value)) {
      index.set(value, item);
    }
  });
  return index;
}

function formatRange(start, end) {
  if (!start && !end) {
    return "no planned time";
  }
  if (!end || start === end) {
    return formatDateTime(start);
  }
  return `${formatDateTime(start)} - ${formatDateTime(end)}`;
}

function formatDateTime(value) {
  if (!value) {
    return "";
  }
  return String(value).replace("T", " ").replace(/\+\d\d:\d\d$/, "");
}

function formatTableCell(value) {
  if (value === null || value === undefined || value === "") {
    return `<span class="muted">-</span>`;
  }
  if (Array.isArray(value)) {
    return escapeHtml(value.join(", "));
  }
  if (typeof value === "object") {
    return `<code>${escapeHtml(JSON.stringify(value))}</code>`;
  }
  return escapeHtml(String(value));
}

function destroyGraph() {
  if (graph) {
    graph.destroy();
    graph = null;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function breakable(value) {
  return escapeHtml(value)
    .replaceAll("_", "_<wbr>")
    .replaceAll("-", "-<wbr>")
    .replaceAll(".", ".<wbr>");
}
