import YAML from "yaml";
import cytoscape from "cytoscape";
import "./styles.css";

const views = [
  ["caseGantt", "Case Gantt"],
  ["waveMatrix", "Wave Matrix"],
  ["actorCalendar", "Actor Calendar"],
  ["graph", "Graph"],
  ["sessions", "Actor Sessions"],
  ["cases", "Cases"],
  ["manifest", "Manifest"],
  ["raw", "Raw"],
];

const actorPalette = [
  { bg: "#2f7f75", fg: "#ffffff", soft: "#e3f4f1", border: "#8fc9c0" },
  { bg: "#8a6a2f", fg: "#ffffff", soft: "#f3ead9", border: "#cfb886" },
  { bg: "#6f6f8f", fg: "#ffffff", soft: "#e8e8f1", border: "#b5b5cf" },
  { bg: "#a15543", fg: "#ffffff", soft: "#f5e3de", border: "#d69b8c" },
  { bg: "#4f7a46", fg: "#ffffff", soft: "#e6f0e4", border: "#a9c9a1" },
  { bg: "#78628c", fg: "#ffffff", soft: "#eee6f3", border: "#c0abd0" },
];

const state = {
  runs: new Map(),
  activeRunId: "",
  activeView: "caseGantt",
  selectedNodeId: "",
  query: "",
  messages: [],
  calendarMode: "month",
  calendarCursorDate: "",
  calendarActorId: "",
  expandedCalendarDays: new Set(),
  ganttZoom: 0.35,
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
  ensureCalendarState(model);
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
        <p class="eyebrow">trace inspection</p>
        <h1>ERP Trace Visualizer</h1>
        <p>Validate planned time, execution waves, actor calendars, and post-processing truth.</p>
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
  const timeRange = model.timeRange
    ? `${formatDate(model.timeRange.startLabel)} - ${formatDate(model.timeRange.endLabel)}`
    : "missing manifest time";
  return `
    <section class="summary-grid" aria-label="Run summary">
      ${metric("Run", model.runId)}
      ${metric("Process Cases", model.cases.length)}
      ${metric("Planned Steps", model.nodes.length)}
      ${metric("Execution Waves", model.waves.length)}
      ${metric("Synthetic Actors", model.actors.length)}
      ${metric("Planned Range", timeRange)}
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
          <span class="field-label">Execution Trace</span>
          <textarea id="executionPaste" spellcheck="false" placeholder="Paste current execution trace YAML or JSON"></textarea>
        </label>
        <label>
          <span class="field-label">Post-Processing Manifest</span>
          <textarea id="manifestPaste" spellcheck="false" placeholder="Paste current manifest YAML or JSON"></textarea>
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
      messages.push({ type: "warning", text: "No Execution Trace loaded for this run." });
    }
    if (!model.manifest) {
      messages.push({ type: "warning", text: "No Post-Processing Manifest loaded for this run." });
    }
    messages.push(...model.warnings.map((text) => ({ type: "warning", text })));
  }

  if (messages.length === 0) {
    return "";
  }

  return `
    <section class="message-stack" aria-live="polite">
      ${messages.map((message) => `<div class="message ${escapeAttr(message.type)}">${escapeHtml(message.text)}</div>`).join("")}
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
        <input id="searchInput" value="${escapeAttr(state.query)}" placeholder="case, actor, step, material, vendor" ${disabled} />
      </label>
    </section>
  `;
}

function renderWorkspace(model) {
  if (!model) {
    return `
      <main class="empty-state">
        <div class="empty-skeleton">
          <span></span><span></span><span></span>
        </div>
        <h2>No trace loaded</h2>
        <p>Upload or paste a current Execution Trace and Post-Processing Manifest.</p>
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
  if (state.activeView === "waveMatrix") {
    return renderWaveMatrix(model);
  }
  if (state.activeView === "actorCalendar") {
    return renderActorCalendar(model);
  }
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
  return renderCaseGantt(model);
}

function renderCaseGantt(model) {
  const missing = renderMissingTimeModel(model, "Case Gantt", "Load both current artifacts to inspect post-processed planned time per Process Case.");
  if (missing) {
    return missing;
  }

  const rows = model.caseGanttRows.filter((row) => row.caseMatches || row.steps.some((step) => matchesEnrichedStep(step)));
  if (rows.length === 0) {
    return renderBlank("No matching Process Cases", "Search did not match case, actor, step, material, or vendor data.");
  }

  const ticks = buildTimeTicks(model.timeRange, Math.round(8 * state.ganttZoom));
  const boardWidth = ganttBoardWidth(model.timeRange, state.ganttZoom);
  const zoomPercent = Math.round(state.ganttZoom * 100);

  return `
    <div class="section-title">
      <div>
        <h2>Case Gantt</h2>
        <p>Post-Processing Manifest planned timestamps, joined with case material and actor data.</p>
      </div>
      <span>${rows.length} case rows</span>
    </div>
    <div class="actor-legend">
      ${model.actors.map((actor) => renderActorLegend(actor)).join("")}
    </div>
    <div class="gantt-toolbar">
      <label class="zoom-control">
        <span class="field-label">Gantt Zoom</span>
        <input id="ganttZoom" type="range" min="0.18" max="2.6" step="0.02" value="${escapeAttr(state.ganttZoom)}" />
      </label>
      <strong id="ganttZoomValue">${zoomPercent}%</strong>
      <span>Zoom out for full-horizon overview; zoom in for step-level inspection.</span>
    </div>
    <div class="gantt-scroll">
      <div class="gantt-board" data-gantt-board style="width:max(100%, ${boardWidth}px)">
        <div class="gantt-axis">
          <div class="gantt-axis-rail">Process Case</div>
          <div class="gantt-axis-track">
            ${ticks
              .map(
                (tick) => `
                  <span class="gantt-tick" style="left:${tick.left}%">
                    <i></i>
                    <strong>${escapeHtml(tick.label)}</strong>
                  </span>
                `,
              )
              .join("")}
          </div>
        </div>
        ${rows
          .map((row) => {
            const visibleSteps = row.steps.filter((step) => row.caseMatches || matchesEnrichedStep(step));
            return `
              <section class="gantt-row" style="min-height:${Math.max(128, 78 + row.maxLane * 34)}px">
                ${renderCaseRail(row.caseRecord, row.lineItem)}
                <div class="gantt-track">
                  ${visibleSteps.map((step) => renderGanttBar(step, model.timeRange)).join("")}
                </div>
              </section>
            `;
          })
          .join("")}
      </div>
    </div>
  `;
}

