/* ==========================================================================
   Personal Expense Tracking Assistant - frontend logic
   Talks to the FastAPI backend (same origin) for all data + the chatbot.
   ========================================================================== */

const API = window.location.origin;
const money = (n) => `₹ ${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

const TOKEN = localStorage.getItem('ledger_token');
if (!TOKEN) { window.location.replace('login.html'); }
const USERNAME = localStorage.getItem('ledger_username') || 'there';

// Every request goes through here so the token is always attached and an
// expired/invalid session bounces back to the login page automatically.
async function apiFetch(path, options = {}) {
  const headers = Object.assign({}, options.headers, { Authorization: `Bearer ${TOKEN}` });
  const res = await fetch(`${API}${path}`, Object.assign({}, options, { headers }));
  if (res.status === 401) {
    localStorage.removeItem('ledger_token');
    localStorage.removeItem('ledger_username');
    window.location.href = 'login.html';
    throw new Error('Not authenticated');
  }
  return res;
}

let dailyChart, categoryChart;
let ledgerExpanded = false;

/* ---------------------------------------------------------------- init -- */
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('welcome-heading').innerHTML = `Welcome, ${escapeHtml(USERNAME)} <span class="wave">👋</span>`;
  document.querySelector('.profile-chip span').textContent = USERNAME;
  document.querySelector('.avatar').textContent = USERNAME.charAt(0).toUpperCase();

  wireThemeToggle();
  wireChatWidget();
  wireChatHistory();
  loadSummary();
  loadDailyChart();
  loadCategoryChart();
  loadTransactions();
  wireNav();
  wireModal();
  wireChat();
  wireQuickActions();
  wireNotifications();
  wirePortfolio();
  wirePlanner();
  wireProfile();
  wireImportExport();
  checkPlannerAlerts(true);
});

function nowTime() {
  return new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

function showToast(text) {
  const t = document.getElementById('toast');
  t.textContent = text;
  t.classList.add('show');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => t.classList.remove('show'), 2400);
}

/* ------------------------------------------------------------- summary -- */
async function loadSummary() {
  try {
    const res = await apiFetch('/api/summary');
    const d = await res.json();

    document.getElementById('stat-income').textContent = money(d.total_income);
    document.getElementById('stat-expenses').textContent = money(d.total_expenses);
    document.getElementById('stat-balance').textContent = money(d.balance);
    document.getElementById('stat-today').textContent = money(d.today_expenses);
    document.getElementById('stat-today-count').textContent = `${d.today_count} transaction${d.today_count === 1 ? '' : 's'}`;

    setChange('stat-income-change', d.income_change_pct, true);
    setChange('stat-expenses-change', d.expense_change_pct, false);
    setChange('stat-balance-change', d.balance_change_pct, true);

    if (d.today) {
      const dt = new Date(d.today + 'T00:00:00');
      const label = dt.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
      document.querySelector('#today-pill span').textContent = label;
    }
  } catch (e) {
    console.error('Failed to load summary', e);
    showToast('Could not reach the backend. Is the FastAPI server running?');
  }
}

function setChange(id, pct, higherIsGood) {
  const el = document.getElementById(id);
  const positive = pct >= 0;
  const good = higherIsGood ? positive : !positive;
  const arrow = positive ? '↑' : '↓';
  el.textContent = `${arrow} ${Math.abs(pct)}% than last month`;
  el.classList.toggle('up', good);
  el.classList.toggle('down', !good);
}

/* -------------------------------------------------------- theme toggle -- */
// Reads/writes the data-theme attribute on <html> ("day" or "night") and
// keeps chart colors (set via JS, not CSS) in sync when it changes.
function chartColors() {
  const day = document.documentElement.getAttribute('data-theme') === 'day';
  return {
    tick: day ? '#636d92' : '#8993b8',
    grid: day ? 'rgba(20,30,60,.08)' : 'rgba(255,255,255,.07)',
    donutBorder: day ? '#ffffff' : '#0f1730',
  };
}

/* -------------------------------------------------------- chat widget -- */
// Yosra AI starts collapsed to a floating icon; clicking it opens the full
// chat panel, and the × (or clicking the icon again) collapses it back.
function wireChatWidget() {
  const widget = document.getElementById('yosra-widget');
  const fab = document.getElementById('yosra-fab');
  const closeBtn = document.getElementById('yosra-close');

  fab.addEventListener('click', () => setChatWidgetOpen(!widget.classList.contains('open')));
  closeBtn.addEventListener('click', () => setChatWidgetOpen(false));
}

function setChatWidgetOpen(open) {
  const widget = document.getElementById('yosra-widget');
  const fab = document.getElementById('yosra-fab');
  widget.classList.toggle('open', open);
  fab.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) {
    document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
    document.getElementById('chat-input').focus();
  }
}

/* -------------------------------------------------- chat history sidebar -- */
// Every conversation with Yosra AI is saved (per logged-in user, in this
// browser) as a "thread" — like the chat history list on the left of
// Claude's own apps. Threads persist in localStorage so switching between
// past conversations, or starting a fresh one, doesn't lose anything.
function threadsKey() { return `yosra_threads_${USERNAME}`; }
function activeThreadKey() { return `yosra_active_${USERNAME}`; }
function newThreadId() { return `t_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`; }

function getThreads() {
  try { return JSON.parse(localStorage.getItem(threadsKey())) || []; } catch { return []; }
}
function setThreads(threads) { localStorage.setItem(threadsKey(), JSON.stringify(threads)); }
function getActiveThreadId() { return localStorage.getItem(activeThreadKey()); }
function setActiveThreadId(id) { localStorage.setItem(activeThreadKey(), id); }

function ensureActiveThread() {
  let threads = getThreads();
  let thread = threads.find(t => t.id === getActiveThreadId());
  if (thread) return thread;
  if (threads.length) {
    setActiveThreadId(threads[0].id);
    return threads[0];
  }
  thread = { id: newThreadId(), title: 'New chat', updatedAt: Date.now(), messages: [] };
  setThreads([thread]);
  setActiveThreadId(thread.id);
  return thread;
}

function persistMessage(role, html, time) {
  const threads = getThreads();
  const idx = threads.findIndex(t => t.id === getActiveThreadId());
  if (idx === -1) return;
  threads[idx].messages.push({ role, html, time });
  threads[idx].updatedAt = Date.now();
  if (role === 'user' && (!threads[idx].title || threads[idx].title === 'New chat')) {
    const plain = html.replace(/<[^>]+>/g, '');
    threads[idx].title = plain.length > 42 ? `${plain.slice(0, 42)}…` : plain;
  }
  const [moved] = threads.splice(idx, 1);
  threads.unshift(moved);
  setThreads(threads);
}

function relativeTime(ts) {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  return new Date(ts).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

function renderActiveThread() {
  const thread = ensureActiveThread();
  const wrap = document.getElementById('chat-messages');
  wrap.innerHTML = '';
  if (thread.messages.length === 0) {
    appendMessage('bot', `Hi, I'm <strong>Yosra AI</strong> 👋 — your personal spending assistant.`);
  } else {
    thread.messages.forEach(m => appendMessage(m.role, m.html, { persist: false, time: m.time }));
  }
}

