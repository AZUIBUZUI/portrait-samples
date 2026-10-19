/* 图库页：分类筛选 + 多种排序 + 平铺 / 分组视图 + 无限滚动 + 详情编辑 */
const state = {
  category_id: '', q: '', source_type: '', tag: '', favorite: false,
  page: 1, page_size: 60,
  view: 'flat',   // flat = 平铺瀑布流，groups = 按原帖 / 导入批次分组
  group: '',      // 选中的分组键
  groupLabel: '',
  sort: 'newest',                                   // 排序方式，见排序下拉
  seed: Math.floor(Math.random() * 1e9),            // 「随机洗一洗」的稳定种子
  loading: false, done: false,                      // 无限滚动状态
  loaded: 0, total: 0,                              // 已加载 / 命中总数
};
let currentSample = null;
let reqToken = 0;   // 过期的响应直接丢弃，防止筛选快速切换时旧结果覆盖新结果

const $grid = document.getElementById('grid');
const $groups = document.getElementById('groups');
const $crumb = document.getElementById('crumb');
const $empty = document.getElementById('empty');
const $sentinel = document.getElementById('sentinel');
const $endLine = document.getElementById('end-line');
const $flatControls = document.getElementById('flat-controls');
const $statsLine = document.getElementById('stats-line');
const $modal = document.getElementById('modal');

document.addEventListener('DOMContentLoaded', () => {
  bindEvents();
  watchSentinel();
  reloadAll();
});

/* 滚动哨兵：进入视口附近就补下一页，滚轮一到底部自动续上 */
function watchSentinel() {
  const io = new IntersectionObserver(entries => {
    if (entries.some(e => e.isIntersecting)) maybeLoadMore();
  }, { rootMargin: '900px 0px' });
  io.observe($sentinel);
  // 双保险：个别环境（如无头浏览器视口高度为 0）里 IO 永远不触发，用滚动事件兜底
  let last = 0;
  window.addEventListener('scroll', () => {
    const now = Date.now();
    if (now - last < 150) return;
    last = now;
    maybeLoadMore();
  }, { passive: true });
}

/* 哨兵只在"离开→进入"的瞬间发事件；追加后它若还留在视口附近就不会再触发，
   所以这里同时做主动的位置检查，一页接一页直到填过视口为止。 */
function maybeLoadMore() {
  if (state.loading || state.done) return;
  if (state.view === 'groups' && !state.group) return;   // 分组浏览一次性全出，无需加载更多
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  const rect = $sentinel.getBoundingClientRect();
  if (rect.top < vh + 900) {
    state.page += 1;
    loadGrid(true);
  }
}

/* 统一刷新入口：stats 只请求一次，顶栏与分类条共用同一份结果 */
async function reloadAll() {
  const stats = await refreshTopbar();
  await Promise.all([loadChips(stats), loadTagFilter()]);
  renderChrome();
  if (state.view === 'groups' && !state.group) await loadGroups();
  else await loadGrid();
}

/* 根据当前视图切换容器的显隐 */
function renderChrome() {
  const browsingGroups = state.view === 'groups' && !state.group;
  $groups.hidden = !browsingGroups;
  $grid.hidden = browsingGroups;
  if (browsingGroups) $empty.hidden = true;
  $flatControls.hidden = state.view === 'groups';
  updateSentinel();

  $crumb.hidden = !(state.view === 'groups' && state.group);
  if (!$crumb.hidden) {
    $crumb.innerHTML = '';
    const back = document.createElement('button');
    back.className = 'btn icon';
    back.textContent = '← 返回分组';
    back.onclick = () => { state.group = ''; reloadAll(); };
    const label = document.createElement('span');
    label.textContent = `${state.groupLabel || '分组'} · 组图`;
    $crumb.append(back, label);
  }
}

function updateSentinel() {
  const browsingGroups = state.view === 'groups' && !state.group;
  $sentinel.hidden = browsingGroups || state.done || state.loading;
  $endLine.hidden = browsingGroups || !state.done || state.loaded === 0;
}

function updateStatsLine() {
  if (state.view === 'groups' && !state.group) return;   // 分组视图由 loadGroups 自己写
  const scope = state.group ? `组内共 ${state.total} 张` : `共 ${state.total} 张`;
  $statsLine.textContent = state.done
    ? `${scope} · 已全部加载`
    : `${scope} · 已加载 ${state.loaded} 张，继续往下滚动`;
}

