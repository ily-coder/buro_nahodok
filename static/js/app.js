(() => {
  const grid = document.getElementById('items-grid');
  if (!grid) return;
  const search = document.getElementById('search');
  const chips = [...document.querySelectorAll('.filter-chip')];
  const cards = [...grid.querySelectorAll('.item-card')];
  const count = document.getElementById('results-count');
  const empty = document.getElementById('empty-state');
  const modal = document.getElementById('item-modal');
  const modalBody = document.getElementById('modal-body');
  let activeCategory = 'all';
  let items = [];
  try { items = JSON.parse(document.getElementById('items-data').textContent); } catch (_) {}

  function filter() {
    const q = (search.value || '').trim().toLocaleLowerCase('ru');
    let shown = 0;
    cards.forEach(card => {
      const match = (activeCategory === 'all' || card.dataset.category === activeCategory)
        && (!q || card.dataset.search.includes(q));
      card.hidden = !match;
      if (match) shown++;
    });
    count.textContent = `${shown} ${shown === 1 ? 'публикация' : 'публикаций'}`;
    empty.hidden = shown !== 0;
  }
  search.addEventListener('input', filter);
  chips.forEach(chip => chip.addEventListener('click', () => {
    chips.forEach(c => c.classList.toggle('active', c === chip));
    activeCategory = chip.dataset.category;
    filter();
  }));

  const categoryNames = {clothes:'Одежда',documents:'Документы',electronics:'Техника',keys:'Ключи',accessories:'Аксессуары',school:'Школьные вещи',other:'Другое'};
  function openItem(id) {
    const item = items.find(x => String(x.id) === String(id));
    if (!item) return;
    const photo = item.image ? `<img class="modal-photo" src="/uploads/${encodeURIComponent(item.image)}" alt="">` : '';
    modalBody.innerHTML = `${photo}<div class="modal-text"><span class="category-tag">${categoryNames[item.category] || 'Другое'}</span><h2 id="modal-title">${escapeHtml(item.title)}</h2><div class="modal-date">${(item.created_at || '').slice(0,16).replace('T',' ')}</div><p>${escapeHtml(item.description || 'Информация о находке уточняется.')}</p>${item.resolved ? '<span class="category-tag">Вещь уже возвращена владельцу</span>' : ''}</div>`;
    modal.classList.add('open'); modal.setAttribute('aria-hidden','false'); document.body.style.overflow='hidden';
  }
  function close() { modal.classList.remove('open'); modal.setAttribute('aria-hidden','true'); document.body.style.overflow=''; }
  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  grid.addEventListener('click', e => { const button=e.target.closest('[data-open]'); if(button) openItem(button.dataset.open); });
  modal.addEventListener('click', e => { if(e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', e => { if(e.key==='Escape') close(); });

  // Refresh the public feed periodically; only reload when the server has new or changed records.
  async function refresh() {
    try {
      const response = await fetch('/api/items', {cache:'no-store'});
      if (!response.ok) return;
      const latest = await response.json();
      const signature = JSON.stringify(latest.map(x => [x.id,x.created_at,x.resolved,x.image]));
      const current = JSON.stringify(items.map(x => [x.id,x.created_at,x.resolved,x.image]));
      if (signature !== current) location.reload();
    } catch (_) {}
  }
  window.setInterval(refresh, 30000);
})();