function renderHistoryList() {
  const list = document.getElementById('ai-history-list');
  if (!list) return;
  const threads = getThreads();
  const activeId = getActiveThreadId();
  if (!threads.length) {
    list.innerHTML = `<p class="ai-history-empty">No conversations yet</p>`;
    return;
  }
  list.innerHTML = threads.map(t => `
    <div class="ai-history-item${t.id === activeId ? ' active' : ''}" data-thread-id="${t.id}">
      <button type="button" class="ai-history-open">
        <span class="ai-history-item-title">${escapeHtml(t.title || 'New chat')}</span>
        <span class="ai-history-item-time">${relativeTime(t.updatedAt)}</span>
      </button>
      <button type="button" class="ai-history-del" aria-label="Delete chat" title="Delete chat">✕</button>
    </div>
  `).join('');
  list.querySelectorAll('.ai-history-item').forEach(row => {
    const id = row.dataset.threadId;
    row.querySelector('.ai-history-open').addEventListener('click', () => {
      setActiveThreadId(id);
      renderActiveThread();
      renderHistoryList();
    });
    row.querySelector('.ai-history-del').addEventListener('click', () => deleteThread(id));
  });
}

function deleteThread(id) {
  const threads = getThreads();
  const thread = threads.find(t => t.id === id);
  if (!thread) return;
  const hasUserMessages = thread.messages.some(m => m.role === 'user');
  if (hasUserMessages && !window.confirm('Delete this chat?')) return;

  const remaining = threads.filter(t => t.id !== id);
  setThreads(remaining);
  if (getActiveThreadId() === id) {
    if (remaining.length) setActiveThreadId(remaining[0].id);
    else localStorage.removeItem(activeThreadKey());
    renderActiveThread();   // opens the next chat, or a fresh one if none are left
  }
  renderHistoryList();
}

function startNewThread() {
  const threads = getThreads();
  // Already on an empty chat? Just reuse it instead of piling up blank "New chat" rows.
  const active = threads.find(t => t.id === getActiveThreadId());
  if (active && !active.messages.some(m => m.role === 'user')) {
    document.getElementById('chat-input').focus();
    return;
  }
  const thread = { id: newThreadId(), title: 'New chat', updatedAt: Date.now(), messages: [] };
  threads.unshift(thread);
  setThreads(threads);
  setActiveThreadId(thread.id);
  renderActiveThread();
  renderHistoryList();
  document.getElementById('chat-input').focus();
}

function wireChatHistory() {
  const panel = document.getElementById('ai-panel');
  const toggle = document.getElementById('ai-history-toggle');
  const newBtn = document.getElementById('ai-history-new');

  // The chat list is shown on the left by default on desktop; the header
  // button still collapses it. (On phones it stays an overlay, opened on demand.)
  const saved = localStorage.getItem('yosra_history_open');
  const openByDefault = saved === null ? window.innerWidth > 520 : saved === '1';
  panel.classList.toggle('history-open', openByDefault);
  toggle.classList.toggle('active', openByDefault);

  toggle.addEventListener('click', () => {
    const open = panel.classList.toggle('history-open');
    toggle.classList.toggle('active', open);
    localStorage.setItem('yosra_history_open', open ? '1' : '0');
    if (open) renderHistoryList();
  });
  newBtn.addEventListener('click', startNewThread);

  renderActiveThread();
  renderHistoryList();
}

function wireThemeToggle() {
  const root = document.documentElement;
  const btns = document.querySelectorAll('.theme-btn');
  const saved = localStorage.getItem('ledger_theme') || 'night';
  root.setAttribute('data-theme', saved);
  btns.forEach(b => b.classList.toggle('active', b.dataset.themeBtn === saved));

  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      const t = btn.dataset.themeBtn;
      root.setAttribute('data-theme', t);
      localStorage.setItem('ledger_theme', t);
      btns.forEach(b => b.classList.toggle('active', b === btn));
      // charts draw their gridlines/ticks in JS, so redraw with the new palette
      loadDailyChart();
      loadCategoryChart();
    });
  });
}