/* ---------- 分类筛选条 ---------- */
async function loadChips(stats) {
  const wrap = document.getElementById('category-chips');
  const cats = await API.get('/api/categories');

  const items = [{ id: '', name: '全部', color: '#94a3b8', sample_count: stats ? stats.total : '' }, ...cats];
  wrap.innerHTML = '';
  items.forEach(cat => {
    const btn = document.createElement('button');
    btn.className = 'chip' + (String(state.category_id) === String(cat.id) ? ' active' : '');
    btn.innerHTML = `<span class="dot"></span>${escapeHtml(cat.name)} ${cat.sample_count}`;
    if (String(state.category_id) === String(cat.id)) btn.style.background = cat.color;
    btn.onclick = () => {
      state.category_id = cat.id;
      loadGrid();
    };
    wrap.append(btn);
  });
}

/* ---------- 分组视图 ---------- */
async function loadGroups() {
  const params = new URLSearchParams();
  if (state.category_id !== '') params.set('category_id', state.category_id);
  const data = await API.get('/api/groups?' + params.toString());

  $groups.innerHTML = '';
  if (!data.items.length) {
    const tip = document.createElement('div');
    tip.className = 'hint';
    tip.textContent = '还没有可分组的内容 —— 网页采集的样片会自动按原帖成组，本地导入按文件夹成组，同一批上传算一组。';
    $groups.append(tip);
  }

  data.items.forEach(group => {
    const card = document.createElement('button');
    card.className = 'group-card';
    card.innerHTML = `
      <div class="group-cover"><img src="${escapeHtml(group.cover_url)}" loading="lazy" alt=""></div>
      <div class="group-meta">
        <strong>${escapeHtml(group.label)}</strong>
        <span>${escapeHtml(sourceName(group.source_type))} · ${group.total} 张${group.author ? ' · ' + escapeHtml(group.author) : ''}</span>
      </div>`;
    card.onclick = () => {
      state.group = group.group_key;
      state.groupLabel = group.label;
      reloadAll();
      window.scrollTo({ top: 0 });
    };
    $groups.append(card);
  });

  if (data.ungrouped) {
    const tip = document.createElement('div');
    tip.className = 'hint';
    tip.textContent = `另有 ${data.ungrouped} 张未分组样片，切到「平铺」查看。`;
    $groups.append(tip);
  }

  $statsLine.textContent = `共 ${data.total} 组 · 未分组 ${data.ungrouped} 张`;
}

function sourceName(type) {
  return { local: '本地文件夹', web: '网页 / 社媒', manual: '手工上传' }[type] || type;
}

/* ---------- 标签筛选（风格 / 光线这些，存在标签里） ---------- */
async function loadTagFilter() {
  const select = document.getElementById('tag-filter');
  const tags = await API.get('/api/tags');
  select.innerHTML = '';
  select.append(new Option('全部标签', ''));
  tags.forEach(t => select.append(new Option(`${t.name}（${t.sample_count}）`, t.name)));
  select.value = state.tag;
}

/* ---------- 瀑布流 + 无限滚动 ---------- */
async function loadGrid(append = false) {
  const token = ++reqToken;
  if (!append) state.page = 1;
  state.loading = true;
  updateSentinel();

  const params = new URLSearchParams();
  if (state.category_id !== '') params.set('category_id', state.category_id);
  if (state.q) params.set('q', state.q);
  if (state.source_type) params.set('source_type', state.source_type);
  if (state.favorite) params.set('favorite', '1');
  if (state.tag) params.set('tag', state.tag);
  if (state.group) params.set('group_key', state.group);
  params.set('sort', state.sort);
  params.set('seed', state.seed);
  params.set('page', state.page);
  params.set('page_size', state.page_size);

  try {
    const data = await API.get('/api/samples?' + params.toString());
    if (token !== reqToken) return;   // 期间筛选又变了，这份过期结果直接扔掉
    renderGrid(data, append);
  } catch (err) {
    if (token === reqToken) toast(err.message, true);
  }
  if (token === reqToken) state.loading = false;
  updateSentinel();
  if (token === reqToken) maybeLoadMore();   // 这页装完哨兵还在跟前就接着补
}

