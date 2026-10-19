/* 纠正分类：逐张看机器猜的分类，点正确分类即修正并跳到下一张 */
let items = [];
let categories = [];
let index = 0;
let confirmed = 0;

document.addEventListener('DOMContentLoaded', async () => {
  document.getElementById('btn-skip').onclick = () => { index += 1; render(); };
  document.getElementById('btn-train').onclick = train;
  document.getElementById('btn-seed').onclick = seed;

  document.addEventListener('keydown', e => {
    const tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target?.isContentEditable) return;
    const number = Number(e.key);
    if (number >= 1 && number <= categories.length) {
      e.preventDefault();
      choose(categories[number - 1]);
    }
  });

  categories = await API.get('/api/categories');
  await load();
  refreshTopbar();
});

async function load() {
  const data = await API.get('/api/label/pending');
  items = data.items || [];
  confirmed = data.confirmed || 0;
  index = 0;
  render();
}

function render() {
  const wrap = document.getElementById('label-wrap');
  const empty = document.getElementById('label-empty');
  const progress = document.getElementById('label-progress');

  if (index >= items.length) {
    wrap.hidden = true;
    empty.hidden = false;
    progress.textContent = `已纠正 ${confirmed} 张 · 没有待纠正的了`;
    return;
  }
  wrap.hidden = false;
  empty.hidden = true;

  const item = items[index];
  progress.textContent = `还剩 ${items.length - index} 张 · 已纠正 ${confirmed} 张`;
  document.getElementById('label-title').textContent = item.title || item.filename;
  document.getElementById('label-current').textContent = item.category_name
    ? `系统当前猜的是「${item.category_name}」`
    : '系统还没给出分类';
  document.getElementById('label-img').src = item.original_url;
  document.getElementById('label-open').href = item.original_url;

  const box = document.getElementById('label-buttons');
  box.innerHTML = '';
  categories.forEach((cat, i) => {
    const btn = document.createElement('button');
    btn.className = 'label-btn' + (item.category_id === cat.id ? ' current' : '');
    btn.style.setProperty('--cat-color', cat.color || '#6366f1');
    btn.innerHTML = `<span class="label-key">${i + 1}</span><span>${escapeHtml(cat.name)}</span>`;
    btn.onclick = () => choose(cat);
    box.append(btn);
  });
}

async function choose(cat) {
  const item = items[index];
  if (!item) return;
  try {
    await API.patch(`/api/samples/${item.id}`, { category_id: cat.id });
    toast(`已标为「${cat.name}」`);
    items.splice(index, 1);
    confirmed += 1;
    render();
    refreshTopbar();
  } catch (err) {
    toast(err.message, true);
  }
}

async function seed(e) {
  const btn = e.target;
  btn.disabled = true;
  try {
    const r = await API.post('/api/label/seed', { limit: 60 });
    toast(`已抽 ${r.assigned} 张待纠正`);
    await load();
  } catch (err) {
    toast(err.message, true);
  }
  btn.disabled = false;
}

async function train() {
  const btn = document.getElementById('btn-train');
  const out = document.getElementById('train-result');
  btn.disabled = true;
  btn.textContent = '训练中…';
  try {
    const r = await API.post('/api/label/train', { k: 5 });
    if (!r.ok) {
      toast(r.error, true);
      out.textContent = r.error;
    } else {
      const accuracy = r.accuracy == null ? '—' : Math.round(r.accuracy * 100) + '%';
      out.textContent = `训练样本 ${r.trained} 张 · 留一法准确率 ${accuracy} · `
        + `处理 ${r.targets} 张（文字命中 ${r.from_text} / 模型 ${r.predicted} / 跳过 ${r.skipped}）`;
      toast('训练并分类完成');
      await load();
    }
  } catch (err) {
    toast(err.message, true);
  }
  btn.disabled = false;
  btn.textContent = '开始训练并分类';
}