/* --------------------------------------------------------- daily chart -- */
async function loadDailyChart() {
  try {
    const res = await apiFetch('/api/daily-expenses');
    const rows = await res.json();
    const colors = chartColors();

    const ctx = document.getElementById('dailyChart').getContext('2d');
    const data = {
      labels: rows.map(r => r.label),
      datasets: [
        { label: 'Income', data: rows.map(r => r.income), backgroundColor: '#2fe3c4', borderRadius: 6, maxBarThickness: 22 },
        { label: 'Expenses', data: rows.map(r => r.expenses), backgroundColor: '#ff5d81', borderRadius: 6, maxBarThickness: 22 },
      ],
    };
    if (dailyChart) dailyChart.destroy();
    dailyChart = new Chart(ctx, {
      type: 'bar',
      data,
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${money(c.raw)}` } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: colors.tick, font: { size: 11 } } },
          y: { grid: { color: colors.grid }, ticks: { color: colors.tick, font: { size: 11 }, callback: (v) => `₹${v}` } },
        },
      },
    });
  } catch (e) {
    console.error('Failed to load daily chart', e);
  }
}

/* ------------------------------------------------------ category chart -- */
async function loadCategoryChart() {
  try {
    const res = await apiFetch('/api/category-expenses');
    const d = await res.json();

    document.getElementById('donut-total').textContent = money(d.total);

    const ctx = document.getElementById('categoryChart').getContext('2d');
    if (categoryChart) categoryChart.destroy();
    categoryChart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: d.categories.map(c => c.category),
        datasets: [{
          data: d.categories.map(c => c.amount),
          backgroundColor: d.categories.map(c => c.color),
          borderWidth: 3,
          borderColor: chartColors().donutBorder,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '72%',
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (c) => ` ${c.label}: ${money(c.raw)}` } },
        },
      },
    });

    const legend = document.getElementById('cat-legend');
    legend.innerHTML = d.categories.map(c => `
      <li>
        <i class="dot-legend" style="background:${c.color}"></i>
        <span class="cat-name">${c.category}</span>
        <span class="cat-pct">${c.percent}%</span>
        <span class="cat-amt">₹${Math.round(c.amount).toLocaleString('en-IN')}</span>
      </li>
    `).join('');
  } catch (e) {
    console.error('Failed to load category chart', e);
  }
}

/* -------------------------------------------------------- transactions -- */
async function loadTransactions() {
  try {
    const limit = ledgerExpanded ? 1000 : 7;
    const res = await apiFetch(`/api/transactions?limit=${limit}`);
    const rows = await res.json();

    const catClass = {
      Food: 'icon-amber', Transport: 'icon-blue', Shopping: 'icon-purple',
      Bills: 'icon-green', Entertainment: 'icon-red', Others: 'icon-amber', Income: 'icon-green',
    };

    document.getElementById('tx-body').innerHTML = rows.map(r => {
      const dt = new Date(r.date + 'T00:00:00');
      const dateLabel = dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
      const sign = r.type === 'Credit' ? '+' : '-';
      const amtClass = r.type === 'Credit' ? 'amt-credit' : 'amt-debit';
      return `
        <tr>
          <td>${dateLabel}</td>
          <td>${escapeHtml(r.description)}</td>
          <td>${r.category}</td>
          <td><span class="badge ${amtClass === 'amt-credit' ? 'icon-green' : 'icon-red'}">${r.type}</span></td>
          <td class="right ${amtClass}">${sign} ${Math.round(r.amount).toLocaleString('en-IN')}</td>
          <td class="right">${Math.round(r.balance).toLocaleString('en-IN')}</td>
          <td class="right"><button class="delete-btn" data-id="${r.id}" title="Delete this transaction">🗑️</button></td>
        </tr>`;
    }).join('');

    const total = rows.reduce((sum, r) => sum + (r.type === 'Credit' ? r.amount : -r.amount), 0);
    const totalEl = document.getElementById('tx-total');
    totalEl.textContent = `${total < 0 ? '-' : '+'} ${Math.abs(Math.round(total)).toLocaleString('en-IN')}`;
    totalEl.style.color = total < 0 ? 'var(--red)' : 'var(--green)';

    document.querySelectorAll('.delete-btn').forEach(btn => {
      btn.addEventListener('click', () => deleteExpense(btn.dataset.id));
    });
  } catch (e) {
    console.error('Failed to load transactions', e);
  }
}

async function deleteExpense(id) {
  if (!confirm('Delete this transaction? This cannot be undone.')) return;
  try {
    const res = await apiFetch(`/api/expenses/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Delete failed');
    showToast('Transaction deleted.');
    loadSummary(); loadDailyChart(); loadCategoryChart(); loadTransactions(); checkPlannerAlerts();
  } catch (e) {
    showToast('Could not delete the transaction.');
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

/* ------------------------------------------------------------- sidebar -- */
function wireNav() {
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const view = item.dataset.view;
      const action = item.dataset.action;

      if (view) {
        document.querySelectorAll('.nav-item[data-view]').forEach(n => n.classList.remove('active'));
        item.classList.add('active');
      }
      if (view === 'ledger') toggleLedgerView(true);
      if (view === 'dashboard') toggleLedgerView(false);
      if (action === 'add-expense') openModal();
      if (action === 'scroll-charts') document.getElementById('charts-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
      if (action === 'focus-chat') setChatWidgetOpen(true);
      if (action === 'show-profile') openProfile();
      if (action === 'toast-settings') showToast('App settings are coming soon.');
      if (action === 'show-portfolio') openPortfolio();
      if (action === 'show-planner') openPlanner();
      if (action === 'logout') logout();
    });
  });

  document.getElementById('view-all-btn').addEventListener('click', () => {
    toggleLedgerView(!ledgerExpanded);
  });
}

async function logout() {
  try {
    await apiFetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {
    // ignore - we're clearing local state either way
  }
  localStorage.removeItem('ledger_token');
  localStorage.removeItem('ledger_username');
  window.location.href = 'login.html';
}

function toggleLedgerView(expand) {
  ledgerExpanded = expand;
  document.getElementById('ledger-title').textContent = expand ? 'All Transactions (Ledger)' : 'Recent Transactions (Ledger)';
  document.getElementById('view-all-btn').textContent = expand ? '← Show Recent' : 'View All →';
  loadTransactions();
}

/* --------------------------------------------------------------- modal -- */
function wireModal() {
  document.getElementById('qa-add').addEventListener('click', openModal);
  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('modal-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'modal-overlay') closeModal();
  });

  // Keep Category and Type in sync so "Income" can't be paired with a
  // Debit transaction (or an expense category paired with Credit) - that
  // combination produced a mis-colored, mis-summed slice in the category
  // chart since income isn't counted in the Debit-only breakdown.
  const typeSelect = document.getElementById('f-type');
  const categorySelect = document.getElementById('f-category');
  const incomeOption = document.getElementById('f-category-income');
  typeSelect.addEventListener('change', () => {
    if (typeSelect.value === 'Credit') {
      incomeOption.hidden = false;
      categorySelect.value = 'Income';
      categorySelect.disabled = true;
    } else {
      categorySelect.disabled = false;
      incomeOption.hidden = true;
      if (categorySelect.value === 'Income') categorySelect.value = 'Food';
    }
  });
  incomeOption.hidden = true; // Debit is the default selected type on open

  wireSmartEntry();

  document.getElementById('expense-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      description: document.getElementById('f-description').value.trim(),
      category: document.getElementById('f-category').value,
      type: document.getElementById('f-type').value,
      amount: parseFloat(document.getElementById('f-amount').value),
      date: document.getElementById('f-date').value || undefined,
    };
    try {
      const res = await apiFetch('/api/expenses', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error('Request failed');
      closeModal();
      document.getElementById('expense-form').reset();
      setReviewHint('');
      document.getElementById('scan-status').textContent = '';
      showToast('Transaction saved.');
      addNotification(
        `${payload.type === 'Credit' ? 'Income' : 'Expense'} added: ${money(payload.amount)} · ${payload.category}`
      );
      loadSummary(); loadDailyChart(); loadCategoryChart(); loadTransactions(); checkPlannerAlerts();
    } catch (err) {
      showToast('Could not save the transaction.');
    }
  });
}

