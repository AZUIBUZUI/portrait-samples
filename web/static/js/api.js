/* 通用请求封装 + 小工具，供各页面脚本共用 */

const API = {
  async get(path) {
    const res = await fetch(path);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
    return data;
  },
  async send(path, method, body) {
    const res = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
    return data;
  },
  post(path, body) { return this.send(path, 'POST', body); },
  patch(path, body) { return this.send(path, 'PATCH', body); },
  del(path) { return this.send(path, 'DELETE'); },
  async upload(path, formData) {
    const res = await fetch(path, { method: 'POST', body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `上传失败 ${res.status}`);
    return data;
  },
};

let __toastTimer = null;
function toast(message, isError = false) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.hidden = false;
  clearTimeout(__toastTimer);
  __toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
}

/** 顶栏的样片总数 / 未分类数 */
async function refreshTopbar() {
  const el = document.getElementById('topbar-stats');
  if (!el) return null;
  try {
    const s = await API.get('/api/stats');
    el.textContent = `共 ${s.total} 张 · 未分类 ${s.unlabeled}`;
    return s;
  } catch (err) {
    el.textContent = '';
    return null;
  }
}

/** 填充分类下拉框 */
async function fillCategorySelect(select, { placeholder = '', selected = '' } = {}) {
  if (!select) return [];
  const cats = await API.get('/api/categories');
  select.innerHTML = '';
  if (placeholder) select.append(new Option(placeholder, ''));
  cats.forEach(c => select.append(new Option(`${c.name}（${c.sample_count}）`, c.id)));
  if (selected !== '' && selected !== null && selected !== undefined) select.value = selected;
  return cats;
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

/** 图片地址：优先缩略图，没有则回退原图 */
function imgUrl(item) {
  return item.thumb_url || item.original_url || '';
}
