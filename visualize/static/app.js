const selectEl = document.getElementById('deviceSelect');
const emptyStateEl = document.getElementById('emptyState');
const ctx = document.getElementById('bandwidthChart');
const timeFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
let chart;
let deviceData = [];

async function loadData() {
  try {
    const response = await fetch('/api/bandwidth');
    const payload = await response.json();
    deviceData = payload.devices || [];
    if (!deviceData.length) {
      emptyStateEl.classList.remove('hidden');
      return;
    }
    emptyStateEl.classList.add('hidden');
    populateSelect();
    renderChart();
  } catch (err) {
    console.error('Failed to load bandwidth data', err);
    emptyStateEl.textContent = `Failed to load bandwidth data: ${err.message}`;
    emptyStateEl.classList.remove('hidden');
  }
}

function populateSelect() {
  selectEl.innerHTML = '';
  deviceData.forEach((device, idx) => {
    const option = document.createElement('option');
    option.value = device.device_name;
    option.textContent = `${device.device_name} (${device.ip_address || 'unknown IP'})`;
    if (idx < 5) {
      option.selected = true;
    }
    selectEl.appendChild(option);
  });
}

function renderChart() {
  const selectedNames = Array.from(selectEl.selectedOptions).map((opt) => opt.value);
  const filteredDevices = deviceData.filter((device) => selectedNames.includes(device.device_name));
  const datasets = [];
  const colors = generateColors(filteredDevices.length);
  filteredDevices.forEach((device, idx) => {
    const color = colors[idx];
    const uploadPoints = toChartPoints(device.series, 'upload_mbps');
    const downloadPoints = toChartPoints(device.series, 'download_mbps');
    datasets.push({
      label: `${device.device_name} Upload`,
      data: uploadPoints,
      borderColor: color,
      backgroundColor: color,
      tension: 0.2,
      borderWidth: 2,
    });
    datasets.push({
      label: `${device.device_name} Download`,
      data: downloadPoints,
      borderColor: color,
      backgroundColor: color,
      borderDash: [6, 6],
      tension: 0.2,
      borderWidth: 2,
    });
  });

  const config = {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      stacked: false,
      scales: {
        x: {
          type: 'linear',
          title: { display: true, text: 'Collected at' },
          ticks: {
            callback(value) {
              return formatTimestamp(value);
            },
          },
        },
        y: { title: { display: true, text: 'Throughput (Mbps)' } },
      },
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label(ctx) {
              const value = ctx.parsed.y ?? 0;
              const when = ctx.parsed.x ? formatTimestamp(ctx.parsed.x) : '';
              return `${ctx.dataset.label}: ${value.toFixed(3)} Mbps @ ${when}`;
            },
          },
        },
      },
    },
  };

  if (chart) {
    chart.destroy();
  }
  chart = new Chart(ctx, config);
}

function generateColors(count) {
  const palette = [];
  for (let i = 0; i < count; i += 1) {
    const hue = Math.floor((360 / Math.max(count, 1)) * i);
    palette.push(`hsl(${hue}, 70%, 50%)`);
  }
  return palette;
}

function toChartPoints(series, key) {
  return series
    .map((point) => {
      const time = Date.parse(point.timestamp);
      if (Number.isNaN(time)) {
        return null;
      }
      return { x: time, y: point[key] };
    })
    .filter(Boolean);
}

function formatTimestamp(value) {
  if (!Number.isFinite(value)) {
    return '';
  }
  try {
    return timeFormatter.format(new Date(value));
  } catch (err) {
    return '';
  }
}

selectEl.addEventListener('change', renderChart);

document.addEventListener('DOMContentLoaded', loadData);