/* ------------------------------------------- smart entry (rules + OCR) -- */
function setReviewHint(msg) {
  const el = document.getElementById('review-hint');
  el.textContent = msg ? `⚠ ${msg}` : '';
  el.hidden = !msg;
}

function setCategoryIfAllowed(category) {
  const sel = document.getElementById('f-category');
  if (document.getElementById('f-type').value === 'Credit') return;
  if ([...sel.options].some((o) => o.value === category || o.text === category)) sel.value = category;
}

function wireSmartEntry() {
  const desc = document.getElementById('f-description');
  const amount = document.getElementById('f-amount');
  let timer;

  // Rule-based category suggestion while typing (user can still change it).
  const suggest = async () => {
    const text = desc.value.trim();
    if (text.length < 2) { setReviewHint(''); return; }
    try {
      const res = await apiFetch('/api/categorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: text, amount: parseFloat(amount.value) || null }),
      });
      if (!res.ok) return;
      const r = await res.json();
      if (r.matched) setCategoryIfAllowed(r.category);
      setReviewHint(r.review ? r.reason : '');
    } catch (_) { /* suggestion is optional - ignore failures */ }
  };
  const later = () => { clearTimeout(timer); timer = setTimeout(suggest, 350); };
  desc.addEventListener('input', later);
  amount.addEventListener('input', later);

  // Receipt photo -> OCR -> pre-filled form; the user checks it and presses Save.
  const btn = document.getElementById('scan-btn');
  const input = document.getElementById('scan-input');
  const status = document.getElementById('scan-status');
  btn.addEventListener('click', () => input.click());
  input.addEventListener('change', async () => {
    const file = input.files[0];
    if (!file) return;
    status.textContent = 'Reading receipt…';
    btn.disabled = true;
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await apiFetch('/api/receipt/scan', { method: 'POST', body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Could not read the receipt.');
      if (data.shop) desc.value = data.shop;
      if (data.amount != null) amount.value = data.amount;
      if (data.date) document.getElementById('f-date').value = data.date;
      document.getElementById('f-type').value = 'Debit';
      document.getElementById('f-type').dispatchEvent(new Event('change'));
      setCategoryIfAllowed(data.category);
      setReviewHint(data.review ? data.reason : '');
      status.textContent = 'Done - please check the details, then save.';
    } catch (err) {
      status.textContent = err.message;
    } finally {
      btn.disabled = false;
      input.value = '';
    }
  });
}

function openModal() {
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('f-description').focus();
}
function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
}

/* ------------------------------------------------------- notifications -- */
let notifications = [];

function wireNotifications() {
  const btn = document.getElementById('notif-btn');
  const panel = document.getElementById('notif-panel');
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) markNotificationsRead();
  });
  document.addEventListener('click', (e) => {
    if (panel.classList.contains('open') && !panel.contains(e.target) && e.target !== btn) {
      panel.classList.remove('open');
    }
  });
}

function addNotification(text) {
  notifications.unshift({ text, time: nowTime(), unread: true });
  notifications = notifications.slice(0, 20);
  renderNotifications();
}

function renderNotifications() {
  const list = document.getElementById('notif-list');
  const dot = document.getElementById('notif-dot');
  if (!notifications.length) {
    list.innerHTML = '<li class="notif-empty">No notifications yet.</li>';
  } else {
    list.innerHTML = notifications.map(n => `
      <li class="notif-item">
        <span>${escapeHtml(n.text)}</span>
        <span class="notif-time">${n.time}</span>
      </li>
    `).join('');
  }
  dot.hidden = !notifications.some(n => n.unread);
}

function markNotificationsRead() {
  notifications.forEach(n => n.unread = false);
  document.getElementById('notif-dot').hidden = true;
}

/* ----------------------------------------------------------- portfolio -- */
function wirePortfolio() {
  document.getElementById('portfolio-close').addEventListener('click', closePortfolio);
  document.getElementById('portfolio-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'portfolio-overlay') closePortfolio();
  });
}