function renderGrid(data, append) {
  if (!append) $grid.innerHTML = '';

  data.items.forEach(item => {
    const el = document.createElement('div');
    el.className = 'tile';
    el.dataset.id = item.id;
    el.innerHTML = `
      <img src="${imgUrl(item)}" alt="${escapeHtml(item.title || item.filename)}" loading="lazy">
      ${item.category_name ? `<span class="badge" style="background:${item.category_color || '#6366f1'}">${escapeHtml(item.category_name)}</span>` : ''}
      ${item.is_favorite ? '<span class="star">★</span>' : ''}
      <div class="tile-overlay">
        <strong>${escapeHtml(item.title || item.filename)}</strong>
        <span>${escapeHtml(item.source_author || '未知来源')} · ${escapeHtml(item.shot_date || '无日期')}</span>
      </div>
      <div class="tile-actions">
        <button class="tile-act${item.is_favorite ? ' on' : ''}" data-act="fav"
          title="${item.is_favorite ? '取消收藏' : '收藏'}">${item.is_favorite ? '★' : '☆'}</button>
        <button class="tile-act" data-act="del" title="删除">×</button>
      </div>`;
    el.onclick = () => openModal(item.id);
    el.querySelector('[data-act="fav"]').onclick = e => {
      e.stopPropagation();   // 别顺带把大图弹窗点开
      quickFavorite(item.id, e.currentTarget);
    };
    el.querySelector('[data-act="del"]').onclick = e => {
      e.stopPropagation();
      quickDelete(item.id);
    };
    $grid.append(el);
  });

  state.loaded = append ? state.loaded + data.items.length : data.items.length;
  state.total = data.total;
  state.done = state.loaded >= state.total;
  $empty.hidden = state.total !== 0;
  updateStatsLine();
}

/* 把一张样片从瀑布流里摘掉（删除后用），不用整页重载、滚动位置不丢 */
function removeTile(id) {
  const tile = $grid.querySelector(`.tile[data-id="${id}"]`);
  if (tile) tile.remove();
  state.loaded = Math.max(0, state.loaded - 1);
  state.total = Math.max(0, state.total - 1);
  state.done = state.loaded >= state.total;
  $empty.hidden = !($grid.children.length === 0 && state.total === 0);
  updateStatsLine();
  updateSentinel();
}

/* 保存修改后就地刷新这块瓷砖（分类角标 / 标题 / 收藏星），不打断浏览 */
async function refreshTile(id) {
  let item;
  try { item = await API.get(`/api/samples/${id}`); } catch { return; }
  // 若当前正按分类筛选，而这张被改到了别的分类，就让它从当前视图消失
  const visible = state.category_id === '' ||
    String(state.category_id) === String(item.category_id ?? '');
  if (!visible) { removeTile(id); return; }

  const tile = $grid.querySelector(`.tile[data-id="${id}"]`);
  if (!tile) return;
  let badge = tile.querySelector('.badge');
  if (item.category_name) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'badge';
      tile.prepend(badge);
    }
    badge.style.background = item.category_color || '#6366f1';
    badge.textContent = item.category_name;
  } else if (badge) {
    badge.remove();
  }
  tile.querySelector('.tile-overlay strong').textContent = item.title || item.filename;
  syncTileStar(id, item.is_favorite);
}

/* ---------- 详情弹窗 ---------- */
/* 同组翻页：navIds 是当前样片所在分组的全部 id（按入库顺序），navIndex 是它的下标 */
let navIds = [];
let navIndex = -1;

async function openModal(id) {
  const nav = await API.get(`/api/samples/${id}/neighbors`);
  navIds = nav.ids || [];
  navIndex = nav.index;
  await showSample(id);
  $modal.hidden = false;
}

