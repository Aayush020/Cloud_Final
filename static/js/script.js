// ── SECTION NAVIGATION ──────────────────────────────────────────────────────
function showSection(name, el) {
  event.preventDefault();
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const sec = document.getElementById('sec-' + name);
  if (sec) sec.classList.add('active');
  if (el) el.classList.add('active');
  if (name === 'analytics') {
    setTimeout(() => initCharts(), 100);
  }
}

// ── COUNTER ANIMATION ────────────────────────────────────────────────────────
document.querySelectorAll('.counter').forEach(el => {
  const target = parseInt(el.dataset.target, 10);
  let cur = 0;
  const step = Math.max(1, Math.ceil(target / 30));
  const iv = setInterval(() => {
    cur = Math.min(cur + step, target);
    el.textContent = cur;
    if (cur >= target) clearInterval(iv);
  }, 40);
});

// ── DRAG & DROP UPLOAD ───────────────────────────────────────────────────────
const dropZone  = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const uploadQueue = document.getElementById('uploadQueue');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  [...e.dataTransfer.files].forEach(uploadFile);
});
fileInput.addEventListener('change', () => { [...fileInput.files].forEach(uploadFile); fileInput.value = ''; });

function uploadFile(file) {
  const item = document.createElement('div');
  item.className = 'q-item';
  item.innerHTML = `<div class="spinner"></div><span class="q-name">${file.name}</span><span class="q-size">${fmtSize(file.size)}</span>`;
  uploadQueue.appendChild(item);

  // Show hash visualizer
  showHashViz(file.name);

  const fd = new FormData();
  fd.append('file', file);

  fetch('/upload', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(data => {
      item.remove();
      if (data.error) {
        item.innerHTML = `<span style="color:#ef4444">Error: ${data.error}</span>`;
        uploadQueue.appendChild(item);
        console.error('Upload error:', data.trace || data.error);
        return;
      }
      updateHashViz(data);
      showResultModal(data);
      setTimeout(() => location.reload(), 2500);
    })
    .catch(err => {
      item.innerHTML = `<span style="color:#ef4444">Network error uploading ${file.name}</span>`;
      uploadQueue.appendChild(item);
      console.error(err);
    });
}

// ── HASH VISUALIZER ──────────────────────────────────────────────────────────
function showHashViz(filename) {
  const viz = document.getElementById('hashViz');
  viz.classList.remove('hidden');
  document.getElementById('pv-name').textContent = filename;
  document.getElementById('pv-md5').textContent  = 'computing…';
  document.getElementById('pv-sha').textContent  = 'waiting…';
  document.getElementById('pv-result').textContent = 'Processing…';
  document.getElementById('pv-icon').textContent   = '⏳';
  document.getElementById('ps-md5').classList.add('active');
  document.getElementById('chunkInfo').textContent = '';
}

function updateHashViz(data) {
  if (!data.md5) return;
  document.getElementById('pv-md5').textContent = data.md5;
  document.getElementById('ps-md5').classList.remove('active');
  document.getElementById('ps-md5').classList.add('done');
  document.getElementById('pv-sha').textContent = data.sha256;
  document.getElementById('ps-sha').classList.add('done');

  const status  = data.status;
  const iconMap = { uploaded: '✅', duplicate: '⚠️', versioned: '🔄' };
  const resMap  = { uploaded: 'Stored & Encrypted', duplicate: 'Duplicate — Referenced', versioned: 'New Version Saved' };
  document.getElementById('pv-icon').textContent    = iconMap[status] || '✅';
  document.getElementById('pv-result').textContent  = resMap[status] || 'Done';
  document.getElementById('ps-result').classList.add('done');

  if (data.chunks) {
    document.getElementById('chunkInfo').textContent = `File split into ${data.chunks} chunk(s) for block-level de-duplication analysis`;
  }
}

// ── RESULT MODAL ─────────────────────────────────────────────────────────────
function showResultModal(data) {
  const iconMap  = { uploaded: '✅', duplicate: '⚠️', versioned: '🔄' };
  const colorMap = { uploaded: '#22c55e', duplicate: '#f97316', versioned: '#6c63ff' };
  document.getElementById('modalIcon').textContent  = iconMap[data.status] || '✅';
  document.getElementById('modalTitle').textContent = data.message;
  document.getElementById('modalTitle').style.color = colorMap[data.status] || '#22c55e';
  document.getElementById('modalMsg').textContent   = '';

  let hashHtml = '';
  if (data.md5)    hashHtml += `<div><span>MD5:</span> ${data.md5}</div>`;
  if (data.sha256) hashHtml += `<div><span>SHA-256:</span> ${data.sha256}</div>`;
  if (data.saved)  hashHtml += `<div><span>Space Saved:</span> ${data.saved}</div>`;
  if (data.size)   hashHtml += `<div><span>File Size:</span> ${data.size}</div>`;
  if (data.chunks) hashHtml += `<div><span>Chunks Analyzed:</span> ${data.chunks}</div>`;
  if (data.version)hashHtml += `<div><span>Version:</span> v${data.version}</div>`;
  document.getElementById('modalHash').innerHTML = hashHtml;

  openModal('resultModal');
}

// ── VERIFY INTEGRITY ─────────────────────────────────────────────────────────
function verifyFile(fileId, name) {
  fetch(`/verify/${fileId}`)
    .then(r => r.json())
    .then(data => {
      document.getElementById('modalIcon').textContent  = data.ok ? '✅' : '🚨';
      document.getElementById('modalTitle').textContent = data.ok ? 'File Integrity Verified!' : 'Integrity Check FAILED!';
      document.getElementById('modalTitle').style.color = data.ok ? '#22c55e' : '#ef4444';
      document.getElementById('modalMsg').textContent   = name;
      let html = '';
      if (data.stored_hash)  html += `<div><span>Stored hash:</span> ${data.stored_hash}</div>`;
      if (data.recomputed)   html += `<div><span>Recomputed:</span> ${data.recomputed}</div>`;
      if (!data.ok && data.status === 'tampered') html += `<div style="color:#ef4444;font-weight:600">⚠️ File may have been tampered with!</div>`;
      document.getElementById('modalHash').innerHTML = html;
      openModal('resultModal');
    });
}

// ── SHARE ─────────────────────────────────────────────────────────────────────
let currentShareFileId = null;
function openShare(fileId, name) {
  currentShareFileId = fileId;
  document.getElementById('shareFileName').textContent = name;
  document.getElementById('shareResult').classList.add('hidden');
  document.getElementById('sharePassword').value = '';
  document.getElementById('shareExpiry').value   = '24';
  openModal('shareModal');
}

function generateShare() {
  if (!currentShareFileId) return;
  const btn = document.getElementById('genShareBtn');
  btn.textContent = 'Generating…';
  btn.disabled = true;
  fetch(`/share/${currentShareFileId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expires_hours: parseInt(document.getElementById('shareExpiry').value),
      password: document.getElementById('sharePassword').value
    })
  })
  .then(r => r.json())
  .then(data => {
    document.getElementById('shareUrl').value = data.url;
    document.getElementById('shareResult').classList.remove('hidden');
    btn.textContent = 'Generated ✓';
  })
  .catch(() => { btn.textContent = 'Error'; })
  .finally(() => { btn.disabled = false; });
}

function copyUrl() {
  const url = document.getElementById('shareUrl').value;
  navigator.clipboard.writeText(url).then(() => {
    const btn = document.querySelector('.btn-copy');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  });
}

// ── VERSIONS ─────────────────────────────────────────────────────────────────
function showVersions(fileId, name) {
  document.getElementById('versionsFileName').textContent = name;
  document.getElementById('versionsList').innerHTML = '<p style="color:var(--text-secondary);text-align:center">Loading…</p>';
  openModal('versionsModal');
  fetch(`/versions/${fileId}`)
    .then(r => r.json())
    .then(data => {
      if (!data.length) {
        document.getElementById('versionsList').innerHTML = '<p style="color:var(--text-secondary)">No versions found.</p>';
        return;
      }
      document.getElementById('versionsList').innerHTML = data.map(v => `
        <div class="versions-list-item">
          <div>
            <strong>v${v.version}</strong>
            <span style="font-size:0.78rem;color:var(--text-secondary);margin-left:10px">${v.date}</span>
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <span style="font-size:0.78rem;color:var(--text-secondary)">${v.size}</span>
            <a href="/download/${v.id}" class="act-btn dl-btn" title="Download v${v.version}">⬇</a>
          </div>
        </div>
      `).join('');
    });
}

// ── DELETE ────────────────────────────────────────────────────────────────────
function deleteFile(fileId) {
  if (!confirm('Delete this file and all its versions?')) return;
  fetch(`/delete/${fileId}`, { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d.status === 'deleted') {
        const row = document.getElementById(`row-${fileId}`);
        if (row) { row.style.opacity = '0'; row.style.transition = 'opacity 0.3s'; setTimeout(() => row.remove(), 300); }
      }
    });
}

// ── SEARCH ────────────────────────────────────────────────────────────────────
function filterFiles(q) {
  document.querySelectorAll('.file-row').forEach(row => {
    row.style.display = row.dataset.name.includes(q.toLowerCase()) ? '' : 'none';
  });
}

// ── CHARTS ────────────────────────────────────────────────────────────────────
let chartsInited = false;
let uploadsChartInst = null;
let savedChartInst   = null;

function initCharts() {
  const uploadsCanvas = document.getElementById('uploadsChart');
  const savedCanvas   = document.getElementById('savedChart');
  if (!uploadsCanvas || !savedCanvas) return;

  // Destroy old instances if they exist
  if (uploadsChartInst) { uploadsChartInst.destroy(); uploadsChartInst = null; }
  if (savedChartInst)   { savedChartInst.destroy();   savedChartInst   = null; }

  const isDark    = document.documentElement.getAttribute('data-theme') === 'dark';
  const gridColor = isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#94a3b8' : '#6b7280';

  const defaults = {
    responsive: true,
    maintainAspectRatio: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 11 } } },
      y: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 11 } }, beginAtZero: true }
    }
  };

  uploadsChartInst = new Chart(uploadsCanvas, {
    type: 'bar',
    data: {
      labels: CHART_LABELS,
      datasets: [{
        data: CHART_UPLOADS,
        backgroundColor: 'rgba(108,99,255,0.7)',
        borderRadius: 6,
        hoverBackgroundColor: '#6c63ff'
      }]
    },
    options: defaults
  });

  savedChartInst = new Chart(savedCanvas, {
    type: 'line',
    data: {
      labels: CHART_LABELS,
      datasets: [{
        data: CHART_SAVED,
        borderColor: '#22c55e',
        backgroundColor: 'rgba(34,197,94,0.12)',
        fill: true,
        tension: 0.4,
        pointBackgroundColor: '#22c55e',
        pointRadius: 4
      }]
    },
    options: defaults
  });
}

// ── MODAL HELPERS ─────────────────────────────────────────────────────────────
function openModal(id) {
  document.getElementById('overlay').classList.remove('hidden');
  document.getElementById(id).classList.remove('hidden');
}
function closeAllModals() {
  document.getElementById('overlay').classList.add('hidden');
  ['resultModal','shareModal','versionsModal'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
  });
}

// ── UTILS ─────────────────────────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}