async function openPortfolio() {
  document.getElementById('portfolio-overlay').classList.add('open');
  try {
    const [summaryRes, catRes] = await Promise.all([
      apiFetch('/api/portfolio'),
      apiFetch('/api/category-expenses'),
    ]);
    const summary = await summaryRes.json();
    const cats = await catRes.json();

    document.getElementById('pf-balance').textContent = money(summary.balance);
    document.getElementById('pf-income').textContent = money(summary.total_income);
    document.getElementById('pf-expenses').textContent = money(summary.total_expenses);

    const list = document.getElementById('pf-categories');
    if (!cats.categories.length) {
      list.innerHTML = '<li class="portfolio-empty">No expenses recorded yet.</li>';
    } else {
      list.innerHTML = cats.categories.map(c => `
        <li>
          <i class="dot-legend" style="background:${c.color}"></i>
          <span class="cat-name">${c.category}</span>
          <span class="cat-pct">${c.percent}%</span>
          <span class="cat-amt">₹${Math.round(c.amount).toLocaleString('en-IN')}</span>
        </li>
      `).join('');
    }
  } catch (e) {
    console.error('Failed to load portfolio', e);
    showToast('Could not load portfolio details.');
  }
}

function closePortfolio() {
  document.getElementById('portfolio-overlay').classList.remove('open');
}

/* ------------------------------------------------------------- planner -- */
// Budgets, savings goals, and recurring bills all live in one modal with
// three tabs, mirroring the Portfolio/Profile modal pattern already used
// elsewhere in the app.
const PLANNER_CATEGORIES = ['Food', 'Transport', 'Shopping', 'Bills', 'Entertainment', 'Others'];

function wirePlanner() {
  document.getElementById('planner-close').addEventListener('click', closePlanner);
  document.getElementById('planner-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'planner-overlay') closePlanner();
  });

  document.querySelectorAll('.planner-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.planner-tab').forEach(t => t.classList.toggle('active', t === tab));
      document.querySelectorAll('.planner-pane').forEach(p => p.classList.remove('active'));
      document.getElementById(`planner-pane-${tab.dataset.tab}`).classList.add('active');
    });
  });

  const catSelect = document.getElementById('bill-category');
  catSelect.innerHTML = PLANNER_CATEGORIES.map(c => `<option>${c}</option>`).join('');

  document.getElementById('goal-add-toggle').addEventListener('click', () => {
    document.getElementById('goal-form').hidden = !document.getElementById('goal-form').hidden;
  });
  document.getElementById('bill-add-toggle').addEventListener('click', () => {
    document.getElementById('bill-form').hidden = !document.getElementById('bill-form').hidden;
  });

  document.getElementById('goal-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('goal-name').value.trim();
    const target_amount = parseFloat(document.getElementById('goal-target').value);
    const target_date = document.getElementById('goal-date').value || null;
    try {
      await apiFetch('/api/goals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, target_amount, target_date }),
      });
      e.target.reset();
      document.getElementById('goal-form').hidden = true;
      loadGoals();
      showToast('Savings goal created.');
    } catch {
      showToast('Could not create goal.');
    }
  });

  document.getElementById('bill-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('bill-name').value.trim();
    const amount = parseFloat(document.getElementById('bill-amount').value);
    const category = document.getElementById('bill-category').value;
    const due_day = parseInt(document.getElementById('bill-due-day').value, 10);
    try {
      await apiFetch('/api/bills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, amount, category, due_day }),
      });
      e.target.reset();
      document.getElementById('bill-form').hidden = true;
      loadBills();
      checkPlannerAlerts();
      showToast('Recurring bill added.');
    } catch {
      showToast('Could not add bill.');
    }
  });
}

function openPlanner() {
  document.getElementById('planner-overlay').classList.add('open');
  loadBudgets();
  loadGoals();
  loadBills();
}
function closePlanner() {
  document.getElementById('planner-overlay').classList.remove('open');
}

/* -- budgets -- */
async function loadBudgets() {
  const list = document.getElementById('budget-list');
  try {
    const res = await apiFetch('/api/budgets');
    const rows = await res.json();
    const overBudget = rows.filter(r => r.status === 'over');
    const alertEl = document.getElementById('budget-alert');
    if (overBudget.length) {
      alertEl.hidden = false;
      alertEl.textContent = `⚠️ Over budget on ${overBudget.map(r => r.category).join(', ')} this month.`;
    } else {
      alertEl.hidden = true;
    }

    list.innerHTML = rows.map(r => `
      <div class="budget-row" data-category="${r.category}">
        <div class="budget-row-head">
          <span class="cat"><i class="dot-legend" style="background:${r.color}"></i>${r.category}</span>
          <span class="budget-row-figs"><strong>₹${r.spent.toLocaleString('en-IN')}</strong>${r.monthly_limit ? ` / ₹${r.monthly_limit.toLocaleString('en-IN')}` : ' spent this month'}</span>
        </div>
        ${r.monthly_limit ? `<div class="pbar"><div class="pbar-fill ${r.status}" style="width:${Math.min(r.percent, 100)}%"></div></div>` : ''}
        <div class="budget-set">
          <input type="number" min="1" step="0.01" placeholder="${r.monthly_limit ? 'Update limit (₹)' : 'Set monthly limit (₹)'}" class="budget-input">
          <button type="button" class="budget-save-btn">Save</button>
          ${r.monthly_limit ? '<button type="button" class="clear-btn budget-clear-btn">Clear</button>' : ''}
        </div>
      </div>
    `).join('');

    list.querySelectorAll('.budget-row').forEach(row => {
      const category = row.dataset.category;
      row.querySelector('.budget-save-btn').addEventListener('click', async () => {
        const input = row.querySelector('.budget-input');
        const val = parseFloat(input.value);
        if (!val || val <= 0) return;
        await apiFetch('/api/budgets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category, monthly_limit: val }),
        });
        loadBudgets();
        checkPlannerAlerts();
        showToast(`${category} budget saved.`);
      });
      const clearBtn = row.querySelector('.budget-clear-btn');
      if (clearBtn) {
        clearBtn.addEventListener('click', async () => {
          await apiFetch(`/api/budgets/${encodeURIComponent(category)}`, { method: 'DELETE' });
          loadBudgets();
          checkPlannerAlerts();
        });
      }
    });
  } catch {
    list.innerHTML = '<p class="planner-empty">Could not load budgets.</p>';
  }
}

