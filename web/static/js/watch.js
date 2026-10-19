/* 关注博主页：增删博主、启用停用、立即采集，并列出最近处理过的笔记 */
document.addEventListener('DOMContentLoaded', () => {
  bindEvents();
  loadWatchers();
  loadNotes();
  refreshTopbar();
});

function bindEvents() {
  document.getElementById('btn-add-watcher').onclick = async e => {
    const url = document.getElementById('watcher-url').value.trim();
    const name = document.getElementById('watcher-name').value.trim();
    if (!url) return toast('请先填博主主页地址', true);
    e.target.disabled = true;
    try {
      const r = await API.post('/api/watchers', { url, name });
      toast(`已关注 ${r.watcher.name || r.watcher.user_id}`);
      document.getElementById('watcher-url').value = '';
      document.getElementById('watcher-name').value = '';
      loadWatchers();
    } catch (err) {
      toast(err.message, true);
    }
    e.target.disabled = false;
  };

  document.getElementById('btn-run-all').onclick = async e => {
    await runWatchers(e.target, {});
  };
}

/* ---------- 关注列表 ---------- */
async function loadWatchers() {
  const list = await API.get('/api/watchers');
  const tbody = document.querySelector('#watchers-table tbody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="hint">还没有关注任何博主</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(w => `
    <tr data-id="${w.id}">
      <td>
        <strong>${escapeHtml(w.name || '未命名博主')}</strong>
        ${w.enabled ? '' : '<span class="hint small">（已停用）</span>'}
      </td>
      <td class="hint small" title="${escapeHtml(w.url)}">${escapeHtml(shorten(w.url, 38))}</td>
      <td>${w.note_count} 篇 · ${w.image_count} 张</td>
      <td>${escapeHtml(w.last_run_at || '还没跑过')}</td>
      <td>${statusHtml(w.last_status)}${w.last_message
          ? `<div class="hint small">${escapeHtml(shorten(w.last_message, 46))}</div>` : ''}</td>
      <td>
        <div class="row">
          <button class="btn icon" data-act="toggle">${w.enabled ? '停用' : '启用'}</button>
          <button class="btn icon" data-act="run">采集</button>
          <button class="btn icon danger" data-act="del">删除</button>
        </div>
      </td>
    </tr>`).join('');

  tbody.querySelectorAll('button[data-act]').forEach(btn => {
    btn.onclick = handleRowAction;
  });
}

async function handleRowAction(e) {
  const btn = e.target;
  const id = Number(btn.closest('tr').dataset.id);
  const act = btn.dataset.act;
  try {
    if (act === 'del') {
      if (!confirm('删除这个关注？已采进库的样片不会被删。')) return;
      await API.del(`/api/watchers/${id}`);
      toast('已取消关注');
    } else if (act === 'toggle') {
      const enabled = btn.textContent.trim() === '停用' ? 0 : 1;
      await API.patch(`/api/watchers/${id}`, { enabled });
      toast(enabled ? '已启用，明天开始自动跟' : '已停用，不再自动跟');
    } else if (act === 'run') {
      await runWatchers(btn, { watcher_ids: [id] });
    }
    loadWatchers();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------- 跑采集 ---------- */
async function runWatchers(btn, payload) {
  const label = btn.textContent;
  const result = document.getElementById('run-result');
  btn.disabled = true;
  btn.textContent = '采集中…';
  result.textContent = '正在打开博主主页抓新作品，请稍等（可能要一两分钟）…';
  try {
    const r = await API.post('/api/watchers/run', payload);
    result.textContent = r.ok === false ? r.error
      : `${r.watchers} 位博主 · 扫到 ${r.scanned} 篇 · 新增 ${r.new_notes} 篇 / ${r.images_added} 张`
        + ` · 重复 ${r.duplicated} 张`
        + (r.skipped ? ` · 跳过 ${r.skipped} 篇非人像` : '')
        + (r.filtered ? ` · 过滤 ${r.filtered} 张（表情包/低清）` : '')
        + ` · 失败 ${r.failed} 篇`;
    if (r.status === 'failed') toast(r.message || '采集没成功', true);
    else toast(`新增 ${r.new_notes} 篇 / ${r.images_added} 张`);
    loadWatchers();
    loadNotes();
    refreshTopbar();
  } catch (err) {
    result.textContent = '';
    toast(err.message, true);
  }
  btn.disabled = false;
  btn.textContent = label;
}

/* ---------- 最近处理的笔记 ---------- */
async function loadNotes() {
  const notes = await API.get('/api/watchers/notes?limit=30');
  const tbody = document.querySelector('#notes-table tbody');
  if (!notes.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="hint">还没有处理记录</td></tr>';
    return;
  }
  const statusMap = { done: '已入库', empty: '没有图片', skipped: '非人像，已跳过', failed: '失败（下次重试）' };
  tbody.innerHTML = notes.map(n => `
    <tr>
      <td>${escapeHtml(shorten(n.title || n.note_id, 40))}</td>
      <td>${escapeHtml(n.watcher_name || '—')}</td>
      <td>${statusMap[n.status] || n.status}${n.images_added ? ` · 新增 ${n.images_added} 张` : ''}
        ${n.message ? `<div class="hint small">${escapeHtml(shorten(n.message, 50))}</div>` : ''}</td>
      <td>${escapeHtml(n.first_seen_at)}</td>
    </tr>`).join('');
}

function statusHtml(status) {
  const map = { done: '完成', failed: '失败' };
  const color = status === 'done' ? '#16a34a' : status === 'failed' ? '#dc2626' : '#7b8494';
  return `<span style="color:${color}">${map[status] || (status || '—')}</span>`;
}

function shorten(text, max) {
  const str = String(text || '');
  return str.length > max ? str.slice(0, max) + '…' : str;
}
