const fileSelect = document.getElementById("snapshot-select");
const reloadButton = document.getElementById("reload-button");
const grid = document.getElementById("graph-grid");
const emptyState = document.getElementById("empty-state");
const tooltip = document.getElementById("tooltip");
const metaSection = document.getElementById("snapshot-meta");
const metaRunId = document.getElementById("meta-run-id");
const metaCollected = document.getElementById("meta-collected");
const metaDeviceCount = document.getElementById("meta-device-count");

const COLOR_PALETTE = [
  "#66d9ef",
  "#a6e22e",
  "#fd971f",
  "#f92672",
  "#ae81ff",
  "#e6db74",
  "#2aa198",
  "#ff6e6e",
  "#00bcd4",
  "#cddc39",
  "#ff9800",
  "#ffb6c1",
  "#ffa500",
];

const state = {
  snapshots: [],
  selected: null,
};

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Request failed (${res.status})`);
  }
  return res.json();
}

function formatTimestamp(timestamp) {
  if (!timestamp) return "—";
  try {
    return new Date(timestamp).toLocaleString();
  } catch {
    return timestamp;
  }
}

function setMeta(meta) {
  if (!meta) {
    metaSection.hidden = true;
    return;
  }
  metaSection.hidden = false;
  metaRunId.textContent = meta.run_id || "—";
  metaCollected.textContent = formatTimestamp(meta.collected_at);
  metaDeviceCount.textContent = meta.device_count ?? "—";
}

function showEmpty(message) {
  emptyState.textContent = message;
  emptyState.hidden = false;
  grid.innerHTML = "";
}

function hideEmpty() {
  emptyState.hidden = true;
}

function normalizeDevices(payload) {
  if (Array.isArray(payload)) {
    return { devices: payload, meta: { run_id: "Unknown", collected_at: null, device_count: payload.length } };
  }
  if (payload && typeof payload === "object") {
    const devices = payload.devices || [];
    return {
      devices,
      meta: {
        run_id: payload.run_id || "Unknown",
        collected_at: payload.collected_at || null,
        device_count: payload.device_count ?? devices.length,
      },
    };
  }
  return { devices: [], meta: null };
}

function groupByHubAndConnection(devices) {
  const grouped = {};
  devices.forEach((device) => {
    const hub = device.connected_to || "Unknown Hub";
    const connection = device.connection || "Unknown Connection";
    if (!grouped[hub]) grouped[hub] = {};
    if (!grouped[hub][connection]) grouped[hub][connection] = [];
    grouped[hub][connection].push(device);
  });
  return grouped;
}

function polarToCartesian(cx, cy, radius, angle) {
  return {
    x: cx + radius * Math.cos(angle),
    y: cy + radius * Math.sin(angle),
  };
}

function showTooltip(evt, text) {
  tooltip.hidden = false;
  tooltip.textContent = text;
  tooltip.style.left = `${evt.pageX + 12}px`;
  tooltip.style.top = `${evt.pageY + 12}px`;
}

function hideTooltip() {
  tooltip.hidden = true;
}

function renderGraph(data) {
  grid.innerHTML = "";
  const hubs = Object.entries(data);
  if (!hubs.length) {
    showEmpty("No devices found in this snapshot.");
    return;
  }
  hideEmpty();

  const colorMap = new Map();
  let colorIndex = 0;
  const colorFor = (connection) => {
    if (!colorMap.has(connection)) {
      colorMap.set(connection, COLOR_PALETTE[colorIndex % COLOR_PALETTE.length]);
      colorIndex += 1;
    }
    return colorMap.get(connection);
  };

  hubs.sort(([hubA], [hubB]) => hubA.localeCompare(hubB));
  hubs.forEach(([hub, connections]) => {
    const card = document.createElement("section");
    card.className = "card";
    const heading = document.createElement("h2");
    heading.textContent = hub;
    card.appendChild(heading);

    const width = 320;
    const height = 320;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const center = { x: width / 2, y: height / 2 };

    const hubCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    hubCircle.setAttribute("cx", center.x);
    hubCircle.setAttribute("cy", center.y);
    hubCircle.setAttribute("r", 28);
    hubCircle.setAttribute("fill", "#ffc857");
    hubCircle.setAttribute("stroke", "#ffe39f");
    hubCircle.setAttribute("stroke-width", "2");
    svg.appendChild(hubCircle);

    const hubLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
    hubLabel.setAttribute("x", center.x);
    hubLabel.setAttribute("y", center.y + 4);
    hubLabel.setAttribute("text-anchor", "middle");
    hubLabel.setAttribute("font-size", "12");
    hubLabel.setAttribute("fill", "#0c111d");
    hubLabel.textContent = "Hub";
    svg.appendChild(hubLabel);

    const connectionEntries = Object.entries(connections);
    connectionEntries.sort(([a], [b]) => a.localeCompare(b));

    const connectionRadius = 110;
    const deviceRadius = 42;

    connectionEntries.forEach(([connection, devices], idx) => {
      const angle = (2 * Math.PI * idx) / Math.max(connectionEntries.length, 1);
      const connectionPos = polarToCartesian(center.x, center.y, connectionRadius, angle);

      const link = document.createElementNS("http://www.w3.org/2000/svg", "line");
      link.setAttribute("x1", center.x);
      link.setAttribute("y1", center.y);
      link.setAttribute("x2", connectionPos.x);
      link.setAttribute("y2", connectionPos.y);
      link.setAttribute("stroke", "#1f2c48");
      link.setAttribute("stroke-width", "1.5");
      svg.appendChild(link);

      const connectionCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      connectionCircle.setAttribute("cx", connectionPos.x);
      connectionCircle.setAttribute("cy", connectionPos.y);
      connectionCircle.setAttribute("r", 16);
      connectionCircle.setAttribute("fill", colorFor(connection));
      connectionCircle.setAttribute("stroke", "#05070f");
      connectionCircle.setAttribute("stroke-width", "1.5");
      svg.appendChild(connectionCircle);

      const connectionText = document.createElementNS("http://www.w3.org/2000/svg", "text");
      connectionText.setAttribute("x", connectionPos.x);
      connectionText.setAttribute("y", connectionPos.y + 4);
      connectionText.setAttribute("text-anchor", "middle");
      connectionText.setAttribute("font-size", "10");
      connectionText.setAttribute("fill", "#05070f");
      connectionText.textContent = connection;
      svg.appendChild(connectionText);

      devices.forEach((device, deviceIdx) => {
        const childAngle = (2 * Math.PI * deviceIdx) / Math.max(devices.length, 1);
        const devicePos = polarToCartesian(connectionPos.x, connectionPos.y, deviceRadius, childAngle);

        const spoke = document.createElementNS("http://www.w3.org/2000/svg", "line");
        spoke.setAttribute("x1", connectionPos.x);
        spoke.setAttribute("y1", connectionPos.y);
        spoke.setAttribute("x2", devicePos.x);
        spoke.setAttribute("y2", devicePos.y);
        spoke.setAttribute("stroke", "#243454");
        spoke.setAttribute("stroke-width", "1");
        svg.appendChild(spoke);

        const deviceCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        deviceCircle.setAttribute("cx", devicePos.x);
        deviceCircle.setAttribute("cy", devicePos.y);
        deviceCircle.setAttribute("r", 10);
        deviceCircle.setAttribute("fill", "#0ff0b3");
        deviceCircle.setAttribute("stroke", "#05070f");
        deviceCircle.setAttribute("stroke-width", "1");
        deviceCircle.addEventListener("mousemove", (evt) => {
          const text = `${device.name || "Unnamed"}\n${device.mac_address || "Unknown MAC"}\n${connection} • ${device.status || "Unknown"}`;
          showTooltip(evt, text);
        });
        deviceCircle.addEventListener("mouseleave", hideTooltip);
        svg.appendChild(deviceCircle);
      });
    });

    card.appendChild(svg);

    const legend = document.createElement("div");
    legend.className = "legend";
    connectionEntries.forEach(([connection, devices]) => {
      const item = document.createElement("div");
      item.className = "legend-item";
      const swatch = document.createElement("span");
      swatch.className = "legend-swatch";
      swatch.style.background = colorFor(connection);
      const label = document.createElement("span");
      label.textContent = `${connection} (${devices.length})`;
      item.appendChild(swatch);
      item.appendChild(label);
      legend.appendChild(item);
    });
    card.appendChild(legend);
    grid.appendChild(card);
  });
}

async function loadSnapshot(name) {
  if (!name) {
    showEmpty("Select a snapshot to visualize the network.");
    setMeta(null);
    return;
  }
  showEmpty("Loading snapshot…");
  try {
    const payload = await fetchJSON(`/api/files/${encodeURIComponent(name)}`);
    const { devices, meta } = normalizeDevices(payload);
    setMeta(meta);
    const grouped = groupByHubAndConnection(devices);
    renderGraph(grouped);
  } catch (err) {
    console.error(err);
    showEmpty("Failed to load snapshot. Check console for details.");
  }
}

async function refreshSnapshots() {
  fileSelect.disabled = true;
  fileSelect.innerHTML = '<option value="">Loading…</option>';
  try {
    const { files } = await fetchJSON("/api/files");
    state.snapshots = files;
    if (!files.length) {
      fileSelect.innerHTML = '<option value="">No snapshots found</option>';
      showEmpty("No JSON snapshots detected in the output directory.");
      setMeta(null);
      return;
    }
    fileSelect.innerHTML = files
      .map(
        (file, idx) =>
          `<option value="${file.name}" ${idx === files.length - 1 ? "selected" : ""}>${file.name} (${file.device_count ?? "?"} devices)</option>`,
      )
      .join("");
    fileSelect.disabled = false;
    const selected = fileSelect.value;
    await loadSnapshot(selected);
  } catch (err) {
    console.error(err);
    showEmpty("Failed to load snapshot list.");
  }
}

fileSelect.addEventListener("change", (evt) => {
  const value = evt.target.value;
  loadSnapshot(value);
});

reloadButton.addEventListener("click", () => {
  refreshSnapshots();
});

refreshSnapshots();