/* -- goals -- */
async function loadGoals() {
  const list = document.getElementById('goal-list');
  try {
    const res = await apiFetch('/api/goals');
    const rows = await res.json();
    if (!rows.length) {
      list.innerHTML = '<p class="planner-empty">No savings goals yet — add one below.</p>';
      return;
    }
    list.innerHTML = rows.map(g => `
      <div class="goal-row" data-id="${g.id}">
        <div class="goal-row-head">
          <span class="name">${escapeHtml(g.name)}</span>
          <span class="goal-row-figs"><strong>₹${g.saved_amount.toLocaleString('en-IN')}</strong> / ₹${g.target_amount.toLocaleString('en-IN')}</span>
        </div>
        <div class="pbar"><div class="pbar-fill ${g.percent >= 100 ? 'ok' : ''}" style="width:${g.percent}%"></div></div>
        ${g.target_date ? `<p class="goal-target-note">Target: ${g.target_date}</p>` : ''}
        <div class="goal-row-actions">
          <input type="number" min="1" step="0.01" placeholder="Add ₹" class="goal-add-input">
          <button type="button" class="goal-add-btn">Add</button>
          <button type="button" class="delete-btn goal-delete-btn" aria-label="Delete goal">🗑</button>
        </div>
      </div>
    `).join('');

    list.querySelectorAll('.goal-row').forEach(row => {
      const id = row.dataset.id;
      row.querySelector('.goal-add-btn').addEventListener('click', async () => {
        const input = row.querySelector('.goal-add-input');
        const val = parseFloat(input.value);
        if (!val || val <= 0) return;
        await apiFetch(`/api/goals/${id}/contribute`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: val }),
        });
        loadGoals();
        showToast('Added to your goal.');
      });
      row.querySelector('.goal-delete-btn').addEventListener('click', async () => {
        await apiFetch(`/api/goals/${id}`, { method: 'DELETE' });
        loadGoals();
      });
    });
  } catch {
    list.innerHTML = '<p class="planner-empty">Could not load goals.</p>';
  }
}

/* -- bills -- */
function billDueTag(daysUntil) {
  if (daysUntil <= 2) return { cls: 'soon', label: daysUntil <= 0 ? 'Due now' : `${daysUntil}d left` };
  if (daysUntil <= 7) return { cls: 'upcoming', label: `${daysUntil}d left` };
  return { cls: 'later', label: `${daysUntil}d left` };
}

async function loadBills() {
  const list = document.getElementById('bill-list');
  try {
    const res = await apiFetch('/api/bills');
    const rows = await res.json();
    if (!rows.length) {
      list.innerHTML = '<p class="planner-empty">No recurring bills yet — add one below.</p>';
      return;
    }
    list.innerHTML = rows.map(b => {
      const tag = billDueTag(b.days_until);
      return `
        <div class="bill-row" data-id="${b.id}">
          <div>
            <div class="bill-row-name">${escapeHtml(b.name)}</div>
            <div class="bill-row-meta">${b.category} · ₹${b.amount.toLocaleString('en-IN')} · due day ${b.due_day}</div>
          </div>
          <div class="bill-row-actions">
            <span class="bill-due-tag ${tag.cls}">${tag.label}</span>
            <button type="button" class="bill-pay-btn">Mark paid</button>
            <button type="button" class="delete-btn bill-delete-btn" aria-label="Delete bill">🗑</button>
          </div>
        </div>
      `;
    }).join('');

    list.querySelectorAll('.bill-row').forEach(row => {
      const id = row.dataset.id;
      row.querySelector('.bill-pay-btn').addEventListener('click', async () => {
        await apiFetch(`/api/bills/${id}/pay`, { method: 'POST' });
        loadBills();
        checkPlannerAlerts();
        loadSummary();
        loadDailyChart();
        loadCategoryChart();
        loadTransactions();
        showToast('Bill marked as paid and logged to your ledger.');
      });
      row.querySelector('.bill-delete-btn').addEventListener('click', async () => {
        await apiFetch(`/api/bills/${id}`, { method: 'DELETE' });
        loadBills();
        checkPlannerAlerts();
      });
    });
  } catch {
    list.innerHTML = '<p class="planner-empty">Could not load bills.</p>';
  }
}

