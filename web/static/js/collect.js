/* 采集页：本地文件夹 / 拖拽上传 / 网页采集 + 任务记录 */
document.addEventListener('DOMContentLoaded', async () => {
  await fillCategorySelect(document.getElementById('target-category'), {
    placeholder: '不指定（自动归类）',
  });
  bindEvents();
  refreshJobs();
  refreshStats();
});

function targetCategory() {
  const value = document.getElementById('target-category').value;
  return value ? Number(value) : null;
}

function bindEvents() {
  document.getElementById('btn-import-dir').onclick = async e => {
    const path = document.getElementById('dir-path').value.trim();
    if (!path) return toast('请先填写文件夹路径', true);
    await runCollect(e.target, 'local', path, {
      recursive: document.getElementById('dir-recursive').checked,
    });
  };

  document.getElementById('btn-collect-web').onclick = async e => {
    const urls = document.getElementById('web-urls').value.trim();
    if (!urls) return toast('请先填入网页地址', true);
    const minSide = Number(document.getElementById('web-min-side').value) || 720;
    await runCollect(e.target, 'web', urls, { min_side: minSide });
  };

  document.getElementById('btn-auto-classify') && (
    document.getElementById('btn-auto-classify').onclick = async e => {
      try {
        const r = await API.post('/api/classify/auto', { only_unlabeled: true });
        toast(`扫描 ${r.scanned} 张，归类 ${r.labeled} 张`);
      } catch (err) { toast(err.message, true); }
    });

  document.getElementById('btn-review-samples').onclick = async e => {
    const result = document.getElementById('review-result');
    e.target.disabled = true;
    result.textContent = '正在回审…';
    try {
      const r = await API.post('/api/maintenance/review-samples', {});
      result.textContent = `共 ${r.groups} 组：判为写真 ${r.keep} 组（${r.images.keep} 张）`
        + ` · 待确认 ${r.unsure} 组（${r.images.unsure} 张） · 非人像 ${r.skip} 组（${r.images.skip} 张）`;
      toast(`回审完成：非人像 ${r.images.skip} 张、待确认 ${r.images.unsure} 张（只标记未删）`);
      refreshStats();
    } catch (err) {
      toast(err.message, true);
      result.textContent = '';
    }
    e.target.disabled = false;
  };

  document.getElementById('btn-fix-meta').onclick = async e => {
    const result = document.getElementById('fix-result');
    e.target.disabled = true;
    result.textContent = '正在重新打开原帖…';
    try {
      const r = await API.post('/api/maintenance/fix-web-meta', {});
      result.textContent = `原帖 ${r.notes} 个 · 订正 ${r.updated} 张 · 归类 ${r.classified} 张 · 失败 ${r.failed} 张`
        + (r.applied.length ? `（${r.applied.join('；')}）` : '')
        + (r.details.length ? `　${r.details[0]}` : '');
      toast(`已订正 ${r.updated} 张，其中归类 ${r.classified} 张`);
    } catch (err) {
      toast(err.message, true);
      result.textContent = '';
    }
    e.target.disabled = false;
  };

  bindUpload();
}

/* ---------- 采集通用流程 ---------- */
async function runCollect(btn, sourceType, target, options = {}) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '采集中…';
  try {
    const r = await API.post('/api/collect', {
      source_type: sourceType, target, category_id: targetCategory(), options,
    });
    if (r.status === 'failed') toast(r.message || '采集失败', true);
    else toast(`新增 ${r.succeeded} 张 · 重复 ${r.duplicated} 张 · 失败 ${r.failed} 张`
      + (r.skipped ? ` · 过滤 ${r.skipped} 张` : ''));
    refreshJobs();
    refreshStats();
  } catch (err) {
    toast(err.message, true);
  }
  btn.disabled = false;
  btn.textContent = label;
}

/* ---------- 拖拽上传 ---------- */
function bindUpload() {
  const zone = document.getElementById('dropzone');
  const input = document.getElementById('file-input');
  const progress = document.getElementById('upload-progress');

  zone.onclick = () => input.click();
  zone.ondragover = e => { e.preventDefault(); zone.classList.add('over'); };
  zone.ondragleave = () => zone.classList.remove('over');
  zone.ondrop = e => {
    e.preventDefault();
    zone.classList.remove('over');
    uploadFiles([...e.dataTransfer.files]);
  };
  input.onchange = () => uploadFiles([...input.files]);

  async function uploadFiles(files) {
    const images = files.filter(f => f.type.startsWith('image/'));
    if (!images.length) return toast('没有可上传的图片', true);

    const form = new FormData();
    images.forEach(f => form.append('files', f));
    form.append('category_id', targetCategory() ?? '');

    progress.hidden = false;
    progress.textContent = `正在上传 ${images.length} 张…`;
    try {
      const r = await API.upload('/api/upload', form);
      progress.textContent = `完成：新增 ${r.succeeded} · 重复 ${r.duplicated} · 失败 ${r.failed}`;
      toast(`上传完成，新增 ${r.succeeded} 张`);
      refreshStats();
    } catch (err) {
      progress.textContent = '';
      toast(err.message, true);
    }
    input.value = '';
  }
}

/* ---------- 任务记录 ---------- */
async function refreshJobs() {
  const jobs = await API.get('/api/jobs?limit=20');
  const tbody = document.querySelector('#jobs-table tbody');
  const sourceLabel = { local: '本地文件夹', web: '网页/社媒', manual: '手工上传' };
  tbody.innerHTML = jobs.map(job => `
    <tr>
      <td>${escapeHtml(job.created_at)}</td>
      <td>${sourceLabel[job.source_type] || job.source_type}</td>
      <td title="${escapeHtml(job.target)}">${escapeHtml(shorten(job.target, 46))}</td>
      <td>新增 ${job.succeeded} · 重复 ${job.duplicated} · 失败 ${job.failed}</td>
      <td>${statusText(job)}${job.message ? `<div class="hint small">${escapeHtml(shorten(job.message, 60))}</div>` : ''}</td>
    </tr>`).join('');
  if (!jobs.length) tbody.innerHTML = '<tr><td colspan="5" class="hint">还没有采集记录</td></tr>';
}

function statusText(job) {
  const map = { pending: '等待中', running: '进行中', done: '完成', failed: '失败' };
  const color = job.status === 'done' ? '#16a34a' : job.status === 'failed' ? '#dc2626' : '#7b8494';
  return `<span style="color:${color}">${map[job.status] || job.status}</span>`;
}

function shorten(text, max) {
  const str = String(text || '');
  return str.length > max ? str.slice(0, max) + '…' : str;
}

async function refreshStats() {
  const s = await refreshTopbar();
  const el = document.getElementById('collect-stats');
  if (el && s) el.textContent = `库内共 ${s.total} 张，其中未分类 ${s.unlabeled} 张`;
}