function renderCaseRail(caseRecord, lineItem) {
  return `
    <div class="case-rail">
      <strong>${escapeHtml(caseRecord.case_id || "-")}</strong>
      <span>${escapeHtml(caseRecord.process_type || "process")}</span>
      <small>RDD ${escapeHtml(caseRecord.requested_delivery_date || "-")}</small>
      <dl>
        <div><dt>Material</dt><dd>${escapeHtml(lineItem.material_id || "-")}</dd></div>
        <div><dt>Vendor</dt><dd>${escapeHtml(lineItem.vendor_id || "-")}</dd></div>
        <div><dt>Qty</dt><dd>${escapeHtml(lineItem.quantity ?? "-")}</dd></div>
        <div><dt>Price</dt><dd>${escapeHtml(formatMoney(lineItem.target_price))}</dd></div>
      </dl>
    </div>
  `;
}

function renderGanttBar(step, range) {
  const geometry = stepBarGeometry(step, range);
  const selected = step.plannedStepId === state.selectedNodeId ? "selected" : "";
  return `
    <button
      type="button"
      class="gantt-bar ${selected}"
      data-node-id="${escapeAttr(step.plannedStepId)}"
      title="${escapeAttr(stepTitle(step))}"
      style="left:${geometry.left}%;width:${geometry.width}%;--lane-top:${28 + (step.rowLaneIndex || 0) * 34}px;--actor-bg:${step.actorColor.bg};--actor-soft:${step.actorColor.soft};--actor-border:${step.actorColor.border}"
    >
      <span>${escapeHtml(compactStepLabel(step.stepType))}</span>
      <em>${escapeHtml(step.actorId)}</em>
    </button>
  `;
}

