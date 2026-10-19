/* 分类管理页：新增 / 改关键词 / 删除 / 批量自动分类 */
document.addEventListener('DOMContentLoaded', () => {
  refreshTopbar();
  loadCategories();
  bindEvents();
});

async function loadCategories() {
  const cats = await API.get('/api/categories');
  const tbody = document.querySelector('#cat-table tbody');
  tbody.innerHTML = cats.map(cat => `
    <tr data-id="${cat.id}">
      <td><input type="color" class="swatch-input" value="${cat.color}" data-field="color" title="标记颜色"></td>
      <td><input value="${escapeHtml(cat.name)}" data-field="name"></td>
      <td><input value="${escapeHtml(cat.keywords)}" placeholder="关键词，逗号分隔" data-field="keywords"></td>
      <td>${cat.sample_count}</td>
      <td><button class="btn danger icon" data-action="delete">删除</button></td>
    </tr>`).join('');
  if (!cats.length) tbody.innerHTML = '<tr><td colspan="5" class="hint">还没有分类</td></tr>';

  tbody.querySelectorAll('.swatch-input').forEach(input => {
    input.oninput = () => { input.closest('td').style.background = ''; };
  });

  tbody.querySelectorAll('input[data-field]').forEach(input => {
    input.onchange = async () => {
      const id = input.closest('tr').dataset.id;
      const field = input.dataset.field;
      try {
        await API.patch(`/api/categories/${id}`, { [field]: input.value });
        toast('已保存');
        if (field !== 'color') loadCategories();
      } catch (err) { toast(err.message, true); }
    };
  });

  tbody.querySelectorAll('[data-action="delete"]').forEach(btn => {
    btn.onclick = async () => {
      const row = btn.closest('tr');
      if (!confirm('删除该分类？其下的样片会变为「未分类」，图片不会删除。')) return;
      try {
        await API.del(`/api/categories/${row.dataset.id}`);
        toast('已删除');
        loadCategories();
      } catch (err) { toast(err.message, true); }
    };
  });
}

function bindEvents() {
  document.getElementById('btn-create').onclick = async () => {
    const name = document.getElementById('new-name').value.trim();
    if (!name) return toast('请填写分类名', true);
    try {
      await API.post('/api/categories', {
        name,
        keywords: document.getElementById('new-keywords').value.trim(),
        color: document.getElementById('new-color').value,
      });
      document.getElementById('new-name').value = '';
      document.getElementById('new-keywords').value = '';
      toast('分类已添加');
      loadCategories();
    } catch (err) { toast(err.message, true); }
  };

  document.getElementById('btn-apply-standard').onclick = async e => {
    const okToGo = confirm('确定重设分类标准？\n\n'
      + '· 会删掉现有全部分类，所有样片先回到「未分类」\n'
      + '· 再按「写真 · 画面状态」重建并重跑归类\n'
      + '· 图片文件不会被删除\n\n继续？');
    if (!okToGo) return;
    e.target.disabled = true;
    const el = document.getElementById('standard-result');
    el.textContent = '正在重建分类并重跑…';
    try {
      const r = await API.post('/api/maintenance/apply-standard', {});
      el.textContent = `已建 ${r.categories} 个分类 · 自动归类 ${r.classified} 张 · 仍未分类 ${r.unclassified} 张 · 同步标签 ${r.tagged} 张`;
      toast(`分类标准已重设，自动归类 ${r.classified} 张`);
      loadCategories();
      refreshTopbar();
    } catch (err) {
      toast(err.message, true);
      el.textContent = '';
    }
    e.target.disabled = false;
  };

  document.getElementById('btn-auto-classify').onclick = async e => {
    e.target.disabled = true;
    try {
      const r = await API.post('/api/classify/auto', { only_unlabeled: true });
      document.getElementById('classify-result').textContent =
        `扫描 ${r.scanned} 张 · 归类 ${r.labeled} 张 · 仍无匹配 ${r.skipped} 张`;
      loadCategories();
    } catch (err) { toast(err.message, true); }
    e.target.disabled = false;
  };
}