/* 填当前样片的全部字段，翻页时复用 */
async function showSample(id) {
  const item = await API.get(`/api/samples/${id}`);
  currentSample = item;

  document.getElementById('m-img').src = item.original_url;
  document.getElementById('m-title').textContent = item.title || item.filename;
  document.getElementById('m-tags').value = item.tags.join(',');
  document.getElementById('m-note').value = item.note || '';
  document.getElementById('m-source').textContent = item.source_url || item.source_type;
  document.getElementById('m-author').textContent = item.source_author || '—';
  document.getElementById('m-text').textContent = item.source_text || '—';
  document.getElementById('m-hashtags').textContent = item.source_hashtags || '—';
  document.getElementById('m-date-label').textContent = item.source_type === 'web' ? '发布日期' : '拍摄日期';
  document.getElementById('m-date').textContent = item.shot_date || '—';
  document.getElementById('m-size').textContent = `${item.width} × ${item.height} · ${(item.file_size / 1024).toFixed(0)} KB`;
  document.getElementById('m-open').href = item.original_url;

  await fillCategorySelect(document.getElementById('m-category'), {
    placeholder: '未分类', selected: item.category_id ?? '',
  });
  updateFavButton();
  // 只有被标成「非人像」的样片才显示"人像无误"按钮，人工看过点一下就能去掉标记
  document.getElementById('m-clear-label').hidden =
    !(item.tags || []).includes('非人像');
  renderNav();
}

/* 组内翻页按钮与计数：只有一张（或未分组）时不显示 */
function renderNav() {
  const total = navIds.length;
  const show = total > 1;
  const prev = document.getElementById('m-prev');
  const next = document.getElementById('m-next');
  const count = document.getElementById('m-count');
  prev.hidden = next.hidden = count.hidden = !show;
  if (!show) return;
  prev.disabled = navIndex <= 0;
  next.disabled = navIndex >= total - 1;
  count.textContent = `${navIndex + 1} / ${total}`;
}

async function navigate(delta) {
  const target = navIndex + delta;
  if (target < 0 || target >= navIds.length) return;
  navIndex = target;
  await showSample(navIds[target]);
}

function updateFavButton() {
  document.getElementById('m-fav').textContent = currentSample?.is_favorite ? '取消收藏' : '收藏';
}

async function toggleFavorite() {
  if (!currentSample) return;
  const nextFav = !currentSample.is_favorite;
  try {
    await API.patch(`/api/samples/${currentSample.id}`, { is_favorite: nextFav });
    currentSample.is_favorite = nextFav;
    updateFavButton();
    syncTileStar(currentSample.id, nextFav);
  } catch (err) { toast(err.message, true); }
}

/* 让背后的瀑布流立即反映收藏状态，不用整页重刷 */
function syncTileStar(id, fav) {
  const tile = document.querySelector(`.tile[data-id="${id}"]`);
  if (!tile) return;
  const btn = tile.querySelector('.tile-act[data-act="fav"]');
  if (btn) {
    btn.classList.toggle('on', fav);
    btn.textContent = fav ? '★' : '☆';
    btn.title = fav ? '取消收藏' : '收藏';
  }
  const star = tile.querySelector('.star');
  if (fav && !star) {
    const s = document.createElement('span');
    s.className = 'star';
    s.textContent = '★';
    tile.append(s);
  } else if (!fav && star) {
    star.remove();
  }
}

/* 人工确认是人像后，去掉「非人像」标记（只动标签，不删图） */
async function clearNotPortraitTag() {
  if (!currentSample) return;
  const tags = (currentSample.tags || []).filter(t => t !== '非人像');
  try {
    await API.patch(`/api/samples/${currentSample.id}`, { tags });
    currentSample.tags = tags;
    document.getElementById('m-clear-label').hidden = true;
    document.getElementById('m-tags').value = tags.join(',');
    toast('已去掉「非人像」标记');
    // 标签计数变了；如果正按「非人像」筛选，整页也要刷新
    if (state.tag === '非人像') reloadAll();
    else loadTagFilter();
  } catch (err) { toast(err.message, true); }
}

/* 缩略图上的快捷按钮：不打开弹窗，直接收藏 / 删除 */
async function quickFavorite(id, btn) {
  const fav = !btn.classList.contains('on');
  try {
    await API.patch(`/api/samples/${id}`, { is_favorite: fav });
    syncTileStar(id, fav);
    if (currentSample && currentSample.id === id) {
      currentSample.is_favorite = fav;
      updateFavButton();
    }
  } catch (err) { toast(err.message, true); }
}

async function quickDelete(id) {
  if (!confirm('确定删除这张样片？原图文件也会一并删除。')) return;
  try {
    await API.del(`/api/samples/${id}`);
    removeTile(id);
    refreshTopbar();
    toast('已删除');
  } catch (err) { toast(err.message, true); }
}