function renderWaveMatrix(model) {
  if (!model.execution) {
    return renderBlank("No Wave Matrix", "Load an Execution Trace to inspect Execution Waves.");
  }

  const actors = model.actors;
  const rows = model.waveMatrix.filter((row) => !state.query.trim() || row.steps.some((step) => matchesEnrichedStep(step)));
  if (rows.length === 0) {
    return renderBlank("No matching Execution Waves", "Search did not match wave, actor, case, material, or step data.");
  }

  return `
    <div class="section-title">
      <div>
        <h2>Wave Matrix</h2>
        <p>Execution Wave order top-to-bottom, Synthetic Actors left-to-right.</p>
      </div>
      <span>${rows.length} waves</span>
    </div>
    <div class="matrix-scroll">
      <div class="wave-matrix" style="grid-template-columns: 144px repeat(${actors.length}, minmax(220px, 1fr));">
        <div class="matrix-corner">Wave</div>
        ${actors.map((actor) => `<div class="matrix-actor">${renderActorLegend(actor)}</div>`).join("")}
        ${rows
          .map(
            (row) => `
              <div class="matrix-wave">
                <strong>${escapeHtml(row.wave.wave_id)}</strong>
                <span>#${escapeHtml(row.wave.sequence_no)}</span>
              </div>
              ${actors
                .map((actor) => {
                  const cellSteps = (row.byActor.get(actor.id) || []).filter((step) => !state.query.trim() || matchesEnrichedStep(step));
                  return `
                    <div class="matrix-cell ${cellSteps.length ? "" : "empty"}">
                      ${cellSteps.map((step) => renderWaveStepCard(step)).join("") || "<span>No planned step</span>"}
                    </div>
                  `;
                })
                .join("")}
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderWaveStepCard(step) {
  const selected = step.plannedStepId === state.selectedNodeId ? "selected" : "";
  return `
    <button type="button" class="matrix-step ${selected}" data-node-id="${escapeAttr(step.plannedStepId)}">
      <span class="step-order">#${escapeHtml(step.schedule?.startup_order || "-")}</span>
      <strong>${escapeHtml(step.caseId)} ${escapeHtml(shortStepType(step.stepType))}</strong>
      <small>${escapeHtml(formatTime(step.plannedStart))} - ${escapeHtml(formatTime(step.plannedEnd))}</small>
      <em>${escapeHtml(step.lineItem.material_id || "-")} / ${escapeHtml(step.lineItem.quantity ?? "-")}</em>
    </button>
  `;
}

function renderActorCalendar(model) {
  const missing = renderMissingTimeModel(model, "Actor Calendar", "Load both current artifacts to inspect one Synthetic Actor at a time.");
  if (missing) {
    return missing;
  }

  const actor = model.actors.find((item) => item.id === state.calendarActorId) || model.actors[0];
  if (!actor) {
    return renderBlank("No Synthetic Actors", "Execution Trace has no actor sessions.");
  }

  const actorEvents = model.actorCalendarEvents
    .filter((event) => event.actorId === actor.id)
    .filter((event) => matchesEnrichedStep(event.step));
  const cursor = parseIsoDate(state.calendarCursorDate) || new Date(model.timeRange.startMs);
  const title = state.calendarMode === "week" ? formatWeekTitle(cursor) : formatMonthTitle(cursor);

  return `
    <div class="section-title">
      <div>
        <h2>Actor Calendar</h2>
        <p>Outlook-style planned work for one Synthetic Actor.</p>
      </div>
      <span>${actorEvents.length} visible events</span>
    </div>
    <div class="calendar-toolbar">
      <label class="calendar-control">
        <span class="field-label">Synthetic Actor</span>
        <select id="calendarActorSelect">
          ${model.actors
            .map((item) => `<option value="${escapeAttr(item.id)}" ${item.id === actor.id ? "selected" : ""}>${escapeHtml(item.id)}</option>`)
            .join("")}
        </select>
      </label>
      <div class="segmented" aria-label="Calendar mode">
        <button type="button" data-calendar-mode="month" class="${state.calendarMode === "month" ? "active" : ""}">Month</button>
        <button type="button" data-calendar-mode="week" class="${state.calendarMode === "week" ? "active" : ""}">Week</button>
      </div>
      <div class="calendar-nav">
        <button type="button" class="ghost compact" data-calendar-nav="prev">Previous</button>
        <button type="button" class="ghost compact" data-calendar-nav="today">Today</button>
        <button type="button" class="ghost compact" data-calendar-nav="next">Next</button>
      </div>
      <strong class="calendar-title">${escapeHtml(title)}</strong>
    </div>
    ${state.calendarMode === "week" ? renderWeekCalendar(actorEvents, cursor, actor) : renderMonthCalendar(actorEvents, cursor, actor)}
  `;
}

function renderMonthCalendar(events, cursor, actor) {
  const days = monthGridDays(cursor);
  const currentMonth = cursor.getUTCMonth();
  const grouped = groupEventsByDate(events);
  return `
    <div class="month-calendar">
      ${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => `<div class="calendar-weekday">${day}</div>`).join("")}
      ${days
        .map((day) => {
          const key = isoDate(day);
          const dayEvents = grouped.get(key) || [];
          const expandedKey = calendarDayExpansionKey(actor.id, key);
          const isExpanded = state.expandedCalendarDays.has(expandedKey);
          const visible = isExpanded ? dayEvents : dayEvents.slice(0, 3);
          return `
            <section class="month-day ${day.getUTCMonth() === currentMonth ? "" : "muted-day"} ${isExpanded ? "expanded" : ""}">
              <header>
                <span>${escapeHtml(day.getUTCDate())}</span>
                ${dayEvents.length ? `<strong>${dayEvents.length}</strong>` : ""}
              </header>
              <div class="month-events">
                ${visible.map((event) => renderCalendarPill(event, actor)).join("")}
                ${
                  dayEvents.length > 3
                    ? `<button type="button" class="calendar-more" data-calendar-day="${escapeAttr(key)}">${isExpanded ? "Show fewer" : `+${dayEvents.length - visible.length} more`}</button>`
                    : ""
                }
              </div>
            </section>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderWeekCalendar(events, cursor, actor) {
  const days = weekDays(cursor);
  const startHour = 7;
  const endHour = 19;
  const hourHeight = 74;
  const gridHeight = (endHour - startHour) * hourHeight;
  return `
    <div class="week-calendar">
      <div class="week-hour-head"></div>
      ${days.map((day) => `<div class="week-day-head"><strong>${escapeHtml(shortWeekday(day))}</strong><span>${escapeHtml(formatDate(isoDate(day)))}</span></div>`).join("")}
      <div class="week-hours" style="height:${gridHeight}px">
        ${Array.from({ length: endHour - startHour + 1 }, (_, index) => startHour + index)
          .map((hour) => `<span style="top:${(hour - startHour) * hourHeight}px">${String(hour).padStart(2, "0")}:00</span>`)
          .join("")}
      </div>
      ${days
        .map((day) => {
          const dayEvents = eventSegmentsForDay(events, day);
          return `
            <div class="week-day-column" style="height:${gridHeight}px">
              ${Array.from({ length: endHour - startHour }, (_, index) => `<i style="top:${index * hourHeight}px"></i>`).join("")}
              ${dayEvents.map((event) => renderWeekEvent(event, actor, startHour, endHour, hourHeight)).join("")}
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderCalendarPill(event, actor) {
  const selected = event.step.plannedStepId === state.selectedNodeId ? "selected" : "";
  return `
    <button
      type="button"
      class="calendar-pill ${selected}"
      data-node-id="${escapeAttr(event.step.plannedStepId)}"
      style="--actor-bg:${actor.color.bg};--actor-soft:${actor.color.soft};--actor-border:${actor.color.border}"
      title="${escapeAttr(stepTitle(event.step))}"
    >
      <strong>${escapeHtml(formatTime(event.step.plannedStart))}</strong>
      <span>${escapeHtml(event.step.caseId)} ${escapeHtml(shortStepType(event.step.stepType))}</span>
    </button>
  `;
}

function renderWeekEvent(event, actor, startHour, endHour, hourHeight) {
  const selected = event.step.plannedStepId === state.selectedNodeId ? "selected" : "";
  const segmentStart = Number.isFinite(event.segmentStartMs) ? new Date(event.segmentStartMs) : null;
  const segmentEnd = Number.isFinite(event.segmentEndMs) ? new Date(event.segmentEndMs) : null;
  const startMinutes = (segmentStart ? minutesFromDate(segmentStart) : minutesFromArtifactTime(event.step.plannedStart)) - startHour * 60;
  const endMinutes = (segmentEnd ? minutesFromDate(segmentEnd) : minutesFromArtifactTime(event.step.plannedEnd)) - startHour * 60;
  const top = Math.max(0, Math.min((endHour - startHour) * hourHeight - 24, (startMinutes / 60) * hourHeight));
  const height = Math.max(30, Math.min((endHour - startHour) * hourHeight - top, ((endMinutes - startMinutes) / 60) * hourHeight));
  return `
    <button
      type="button"
      class="week-event ${selected}"
      data-node-id="${escapeAttr(event.step.plannedStepId)}"
      style="top:${top}px;height:${height}px;--actor-bg:${actor.color.bg};--actor-soft:${actor.color.soft};--actor-border:${actor.color.border}"
      title="${escapeAttr(stepTitle(event.step))}"
    >
      <strong>${escapeHtml(segmentStart ? formatTime(segmentStart.toISOString()) : formatTime(event.step.plannedStart))}</strong>
      <span>${escapeHtml(event.step.caseId)} ${escapeHtml(shortStepType(event.step.stepType))}</span>
      <em>${escapeHtml(event.step.lineItem.material_id || "")} ${escapeHtml(event.step.lineItem.quantity ?? "")}</em>
    </button>
  `;
}

function renderGraphView(model) {
  if (!model.execution) {
    return renderBlank("No dependency graph", "Load an Execution Trace to see dependencies.");
  }

  return `
    <div class="section-title">
      <div>
        <h2>Dependency Graph</h2>
        <p>Planned Step dependency graph from the Execution Trace.</p>
      </div>
      <span>${model.visibleNodes.length} visible Planned Steps</span>
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
          "border-color": "#dcebe8",
          "border-width": 2,
          color: "#172033",
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
          "background-color": "#8a6a2f",
          "border-color": "#e7d7b8",
          "border-width": 4,
        },
      },
      {
        selector: "edge",
        style: {
          "curve-style": "bezier",
          "line-color": "#9aa7b2",
          "target-arrow-color": "#9aa7b2",
          "target-arrow-shape": "triangle",
          width: 2,
          label: "data(label)",
          "font-size": 9,
          color: "#5a6570",
          "text-background-color": "#f7f9fb",
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
    return renderBlank("No Actor Sessions", "Load an Execution Trace to inspect Actor Sessions.");
  }

  return `
    <div class="section-title">
      <div>
        <h2>Actor Sessions</h2>
        <p>Synthetic Actor to Technical SAP User mapping.</p>
      </div>
      <span>${model.sessions.length} records</span>
    </div>
    ${renderTable(
      ["actor_session_id", "synthetic_actor_id", "technical_sap_user_id", "username_env_var", "login_url_env_var", "delay_multiplier", "runtime_delay_cap_seconds"],
      model.sessions.map((session) => [
        session.actor_session_id,
        session.synthetic_actor_id,
        session.technical_sap_user_id,
        session.username_env_var,
        session.login_url_env_var,
        session.human_delay_profile?.delay_multiplier,
        session.human_delay_profile?.runtime_delay_cap_seconds,
      ]),
    )}
  `;
}

function renderCases(model) {
  if (!model.execution) {
    return renderBlank("No Process Cases", "Load an Execution Trace to inspect input cases.");
  }

  const caseRows = model.cases.flatMap((item) => {
    const lineItems = Array.isArray(item.line_items) && item.line_items.length > 0 ? item.line_items : [{}];
    return lineItems.map((lineItem) => [
      item.case_id,
      item.process_type,
      item.case_scenario_type,
      item.requested_delivery_date,
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
      <div>
        <h2>Process Cases</h2>
        <p>Configured case input data used by Planned Steps.</p>
      </div>
      <span>${caseRows.length} line items</span>
    </div>
    ${renderTable(
      [
        "case_id",
        "process",
        "case_scenario_type",
        "requested_delivery_date",
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
    return renderBlank("No Post-Processing Manifest", "Load a manifest to inspect post-processing truth.");
  }

  const manifest = model.manifest;
  return `
    <div class="section-title">
      <div>
        <h2>Post-Processing Manifest</h2>
        <p>Post-Processor contract for timestamps, actor projection, lineage, and planned date overrides.</p>
      </div>
      <span>${escapeHtml(manifest.manifest_version || "unknown version")}</span>
    </div>
    <div class="manifest-grid">
      ${renderDataSection("Timestamp Policy", renderKeyValueRows(manifest.timestamp_policy || {}))}
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
        renderTable(["case_id", "chain"], (manifest.object_lineage || []).map((item) => [item.case_id, item.chain])),
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
        renderTable(["id", "description"], (manifest.post_processing_exports || []).map((item) => [item.id, item.description])),
      )}
      ${renderDataSection("Failed Process Case Policy", renderKeyValueRows(manifest.failed_process_case_policy || {}))}
    </div>
  `;
}

function renderRaw(model) {
  return `
    <div class="section-title">
      <div>
        <h2>Raw Parsed Data</h2>
        <p>Parsed JSON view of loaded artifacts.</p>
      </div>
      <span>debug</span>
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

  const enriched = model.enrichedByNode.get(state.selectedNodeId) || model.enrichedSteps[0];
  const node = enriched?.node || model.nodes[0];
  if (!node) {
    return renderBlank("No Planned Step selected", "Execution Trace has no Planned Steps.");
  }
  const schedule = enriched?.schedule || model.scheduleByNode.get(node.planned_step_id);
  const timestamp = enriched?.timestamp || model.timestampByNode.get(node.planned_step_id);
  const expectedKeys = model.expectedKeysByNode.get(node.planned_step_id) || [];
  const dateOverrides = model.dateOverridesByNode.get(node.planned_step_id) || [];
  const incoming = model.edges.filter((edge) => edge.to === node.planned_step_id);
  const outgoing = model.edges.filter((edge) => edge.from === node.planned_step_id);
  const caseRecord = enriched?.caseRecord || model.caseById.get(node.case_id) || {};
  const lineItem = enriched?.lineItem || firstLineItem(caseRecord);

  return `
    <div class="detail-header">
      <div>
        <h2>${escapeHtml(node.planned_step_id)}</h2>
        <span>${escapeHtml(node.step_type)}</span>
      </div>
      <button type="button" class="ghost compact" data-clear-selection>Clear</button>
    </div>
    ${renderDataSection(
      "Procurement Facts",
      renderKeyValueRows({
        case_id: caseRecord.case_id,
        process_type: caseRecord.process_type,
        requested_delivery_date: caseRecord.requested_delivery_date,
        material_id: lineItem.material_id,
        vendor_id: lineItem.vendor_id,
        quantity: lineItem.quantity,
        target_price: lineItem.target_price,
        plant: lineItem.plant,
        purchasing_org: lineItem.purchasing_org,
        storage_location: lineItem.storage_location,
      }),
    )}
    ${renderDataSection(
      "Schedule Facts",
      renderKeyValueRows({
        wave_id: schedule?.wave_id,
        wave_sequence_no: schedule?.sequence_no,
        startup_order: schedule?.startup_order,
        synthetic_actor_id: node.synthetic_actor_id,
        actor_session_id: node.actor_session_id,
        technical_sap_user_id: node.technical_sap_user_id,
      }),
    )}
    ${renderDataSection(
      "Manifest Time",
      renderKeyValueRows({
        planned_synthetic_start: timestamp?.planned_synthetic_start,
        planned_synthetic_end: timestamp?.planned_synthetic_end,
        planned_date_inputs: timestamp?.planned_date_inputs,
      }),
    )}
    ${renderDataSection("Inputs", renderJsonBlock(node.inputs || {}))}
    ${renderDataSection("Required SAP Object Keys", renderJsonBlock(node.required_sap_object_keys || []))}
    ${renderDataSection("Planned Date Inputs", renderJsonBlock(node.planned_date_inputs || {}))}
    ${renderDataSection("Labels", renderJsonBlock(node.labels || {}))}
    ${renderDataSection("Incoming Dependencies", renderJsonBlock(incoming))}
    ${renderDataSection("Outgoing Dependencies", renderJsonBlock(outgoing))}
    ${renderDataSection("Manifest Required SAP Object Keys", renderJsonBlock(expectedKeys))}
    ${renderDataSection("Manifest Planned Date Input Overrides", renderJsonBlock(dateOverrides))}
    ${renderDataSection("Full Planned Step Object", renderJsonBlock(node))}
  `;
}

function renderBlank(title, body) {
  return `
    <div class="blank">
      <div class="empty-skeleton compact-skeleton">
        <span></span><span></span><span></span>
      </div>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(body)}</p>
    </div>
  `;
}

function renderMissingTimeModel(model, title, body) {
  if (!model.execution || !model.manifest) {
    return renderBlank(title, body);
  }
  if (!model.timeRange) {
    return renderBlank("No manifest timestamps", "Loaded manifest has no valid planned_synthetic_start/end values.");
  }
  return "";
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
          ${rows.map((row) => `<tr>${row.map((cell) => `<td>${formatTableCell(cell)}</td>`).join("")}</tr>`).join("")}
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
              <dt>${escapeHtml(formatKey(key))}</dt>
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

function renderActorLegend(actor) {
  return `
    <span class="actor-chip" style="--actor-bg:${actor.color.bg};--actor-soft:${actor.color.soft};--actor-border:${actor.color.border}">
      <i></i>${escapeHtml(actor.id)}
    </span>
  `;
}

function bindEvents() {
  document.querySelector("#runSelect")?.addEventListener("change", (event) => {
    state.activeRunId = event.target.value;
    state.selectedNodeId = "";
    state.calendarActorId = "";
    state.calendarCursorDate = "";
    state.expandedCalendarDays.clear();
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
    state.calendarActorId = "";
    state.calendarCursorDate = "";
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

  document.querySelector("#ganttZoom")?.addEventListener("input", (event) => {
    updateGanttZoom(Number(event.target.value), false);
  });

  document.querySelector("#ganttZoom")?.addEventListener("change", (event) => {
    updateGanttZoom(Number(event.target.value), true);
  });

  document.querySelector("#ganttZoom")?.addEventListener("pointerup", (event) => {
    updateGanttZoom(Number(event.target.value), true);
  });

  document.querySelector("#ganttZoom")?.addEventListener("keyup", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "Home" || event.key === "End") {
      updateGanttZoom(Number(event.target.value), true);
    }
  });

  function updateGanttZoom(value, shouldRender) {
    state.ganttZoom = clamp(value, 0.18, 2.6);
    const activeModel = getActiveModel();
    const board = document.querySelector("[data-gantt-board]");
    const label = document.querySelector("#ganttZoomValue");
    if (activeModel?.timeRange && board) {
      board.style.width = `max(100%, ${ganttBoardWidth(activeModel.timeRange, state.ganttZoom)}px)`;
    }
    if (label) {
      label.textContent = `${Math.round(state.ganttZoom * 100)}%`;
    }
    if (!shouldRender) {
      return;
    }
    render();
  }

  document.querySelector("#calendarActorSelect")?.addEventListener("change", (event) => {
    state.calendarActorId = event.target.value;
    state.expandedCalendarDays.clear();
    render();
  });

  document.querySelectorAll("[data-calendar-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.calendarMode = button.dataset.calendarMode;
      state.expandedCalendarDays.clear();
      render();
    });
  });

  document.querySelectorAll("[data-calendar-day]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = calendarDayExpansionKey(state.calendarActorId, button.dataset.calendarDay);
      if (state.expandedCalendarDays.has(key)) {
        state.expandedCalendarDays.delete(key);
      } else {
        state.expandedCalendarDays.add(key);
      }
      render();
    });
  });

  document.querySelectorAll("[data-calendar-nav]").forEach((button) => {
    button.addEventListener("click", () => {
      shiftCalendar(button.dataset.calendarNav);
      render();
    });
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
      messages.push({ type: "error", text: `Pasted Execution Trace: ${error.message}` });
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
      messages.push({ type: "error", text: `Pasted Post-Processing Manifest: ${error.message}` });
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
  state.calendarActorId = "";
  state.calendarCursorDate = "";
  state.expandedCalendarDays.clear();
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
  if (value.trace_version === "0.2" && value.dependency_graph && value.execution_schedule) {
    return "execution";
  }
  if (value.manifest_version === "0.2" && value.timestamp_policy && Array.isArray(value.planned_step_timestamps)) {
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
  const actors = buildActors(sessions, nodes);
  const actorById = new Map(actors.map((actor) => [actor.id, actor]));
  const enrichedSteps = buildEnrichedSteps({ nodes, caseById, scheduleByNode, timestampByNode, actorById });
  const enrichedByNode = new Map(enrichedSteps.map((step) => [step.plannedStepId, step]));
  const timeRange = buildTimeRange(enrichedSteps);
  const caseGanttRows = buildCaseGanttRows({ cases, caseById, enrichedSteps });
  const waveMatrix = buildWaveMatrix({ waves, enrichedByNode });
  const actorCalendarEvents = enrichedSteps
    .filter((step) => step.hasManifestTime)
    .map((step) => ({ actorId: step.actorId, date: isoDate(step.startDate), step }));
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
    actors,
    actorById,
    enrichedSteps,
    enrichedByNode,
    timeRange,
    caseGanttRows,
    waveMatrix,
    actorCalendarEvents,
    visibleNodes,
    warnings: buildRunWarnings(run, execution, manifest, enrichedSteps),
  };
}

function ensureCalendarState(model) {
  if (!model?.execution) {
    return;
  }
  if (!state.calendarActorId || !model.actorById.has(state.calendarActorId)) {
    state.calendarActorId = model.actors[0]?.id || "";
  }
  if (!state.calendarCursorDate && model.timeRange) {
    state.calendarCursorDate = isoDate(new Date(model.timeRange.startMs));
  }
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

function buildActors(sessions, nodes) {
  const ids = [];
  sessions.forEach((session) => {
    if (session.synthetic_actor_id && !ids.includes(session.synthetic_actor_id)) {
      ids.push(session.synthetic_actor_id);
    }
  });
  nodes.forEach((node) => {
    if (node.synthetic_actor_id && !ids.includes(node.synthetic_actor_id)) {
      ids.push(node.synthetic_actor_id);
    }
  });
  return ids.map((id, index) => {
    const session = sessions.find((item) => item.synthetic_actor_id === id) || {};
    return {
      id,
      session,
      color: actorPalette[index % actorPalette.length],
    };
  });
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

function buildEnrichedSteps({ nodes, caseById, scheduleByNode, timestampByNode, actorById }) {
  return nodes.map((node) => {
    const caseRecord = caseById.get(node.case_id) || {};
    const lineItem = firstLineItem(caseRecord);
    const timestamp = timestampByNode.get(node.planned_step_id) || null;
    const plannedStart = timestamp?.planned_synthetic_start || "";
    const plannedEnd = timestamp?.planned_synthetic_end || "";
    const startDate = parseArtifactDate(plannedStart);
    const endDate = parseArtifactDate(plannedEnd);
    const actorId = node.synthetic_actor_id || "";
    const actor = actorById.get(actorId) || { color: actorPalette[0] };
    const schedule = scheduleByNode.get(node.planned_step_id) || null;
    return {
      node,
      timestamp,
      caseRecord,
      lineItem,
      schedule,
      plannedStepId: node.planned_step_id,
      caseId: node.case_id,
      stepType: node.step_type,
      actorId,
      actorColor: actor.color,
      plannedStart,
      plannedEnd,
      startDate,
      endDate,
      startMs: startDate?.getTime() ?? Number.NaN,
      endMs: endDate?.getTime() ?? Number.NaN,
      hasManifestTime: Boolean(timestamp && startDate && endDate),
    };
  });
}

function buildTimeRange(enrichedSteps) {
  const timed = enrichedSteps.filter((step) => step.hasManifestTime);
  if (timed.length === 0) {
    return null;
  }
  const startMs = Math.min(...timed.map((step) => step.startMs));
  const endMs = Math.max(...timed.map((step) => step.endMs));
  return {
    startMs,
    endMs,
    durationMs: Math.max(1, endMs - startMs),
    startLabel: new Date(startMs).toISOString().slice(0, 10),
    endLabel: new Date(endMs).toISOString().slice(0, 10),
  };
}

function buildCaseGanttRows({ cases, caseById, enrichedSteps }) {
  const grouped = groupBy(enrichedSteps, "caseId");
  const caseIds = new Set([...cases.map((item) => item.case_id), ...grouped.keys()]);
  return [...caseIds]
    .map((caseId) => {
      const caseRecord = caseById.get(caseId) || { case_id: caseId };
      const steps = [...(grouped.get(caseId) || [])].sort(compareEnrichedByManifestTime);
      steps.forEach((step, index) => {
        step.rowLaneIndex = index;
      });
      return {
        caseId,
        caseRecord,
        lineItem: firstLineItem(caseRecord),
        steps,
        caseMatches: matchesText([caseRecord, firstLineItem(caseRecord)], state.query),
        firstStartMs: Math.min(...steps.filter((step) => step.hasManifestTime).map((step) => step.startMs), Number.POSITIVE_INFINITY),
        maxLane: Math.max(0, steps.length - 1),
      };
    })
    .sort((left, right) => left.firstStartMs - right.firstStartMs || String(left.caseId).localeCompare(String(right.caseId)));
}

function buildWaveMatrix({ waves, enrichedByNode }) {
  return [...waves]
    .sort((left, right) => left.sequence_no - right.sequence_no)
    .map((wave) => {
      const steps = (wave.planned_steps || [])
        .map((item) => enrichedByNode.get(item.planned_step_id))
        .filter(Boolean)
        .sort((left, right) => (left.schedule?.startup_order || 0) - (right.schedule?.startup_order || 0));
      const byActor = groupBy(steps, "actorId");
      return { wave, steps, byActor };
    });
}

function buildRunWarnings(run, execution, manifest, enrichedSteps) {
  const warnings = [...run.warnings];
  if (execution && execution.trace_version !== "0.2") {
    warnings.push(`Unsupported trace_version ${execution.trace_version || "missing"}; expected 0.2.`);
  }
  if (manifest && manifest.manifest_version !== "0.2") {
    warnings.push(`Unsupported manifest_version ${manifest.manifest_version || "missing"}; expected 0.2.`);
  }
  if (execution && manifest && execution.run_id && manifest.run_id && execution.run_id !== manifest.run_id) {
    warnings.push(`run_id mismatch: execution=${execution.run_id}, manifest=${manifest.run_id}.`);
  }
  if (execution && manifest && execution.config_hash && manifest.config_hash && execution.config_hash !== manifest.config_hash) {
    warnings.push("config_hash mismatch between Execution Trace and Post-Processing Manifest.");
  }
  if (execution && manifest) {
    const missing = enrichedSteps.filter((step) => !step.hasManifestTime).length;
    if (missing > 0) {
      warnings.push(`${missing} Planned Step(s) have no valid manifest planned timestamp.`);
    }
  }
  return warnings;
}

function shiftCalendar(direction) {
  const current = parseIsoDate(state.calendarCursorDate) || new Date();
  if (direction === "today") {
    state.calendarCursorDate = isoDate(new Date());
    state.expandedCalendarDays.clear();
    return;
  }
  const next = new Date(current);
  if (state.calendarMode === "week") {
    next.setUTCDate(next.getUTCDate() + (direction === "next" ? 7 : -7));
  } else {
    next.setUTCMonth(next.getUTCMonth() + (direction === "next" ? 1 : -1), 1);
  }
  state.calendarCursorDate = isoDate(next);
  state.expandedCalendarDays.clear();
}

function calendarDayExpansionKey(actorId, dateKey) {
  return `${actorId || "actor"}:${dateKey || "date"}`;
}

function compareEnrichedByManifestTime(left, right) {
  if (left.hasManifestTime && right.hasManifestTime && left.startMs !== right.startMs) {
    return left.startMs - right.startMs;
  }
  return String(left.plannedStepId).localeCompare(String(right.plannedStepId));
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

function matchesEnrichedStep(step) {
  return matchesText([step.node, step.timestamp, step.caseRecord, step.lineItem, step.schedule, step.actorId], state.query);
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

function firstLineItem(caseRecord) {
  return Array.isArray(caseRecord?.line_items) && caseRecord.line_items.length > 0 ? caseRecord.line_items[0] : {};
}

function stepBarGeometry(step, range) {
  if (!step.hasManifestTime || !range) {
    return { left: 0, width: 0.5 };
  }
  const left = ((step.startMs - range.startMs) / range.durationMs) * 100;
  const width = Math.max(0.35, ((step.endMs - step.startMs) / range.durationMs) * 100);
  return {
    left: clamp(left, 0, 99.5).toFixed(3),
    width: clamp(width, 0.35, 100 - left).toFixed(3),
  };
}

function ganttBoardWidth(range, zoom) {
  const days = Math.max(1, Math.ceil(range.durationMs / 86_400_000));
  return Math.max(560, Math.round(days * 180 * zoom));
}

function buildTimeTicks(range, desiredCount) {
  if (!range) {
    return [];
  }
  const ticks = [];
  const count = Math.max(2, desiredCount);
  for (let index = 0; index < count; index += 1) {
    const ms = range.startMs + (range.durationMs * index) / (count - 1);
    ticks.push({
      left: ((ms - range.startMs) / range.durationMs) * 100,
      label: formatDateTimeShort(new Date(ms).toISOString()),
    });
  }
  return ticks;
}

function groupEventsByDate(events) {
  const grouped = new Map();
  events.forEach((event) => {
    if (!grouped.has(event.date)) {
      grouped.set(event.date, []);
    }
    grouped.get(event.date).push(event);
  });
  grouped.forEach((items) => items.sort((left, right) => left.step.startMs - right.step.startMs));
  return grouped;
}

function eventSegmentsForDay(events, day) {
  const dayStartMs = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate());
  const dayEndMs = dayStartMs + 86_400_000;
  return events
    .filter((event) => event.step.startMs < dayEndMs && event.step.endMs > dayStartMs)
    .map((event) => ({
      ...event,
      segmentStartMs: Math.max(event.step.startMs, dayStartMs),
      segmentEndMs: Math.min(event.step.endMs, dayEndMs),
    }))
    .sort((left, right) => left.segmentStartMs - right.segmentStartMs || String(left.step.plannedStepId).localeCompare(String(right.step.plannedStepId)));
}

function monthGridDays(cursor) {
  const monthStart = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth(), 1));
  const start = startOfWeek(monthStart);
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start);
    day.setUTCDate(start.getUTCDate() + index);
    return day;
  });
}