/* -- dashboard banners + notification bell (budgets + bills) -- */
// Runs on every dashboard load (fresh login OR reopening the app while
// already signed in - both land on the dashboard), and again whenever
// something that could change the answer happens (expense added, budget
// saved, bill paid). `notify` is true only on the first run so the bell
// doesn't collect duplicate entries.
async function checkPlannerAlerts(notify = false) {
  const box = document.getElementById('dashboard-alerts');
  try {
    const [budgetsRes, billsRes] = await Promise.all([apiFetch('/api/budgets'), apiFetch('/api/bills')]);
    const budgets = await budgetsRes.json();
    const bills = await billsRes.json();
    const money = (n) => `₹${n.toLocaleString('en-IN')}`;

    const banners = [];
    budgets.filter(b => b.status === 'over').forEach(b => {
      banners.push({
        tone: 'rose',
        text: `⚠️ Over budget on ${b.category} this month — ${money(b.spent)} spent of your ${money(b.monthly_limit)} limit.`,
      });
    });
    bills.filter(b => b.days_until <= 3).forEach(b => {
      const when = b.days_until <= 0 ? 'due today' : `due in ${b.days_until} day${b.days_until === 1 ? '' : 's'}`;
      banners.push({ tone: 'amber', text: `📅 ${b.name} (${money(b.amount)}) is ${when}.` });
    });

    box.innerHTML = banners.map((a, i) => `
      <div class="dash-alert ${a.tone === 'amber' ? 'amber' : ''}" data-i="${i}">
        <span class="dash-alert-text">${escapeHtml(a.text)}</span>
        <button type="button" class="dash-alert-link" data-open-planner>View</button>
        <button type="button" class="dash-alert-close" aria-label="Dismiss">✕</button>
      </div>
    `).join('');
    box.querySelectorAll('[data-open-planner]').forEach(btn => btn.addEventListener('click', openPlanner));
    box.querySelectorAll('.dash-alert-close').forEach(btn => {
      btn.addEventListener('click', () => btn.closest('.dash-alert').remove());
    });

    if (notify) {
      budgets.filter(b => b.status === 'over').forEach(b => {
        addNotification(`⚠️ You're over your ${b.category} budget (${money(b.spent)} of ${money(b.monthly_limit)}).`);
      });
      budgets.filter(b => b.status === 'warning').forEach(b => {
        addNotification(`You've used ${b.percent}% of your ${b.category} budget this month.`);
      });
      bills.filter(b => b.days_until <= 3).forEach(b => {
        const when = b.days_until <= 0 ? 'due today' : `due in ${b.days_until} day${b.days_until === 1 ? '' : 's'}`;
        addNotification(`📅 ${b.name} (${money(b.amount)}) is ${when}.`);
      });
    }
  } catch {
    /* silent - alerts are a nice-to-have, not critical path */
  }
}

/* ---------------------------------------------------------- quick acts -- */
function wireQuickActions() {
  document.getElementById('qa-charts').addEventListener('click', () =>
    document.getElementById('charts-section').scrollIntoView({ behavior: 'smooth' }));
  document.getElementById('qa-chat').addEventListener('click', () => setChatWidgetOpen(true));
  document.getElementById('qa-export').addEventListener('click', openImportExport);
}

async function exportCsv() {
  try {
    const res = await apiFetch('/api/transactions?limit=5000');
    const rows = await res.json();
    const header = 'Date,Description,Category,Type,Amount,Balance\n';
    const body = rows.map(r => `${r.date},"${r.description.replace(/"/g, '""')}",${r.category},${r.type},${r.amount},${r.balance}`).join('\n');
    const blob = new Blob([header + body], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'ledger-export.csv';
    a.click();
    URL.revokeObjectURL(url);
    showToast('Ledger exported as CSV.');
  } catch (e) {
    showToast('Export failed.');
  }
}

/* ---------------------------------------------------- import / export -- */
let pendingImportRows = [];

function wireImportExport() {
  document.getElementById('io-close').addEventListener('click', closeImportExport);
  document.getElementById('io-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'io-overlay') closeImportExport();
  });
  document.getElementById('io-export-btn').addEventListener('click', exportCsv);
  document.getElementById('io-cancel-btn').addEventListener('click', resetImportPreview);
  document.getElementById('io-file-input').addEventListener('change', handleImportFile);
  document.getElementById('io-confirm-btn').addEventListener('click', confirmImport);
}

function openImportExport() {
  resetImportPreview();
  document.getElementById('io-overlay').classList.add('open');
}
function closeImportExport() {
  document.getElementById('io-overlay').classList.remove('open');
  resetImportPreview();
}
function resetImportPreview() {
  pendingImportRows = [];
  document.getElementById('io-preview').hidden = true;
  document.getElementById('io-file-input').value = '';
}

function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim() !== '');
  const parseLine = (line) => {
    const cells = [];
    let cur = '', inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (inQuotes) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (c === '"') { inQuotes = false; }
        else cur += c;
      } else if (c === '"') {
        inQuotes = true;
      } else if (c === ',') {
        cells.push(cur); cur = '';
      } else {
        cur += c;
      }
    }
    cells.push(cur);
    return cells.map(c => c.trim());
  };
  if (!lines.length) return [];
  const header = parseLine(lines[0]).map(h => h.toLowerCase());
  const col = (name) => header.indexOf(name);
  const iDate = col('date'), iDesc = col('description'), iCat = col('category'),
        iType = col('type'), iAmt = col('amount');

  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = parseLine(lines[i]);
    const amount = parseFloat(cells[iAmt]);
    if (!cells[iDesc] || !Number.isFinite(amount) || amount <= 0) continue;
    rows.push({
      date: iDate >= 0 && cells[iDate] ? cells[iDate] : null,
      description: cells[iDesc],
      category: iCat >= 0 && cells[iCat] ? cells[iCat] : 'Others',
      type: iType >= 0 && cells[iType] === 'Credit' ? 'Credit' : 'Debit',
      amount,
    });
  }
  return rows;
}

function handleImportFile(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const rows = parseCsv(reader.result);
    if (!rows.length) {
      showToast('No valid transactions found in that file.');
      resetImportPreview();
      return;
    }
    pendingImportRows = rows;
    renderImportPreview(rows);
  };
  reader.onerror = () => showToast('Could not read that file.');
  reader.readAsText(file);
}

function renderImportPreview(rows) {
  document.getElementById('io-preview').hidden = false;
  document.getElementById('io-preview-count').textContent = rows.length;
  const body = document.getElementById('io-preview-body');
  const shown = rows.slice(0, 50);
  body.innerHTML = shown.map(r => `
    <tr>
      <td>${escapeHtml(r.date || '—')}</td>
      <td>${escapeHtml(r.description)}</td>
      <td>${escapeHtml(r.category)}</td>
      <td>${r.type}</td>
      <td class="right">${money(r.amount)}</td>
    </tr>
  `).join('') + (rows.length > shown.length
    ? `<tr><td colspan="5" class="io-more-row">+ ${rows.length - shown.length} more row(s) not shown, but will be imported</td></tr>`
    : '');
}