async function deleteCurrent() {
  if (!currentSample) return;
  if (!confirm('确定删除这张样片？原图文件也会一并删除。')) return;
  const id = currentSample.id;
  try {
    await API.del(`/api/samples/${id}`);
    toast('已删除');
    const removedAt = navIds.indexOf(id);
    if (removedAt >= 0) navIds.splice(removedAt, 1);
    if (navIds.length) {
      // 删完停在原位置的下一条（删的是最后一张则停在新的最后一张）
      navIndex = Math.min(Math.max(removedAt, 0), navIds.length - 1);
      await showSample(navIds[navIndex]);
    } else {
      closeModal();
    }
    removeTile(id);
    refreshTopbar();
  } catch (err) { toast(err.message, true); }
}

function closeModal() {
  $modal.hidden = true;
  currentSample = null;
  navIds = [];
  navIndex = -1;
}

/* ---------- 事件 ---------- */
function bindEvents() {
  document.querySelectorAll('#view-toggle .seg-btn').forEach(btn => {
    btn.onclick = () => {
      state.view = btn.dataset.view;
      state.group = '';
      document.querySelectorAll('#view-toggle .seg-btn')
        .forEach(b => b.classList.toggle('active', b === btn));
      reloadAll();
    };
  });

  document.getElementById('search').oninput = debounce(e => {
    state.q = e.target.value.trim();
    loadGrid();
  }, 300);

  document.getElementById('sort-select').onchange = e => {
    state.sort = e.target.value;
    // 每次选「随机洗一洗」都换新种子，保证真能洗出新花样
    if (state.sort === 'random') state.seed = Math.floor(Math.random() * 1e9);
    loadGrid();
  };

  document.getElementById('source-filter').onchange = e => {
    state.source_type = e.target.value;
    loadGrid();
  };

  document.getElementById('fav-only').onchange = e => {
    state.favorite = e.target.checked;
    loadGrid();
  };

  document.getElementById('tag-filter').onchange = e => {
    state.tag = e.target.value;
    loadGrid();
  };

  document.getElementById('btn-auto-classify').onclick = async e => {
    e.target.disabled = true;
    try {
      const r = await API.post('/api/classify/auto', { only_unlabeled: true });
      toast(`扫描 ${r.scanned} 张，归类 ${r.labeled} 张`);
      await reloadAll();
    } catch (err) { toast(err.message, true); }
    e.target.disabled = false;
  };

  $modal.querySelectorAll('[data-close]').forEach(el => { el.onclick = closeModal; });

  document.getElementById('m-save').onclick = async () => {
    const id = currentSample.id;
    try {
      await API.patch(`/api/samples/${id}`, {
        category_id: document.getElementById('m-category').value || null,
        tags: document.getElementById('m-tags').value.split(/[,，]/).map(t => t.trim()).filter(Boolean),
        note: document.getElementById('m-note').value,
      });
      toast('已保存');
      closeModal();
      await refreshTile(id);
      loadChips(await refreshTopbar());
    } catch (err) { toast(err.message, true); }
  };

  document.getElementById('m-fav').onclick = toggleFavorite;

  document.getElementById('m-clear-label').onclick = clearNotPortraitTag;

  document.getElementById('m-delete').onclick = deleteCurrent;

  document.getElementById('m-prev').onclick = () => navigate(-1);
  document.getElementById('m-next').onclick = () => navigate(1);

  /* 大图快捷键：← → 同组翻页，F 收藏，Delete 删除，Esc 关闭。
     光标在输入框里时不抢键，否则改标签 / 备注时按 Delete 会误删照片。 */
  document.addEventListener('keydown', e => {
    if ($modal.hidden) return;
    if (e.key === 'Escape') { closeModal(); return; }
    const tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target?.isContentEditable) return;

    if (e.key === 'ArrowLeft') { e.preventDefault(); navigate(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); navigate(1); }
    else if (e.key === 'f' || e.key === 'F') { e.preventDefault(); toggleFavorite(); }
    else if (e.key === 'r' || e.key === 'R') {
      // 只有当前这张带着「非人像」标记时才响应
      if (!document.getElementById('m-clear-label').hidden) {
        e.preventDefault();
        clearNotPortraitTag();
      }
    }
    else if (e.key === 'Delete') { e.preventDefault(); deleteCurrent(); }
  });
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
}