function weekDays(cursor) {
  const start = startOfWeek(cursor);
  return Array.from({ length: 7 }, (_, index) => {
    const day = new Date(start);
    day.setUTCDate(start.getUTCDate() + index);
    return day;
  });
}

function startOfWeek(date) {
  const day = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const weekday = day.getUTCDay() || 7;
  day.setUTCDate(day.getUTCDate() - weekday + 1);
  return day;
}

function parseArtifactDate(value) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function parseIsoDate(value) {
  if (!value) {
    return null;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function minutesFromArtifactTime(value) {
  const match = String(value || "").match(/T(\d{2}):(\d{2})/);
  if (!match) {
    return 0;
  }
  return Number(match[1]) * 60 + Number(match[2]);
}

function minutesFromDate(value) {
  return value.getUTCHours() * 60 + value.getUTCMinutes();
}

function shortStepType(value) {
  return String(value || "")
    .replaceAll("create_", "create ")
    .replaceAll("post_", "post ")
    .replaceAll("enter_", "enter ")
    .replaceAll("_", " ");
}

function compactStepLabel(value) {
  const labels = {
    create_purchase_requisition: "PR",
    create_purchase_order: "PO",
    post_goods_receipt: "GR",
    enter_incoming_invoice: "Invoice",
    post_outgoing_payment: "Payment",
  };
  return labels[value] || shortStepType(value);
}

function stepTitle(step) {
  return `${step.plannedStepId} | ${step.caseId} | ${step.stepType} | ${step.actorId} | ${formatRange(step.plannedStart, step.plannedEnd)}`;
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
  return String(value).replace("T", " ").replace(/\+\d\d:\d\d$/, "").replace(/-\d\d:\d\d$/, "");
}

function formatDateTimeShort(value) {
  if (!value) {
    return "";
  }
  const clean = formatDateTime(value);
  return clean.slice(5, 16);
}

function formatDate(value) {
  if (!value) {
    return "";
  }
  return String(value).slice(5) || String(value);
}

function formatTime(value) {
  if (!value) {
    return "";
  }
  const clean = formatDateTime(value);
  return clean.slice(11, 16);
}

function formatMonthTitle(date) {
  return date.toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
}

function formatWeekTitle(date) {
  const days = weekDays(date);
  return `${formatDate(isoDate(days[0]))} - ${formatDate(isoDate(days[6]))}`;
}

function shortWeekday(date) {
  return date.toLocaleString("en-US", { weekday: "short", timeZone: "UTC" });
}

function formatMoney(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return Number.isFinite(Number(value)) ? Number(value).toFixed(2) : String(value);
}

function formatKey(value) {
  return String(value || "").replaceAll("_", " ");
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

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
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