async function confirmImport() {
  if (!pendingImportRows.length) return;
  const btn = document.getElementById('io-confirm-btn');
  btn.disabled = true;
  btn.textContent = 'Importing…';
  try {
    const res = await apiFetch('/api/expenses/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows: pendingImportRows }),
    });
    if (!res.ok) throw new Error('Import failed');
    const data = await res.json();
    showToast(`Restored ${data.inserted} transaction(s).`);
    addNotification(`Imported ${data.inserted} transaction(s) from CSV.`);
    closeImportExport();
    loadSummary(); loadDailyChart(); loadCategoryChart(); loadTransactions(); checkPlannerAlerts();
  } catch (e) {
    showToast('Import failed. Check the file and try again.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Import transactions';
  }
}

/* -------------------------------------------------------------- profile -- */
function wireProfile() {
  document.getElementById('profile-close').addEventListener('click', closeProfile);
  document.getElementById('profile-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'profile-overlay') closeProfile();
  });
}

async function openProfile() {
  document.getElementById('profile-overlay').classList.add('open');
  try {
    const res = await apiFetch('/api/auth/me');
    const data = await res.json();
    document.getElementById('profile-avatar-lg').textContent = data.username.charAt(0).toUpperCase();
    document.getElementById('profile-name').textContent = data.username;
    const since = data.created_at ? new Date(data.created_at) : null;
    document.getElementById('profile-since').textContent = since
      ? `Member since ${since.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' })}`
      : '';
    document.getElementById('profile-tx-count').textContent =
      `${data.transaction_count} transaction${data.transaction_count === 1 ? '' : 's'} recorded`;
  } catch (e) {
    showToast('Could not load profile details.');
  }
}
function closeProfile() {
  document.getElementById('profile-overlay').classList.remove('open');
}

/* ----------------------------------------------------------------- chat -- */
function wireChat() {
  document.getElementById('chat-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    sendChat(text);
  });
  wireVoiceInput();
  wireVoiceOutput();
}

/* ------------------------------------------------------------ voice I/O -- */
// Speak-to-Yosra (Web Speech API speech recognition) and read-replies-aloud
// (speech synthesis). Both are client-side only, no backend involved, and
// degrade gracefully (mic button hides itself) on browsers without support.
let _recognizing = false;

function wireVoiceInput() {
  const micBtn = document.getElementById('chat-mic-btn');
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    micBtn.hidden = true;
    return;
  }
  const recognition = new Recognition();
  recognition.lang = 'en-IN';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onresult = (e) => {
    const transcript = e.results[0][0].transcript;
    if (transcript && transcript.trim()) sendChat(transcript.trim());
  };
  recognition.onerror = () => {};
  recognition.onend = () => {
    _recognizing = false;
    micBtn.classList.remove('listening');
  };

  micBtn.addEventListener('click', () => {
    if (_recognizing) {
      recognition.stop();
      return;
    }
    try {
      recognition.start();
      _recognizing = true;
      micBtn.classList.add('listening');
    } catch {
      _recognizing = false;
    }
  });
}

function looksTamil(text) {
  return /[\u0B80-\u0BFF]/.test(text);
}

function speak(text) {
  if (!('speechSynthesis' in window)) return;
  const clean = text
    .replace(/\*\*/g, '')
    .replace(/[•]/g, '')
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, '')
    .replace(/\n+/g, '. ')
    .trim();
  if (!clean) return;
  const utter = new SpeechSynthesisUtterance(clean);
  utter.lang = looksTamil(clean) ? 'ta-IN' : 'en-IN';
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
}

function wireVoiceOutput() {
  const btn = document.getElementById('ai-voice-toggle');
  const saved = localStorage.getItem('yosra_voice_out') === '1';
  btn.classList.toggle('active', saved);
  btn.addEventListener('click', () => {
    const on = !btn.classList.contains('active');
    btn.classList.toggle('active', on);
    localStorage.setItem('yosra_voice_out', on ? '1' : '0');
    if (!on) window.speechSynthesis && window.speechSynthesis.cancel();
  });
}

async function sendChat(text) {
  appendMessage('user', escapeHtml(text));
  renderHistoryList();
  const typingId = appendTyping();
  try {
    const res = await apiFetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    removeTyping(typingId);
    appendMessage('bot', formatBotReply(data.reply));
    renderHistoryList();
    if (localStorage.getItem('yosra_voice_out') === '1') speak(data.reply);
    // Yosra AI can write to the ledger (add expense/income, set a budget)
    // as well as just answer questions - refresh the dashboard every time
    // so a chat-made change shows up immediately, without needing a reload.
    loadSummary();
    loadDailyChart();
    loadCategoryChart();
    loadTransactions();
    checkPlannerAlerts();
  } catch (e) {
    removeTyping(typingId);
    appendMessage('bot', "Sorry, I couldn't reach the server. Make sure the FastAPI backend is running.");
  }
}

function formatBotReply(text) {
  // Minimal markdown: **bold** and line breaks, so responses read like the
  // structured, easy-to-scan answers in the reference design.
  const escaped = escapeHtml(text);
  return escaped
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

function appendMessage(role, html, opts = {}) {
  const { persist = true, time } = opts;
  const wrap = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  const t = time || nowTime();
  div.innerHTML = `<div class="bubble">${html}</div><span class="msg-time">${t}</span>`;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
  if (persist) persistMessage(role, html, t);
}

function appendTyping() {
  const wrap = document.getElementById('chat-messages');
  const div = document.createElement('div');
  const id = `typing-${Date.now()}`;
  div.id = id;
  div.className = 'msg bot';
  div.innerHTML = `<div class="bubble">Thinking…</div>`;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
  return id;
}
function removeTyping(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}
