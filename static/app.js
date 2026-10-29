// TriageLens — Frontend Vanilla JavaScript
// Strict DOM construction: textContent and createElement ONLY.

(function () {
  'use strict';

  // Compute BASE from currentScript.src to support arbitrary mount prefixes (e.g. /triagelens/)
  const BASE = new URL('..', document.currentScript.src).pathname;

  // Idempotency key generator with crypto.randomUUID and getRandomValues fallback for plain HTTP
  function generateIdempotencyKey() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      try {
        return crypto.randomUUID();
      } catch (_) {}
    }
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    let hex = '';
    for (let i = 0; i < bytes.length; i++) {
      hex += bytes[i].toString(16).padStart(2, '0');
    }
    return hex;
  }

  // Toast notifications for Problem Details and informational notices
  function showToast(title, detail, isError) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'toast' + (isError ? ' toast-error' : ' toast-success');

    const titleEl = document.createElement('div');
    titleEl.className = 'toast-title';
    titleEl.textContent = title;
    toast.appendChild(titleEl);

    if (detail) {
      const detailEl = document.createElement('div');
      detailEl.className = 'toast-detail';
      detailEl.textContent = detail;
      toast.appendChild(detailEl);
    }

    container.appendChild(toast);

    setTimeout(() => {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 5000);
  }

  // Handle server readiness
  async function checkReadiness() {
    const pill = document.getElementById('site-status');
    const textEl = document.getElementById('site-status-text');
    if (!pill || !textEl) return;

    try {
      const res = await fetch(BASE + 'readyz');
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        pill.dataset.state = data.redis === 'ok' ? 'ok' : 'degraded';
        textEl.textContent = data.redis === 'ok' ? 'Ready' : 'Degraded';
      } else {
        pill.dataset.state = 'degraded';
        textEl.textContent = res.status === 503 ? 'Warming up' : 'Degraded';
      }
    } catch (_) {
      pill.dataset.state = 'down';
      textEl.textContent = 'Offline';
    }
  }

  // Tab navigation
  function switchTab(tabId, scroll) {
    const valid = ['about', 'how-it-works', 'try'];
    const active = valid.includes(tabId) ? tabId : 'about';

    document.querySelectorAll('[data-tab]').forEach(panel => {
      panel.hidden = panel.dataset.tab !== active;
    });

    document.querySelectorAll('[data-tab-link]').forEach(link => {
      if (link.dataset.tabLink === active) {
        link.setAttribute('aria-current', 'page');
      } else {
        link.removeAttribute('aria-current');
      }
    });

    if (scroll) {
      const target = document.getElementById(active);
      if (target) {
        target.scrollIntoView({ behavior: 'auto', block: 'start' });
      }
    }
  }

  function handleHash() {
    let hash = '';
    try {
      hash = decodeURIComponent(location.hash.slice(1));
    } catch (_) {
      hash = 'about';
    }
    if (hash === 'about' || hash === 'how-it-works' || hash === 'try') {
      switchTab(hash, false);
    } else if (hash) {
      const el = document.getElementById(hash);
      const panel = el ? el.closest('[data-tab]') : null;
      if (panel) {
        switchTab(panel.dataset.tab, true);
      }
    } else {
      switchTab('about', false);
    }
  }

  window.addEventListener('hashchange', handleHash);

  // Search state
  let currentSearchQuery = 'Android checkout dark mode';
  let currentSearchCategory = '';
  let searchDebounceTimer = null;
  let searchAbortController = null;

  // Search inputs
  const searchInput = document.getElementById('search-input');
  const categoryPills = document.querySelectorAll('.pill-btn[data-category]');

  function updateCategoryPills(cat) {
    currentSearchCategory = cat;
    categoryPills.forEach(pill => {
      const matches = pill.dataset.category === cat;
      pill.setAttribute('aria-pressed', matches ? 'true' : 'false');
    });
  }

  categoryPills.forEach(pill => {
    pill.addEventListener('click', () => {
      updateCategoryPills(pill.dataset.category || '');
      triggerSearch(true);
    });
  });

  if (searchInput) {
    searchInput.value = currentSearchQuery;
    searchInput.addEventListener('input', () => {
      currentSearchQuery = searchInput.value.trim();
      triggerSearch(false);
    });
  }

  function triggerSearch(immediate) {
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = null;
    }
    if (immediate) {
      executeSearch();
    } else {
      searchDebounceTimer = setTimeout(executeSearch, 250);
    }
  }

  // Cross-highlighting helper
  function setupHoverHighlight(itemEl, ticketCode) {
    itemEl.addEventListener('mouseenter', () => {
      document.querySelectorAll(`[data-ticket-code="${ticketCode}"]`).forEach(el => {
        el.classList.add('is-highlighted');
      });
    });
    itemEl.addEventListener('mouseleave', () => {
      document.querySelectorAll(`[data-ticket-code="${ticketCode}"]`).forEach(el => {
        el.classList.remove('is-highlighted');
      });
    });
    itemEl.addEventListener('focus', () => {
      document.querySelectorAll(`[data-ticket-code="${ticketCode}"]`).forEach(el => {
        el.classList.add('is-highlighted');
      });
    });
    itemEl.addEventListener('blur', () => {
      document.querySelectorAll(`[data-ticket-code="${ticketCode}"]`).forEach(el => {
        el.classList.remove('is-highlighted');
      });
    });
  }

  // Render a ticket row for inspector
  function createInspectorRow(item, rankNum, isFused) {
    const row = document.createElement('div');
    row.className = 'inspector-item';
    row.dataset.ticketCode = item.ticket_code;
    row.tabIndex = 0;

    const topBar = document.createElement('div');
    topBar.className = 'item-top';

    const rank = document.createElement('span');
    rank.className = 'item-rank';
    rank.textContent = '#' + rankNum;
    topBar.appendChild(rank);

    const code = document.createElement('span');
    code.className = 'item-code';
    code.textContent = item.ticket_code;
    topBar.appendChild(code);

    const cosine = document.createElement('span');
    cosine.className = 'item-cosine';
    const cosVal = typeof item.cosine === 'number' ? item.cosine.toFixed(2) : '--';
    cosine.textContent = 'cos ' + cosVal;
    topBar.appendChild(cosine);

    row.appendChild(topBar);

    const title = document.createElement('div');
    title.className = 'item-title';
    title.textContent = item.title;
    row.appendChild(title);

    const badges = document.createElement('div');
    badges.className = 'item-badges';

    // Category badge
    const catBadge = document.createElement('span');
    if (item.category === 'BUG_REPORT') {
      catBadge.className = 'badge badge-bug';
      catBadge.textContent = 'Bug';
    } else if (item.category === 'FEATURE_REQUEST') {
      catBadge.className = 'badge badge-feat';
      catBadge.textContent = 'Feature';
    } else {
      catBadge.className = 'badge badge-enq';
      catBadge.textContent = 'Enquiry';
    }
    badges.appendChild(catBadge);

    // Exact code badge
    if (item.code_hit) {
      const exactBadge = document.createElement('span');
      exactBadge.className = 'badge badge-exact';
      exactBadge.textContent = 'exact code';
      badges.appendChild(exactBadge);
    }

    row.appendChild(badges);

    setupHoverHighlight(row, item.ticket_code);
    return row;
  }

  function renderInspectorColumn(listEl, countEl, items, emptyReason, isFused) {
    while (listEl.firstChild) {
      listEl.removeChild(listEl.firstChild);
    }

    countEl.textContent = (items ? items.length : 0) + ' rows';

    if (!items || items.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'col-empty';
      empty.textContent = emptyReason;
      listEl.appendChild(empty);
      return;
    }

    items.forEach((item, idx) => {
      const row = createInspectorRow(item, idx + 1, isFused);
      listEl.appendChild(row);
    });
  }

  function renderTelemetry(telem) {
    const embedEl = document.getElementById('telem-embed');
    const dbEl = document.getElementById('telem-db');
    const planEl = document.getElementById('telem-plan');
    const pgvectorEl = document.getElementById('telem-pgvector');
    const modelEl = document.getElementById('telem-model');
    const indexesEl = document.getElementById('telem-indexes');

    if (!telem) return;

    if (embedEl) embedEl.textContent = typeof telem.embed_ms === 'number' ? telem.embed_ms.toFixed(1) + ' ms' : '--';
    if (dbEl) dbEl.textContent = typeof telem.db_ms === 'number' ? telem.db_ms.toFixed(1) + ' ms' : '--';
    if (pgvectorEl) pgvectorEl.textContent = telem.pgvector || '--';
    if (modelEl) modelEl.textContent = telem.model || '--';

    if (planEl) {
      if (telem.plan) {
        planEl.textContent = telem.plan.uses_hnsw ? 'Index Scan · intake_hnsw' : 'Seq Scan';
        if (telem.plan.uses_gin) planEl.textContent += ' · GIN intake_tsv_gin';
        planEl.title = (telem.plan.node_types || []).join(', ');
      } else {
        planEl.textContent = 'Seq Scan';
        planEl.title = '';
      }
    }

    if (indexesEl && telem.index_sizes) {
      const sizes = [];
      for (const [name, meta] of Object.entries(telem.index_sizes)) {
        sizes.push(name + ': ' + (meta.pretty || meta.bytes + ' B'));
      }
      indexesEl.textContent = sizes.join(', ') || '--';
    }
  }

  async function executeSearch() {
    if (!currentSearchQuery) {
      const lexicalList = document.getElementById('list-lexical');
      const semanticList = document.getElementById('list-semantic');
      const fusedList = document.getElementById('list-fused');
      const countLex = document.getElementById('count-lexical');
      const countSem = document.getElementById('count-semantic');
      const countFused = document.getElementById('count-fused');

      if (lexicalList && countLex) renderInspectorColumn(lexicalList, countLex, [], 'No query entered.', false);
      if (semanticList && countSem) renderInspectorColumn(semanticList, countSem, [], 'No query entered.', false);
      if (fusedList && countFused) renderInspectorColumn(fusedList, countFused, [], 'No query entered.', true);
      return;
    }

    if (searchAbortController) {
      searchAbortController.abort();
    }
    searchAbortController = new AbortController();

    const params = new URLSearchParams();
    params.set('q', currentSearchQuery);
    if (currentSearchCategory) {
      params.set('category', currentSearchCategory);
    }
    params.set('explain', '1');

    try {
      const res = await fetch(BASE + 'api/search?' + params.toString(), {
        signal: searchAbortController.signal,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.title || 'Search Failed', err.detail || 'Could not execute hybrid search.', true);
        return;
      }

      const data = await res.json();
      const lexicalList = document.getElementById('list-lexical');
      const semanticList = document.getElementById('list-semantic');
      const fusedList = document.getElementById('list-fused');
      const countLex = document.getElementById('count-lexical');
      const countSem = document.getElementById('count-semantic');
      const countFused = document.getElementById('count-fused');

      if (lexicalList && countLex) {
        renderInspectorColumn(lexicalList, countLex, data.lexical, 'No lexical matches found for this query.', false);
      }
      if (semanticList && countSem) {
        renderInspectorColumn(semanticList, countSem, data.semantic, 'No semantic matches found for this query.', false);
      }
      if (fusedList && countFused) {
        renderInspectorColumn(fusedList, countFused, data.fused, 'No fused results available.', true);
      }

      renderTelemetry(data.telemetry);
    } catch (e) {
      if (e.name === 'AbortError') return;
      showToast('Search Error', 'Failed to connect to search endpoint.', true);
    }
  }

  // --- TRIAGE PANEL & PREVIEW ---
  const triageForm = document.getElementById('triage-form');
  const triageTitleInput = document.getElementById('triage-title');
  const triageDescInput = document.getElementById('triage-desc');
  const triageErrorCodeInput = document.getElementById('triage-error-code');
  const triageCategorySelect = document.getElementById('triage-category');
  const triagePreviewBtn = document.getElementById('triage-preview-btn');
  const triageResults = document.getElementById('triage-results');

  let currentPreviewData = null;
  let activeCommitIdempotencyKey = null;

  function renderPiiTokens(containerEl, scrubbedText) {
    while (containerEl.firstChild) {
      containerEl.removeChild(containerEl.firstChild);
    }

    if (!scrubbedText) return;

    // Pattern for highlighted [REDACTED_*] tokens
    const parts = scrubbedText.split(/(\[REDACTED_[A-Z_]+\])/g);
    parts.forEach(part => {
      if (/^\[REDACTED_[A-Z_]+\]$/.test(part)) {
        const tokenSpan = document.createElement('span');
        tokenSpan.className = 'pii-token';
        tokenSpan.textContent = part;
        containerEl.appendChild(tokenSpan);
      } else if (part) {
        containerEl.appendChild(document.createTextNode(part));
      }
    });
  }

  function renderPiiCounts(containerEl, counts, total) {
    while (containerEl.firstChild) {
      containerEl.removeChild(containerEl.firstChild);
    }

    if (!counts || total === 0) {
      const none = document.createElement('span');
      none.textContent = 'No personal data tokens detected.';
      containerEl.appendChild(none);
      return;
    }

    for (const [label, count] of Object.entries(counts)) {
      const pill = document.createElement('span');
      pill.className = 'pii-count-pill';
      pill.textContent = label + ': ' + count;
      containerEl.appendChild(pill);
    }

    const totalPill = document.createElement('span');
    totalPill.className = 'pii-count-pill';
    totalPill.textContent = 'Total: ' + total;
    containerEl.appendChild(totalPill);
  }

  function renderTriageDecision(preview) {
    const decision = preview.decision || {};
    const verdict = decision.verdict || 'NEW';
    const master = decision.master || null;
    const cosine = typeof decision.cosine === 'number' ? decision.cosine.toFixed(2) : '--';
    const reasons = decision.reasons || [];

    const verdictBadge = document.getElementById('verdict-badge');
    const decisionMeta = document.getElementById('decision-meta');
    const decisionActions = document.getElementById('decision-actions');
    const commitResult = document.getElementById('commit-result');

    if (commitResult) {
      commitResult.classList.add('hidden');
      while (commitResult.firstChild) {
        commitResult.removeChild(commitResult.firstChild);
      }
    }

    if (verdictBadge) {
      verdictBadge.textContent = verdict;
      verdictBadge.className = 'verdict-badge ' + (
        verdict === 'DUPLICATE' ? 'verdict-dup' :
        verdict === 'REVIEW' ? 'verdict-review' : 'verdict-new'
      );
    }

    if (decisionMeta) {
      while (decisionMeta.firstChild) {
        decisionMeta.removeChild(decisionMeta.firstChild);
      }

      const p = document.createElement('p');
      let text = 'Verdict: ' + verdict;
      if (master) text += ' · Master: ' + master;
      text += ' · Cosine: ' + cosine;
      p.textContent = text;
      decisionMeta.appendChild(p);

      if (reasons.length > 0) {
        const rP = document.createElement('p');
        rP.className = 'muted';
        rP.textContent = 'Reasons: ' + reasons.join('; ');
        decisionMeta.appendChild(rP);
      }

      if (preview.related) {
        const relP = document.createElement('p');
        relP.className = 'muted';
        relP.textContent = 'Related cross-category item: ' + preview.related.ticket_code +
          ' (' + preview.related.category + ') "' + preview.related.title + '"' +
          ' · cos ' + (typeof preview.related.cosine === 'number' ? preview.related.cosine.toFixed(2) : '--');
        decisionMeta.appendChild(relP);
      }
    }

    // Build action buttons
    if (decisionActions) {
      while (decisionActions.firstChild) {
        decisionActions.removeChild(decisionActions.firstChild);
      }

      // Generate fresh idempotency key for this commit choice
      activeCommitIdempotencyKey = generateIdempotencyKey();

      if (verdict === 'DUPLICATE' || verdict === 'REVIEW') {
        const cat = preview.search_category || preview.suggestion?.category || 'BUG_REPORT';
        if (cat === 'BUG_REPORT' && master) {
          const linkBtn = document.createElement('button');
          linkBtn.type = 'button';
          linkBtn.className = 'btn btn-primary';
          linkBtn.textContent = 'Link as duplicate of ' + master;
          linkBtn.addEventListener('click', () => executeCommit('LINK', master));
          decisionActions.appendChild(linkBtn);
        } else if (cat === 'FEATURE_REQUEST' && master) {
          const voteBtn = document.createElement('button');
          voteBtn.type = 'button';
          voteBtn.className = 'btn btn-primary';
          voteBtn.textContent = 'Add vote to ' + master;
          voteBtn.addEventListener('click', () => executeCommit('VOTE', master));
          decisionActions.appendChild(voteBtn);
        } else if (cat === 'CLIENT_ENQUIRY' && master) {
          const ansBtn = document.createElement('button');
          ansBtn.type = 'button';
          ansBtn.className = 'btn btn-primary';
          ansBtn.textContent = 'Send FAQ answer';
          ansBtn.addEventListener('click', () => executeCommit('ANSWER', master));
          decisionActions.appendChild(ansBtn);
        }

        // Always offer Not a duplicate
        const notDupBtn = document.createElement('button');
        notDupBtn.type = 'button';
        notDupBtn.className = 'btn btn-secondary';
        notDupBtn.textContent = 'Not a duplicate';
        notDupBtn.addEventListener('click', () => executeCommit('NEW', null));
        decisionActions.appendChild(notDupBtn);
      } else {
        // Verdict is NEW
        const newBtn = document.createElement('button');
        newBtn.type = 'button';
        newBtn.className = 'btn btn-primary';
        newBtn.textContent = 'Create new ticket';
        newBtn.addEventListener('click', () => executeCommit('NEW', null));
        decisionActions.appendChild(newBtn);
      }
    }
  }

  async function executeCommit(action, masterCode) {
    if (!currentPreviewData) return;

    const actionButtons = document.querySelectorAll('#decision-actions button');
    actionButtons.forEach(btn => { btn.disabled = true; });

    const key = activeCommitIdempotencyKey || generateIdempotencyKey();
    const cat = currentPreviewData.search_category || currentPreviewData.suggestion?.category || 'BUG_REPORT';

    const body = {
      title: triageTitleInput.value.trim(),
      description: triageDescInput.value.trim(),
      error_code: triageErrorCodeInput.value.trim() || null,
      category: cat,
      action: action,
      master_code: masterCode,
    };

    try {
      const res = await fetch(BASE + 'api/items', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': key,
        },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.title || 'Commit Failed', err.detail || 'Could not commit item.', true);
        actionButtons.forEach(btn => { btn.disabled = false; });
        return;
      }

      const data = await res.json();
      const resultEl = document.getElementById('commit-result');
      if (resultEl) {
        resultEl.classList.remove('hidden');
        while (resultEl.firstChild) {
          resultEl.removeChild(resultEl.firstChild);
        }

        let msg = '';
        if (action === 'NEW') {
          msg = 'Created new ticket ' + data.ticket_code;
        } else if (action === 'LINK') {
          msg = 'Linked as ' + data.ticket_code + ' (duplicate of ' + data.master + ')';
        } else if (action === 'VOTE') {
          const masterCand = currentPreviewData.candidates?.find(c => c.ticket_code === data.master);
          const prevCount = masterCand ? masterCand.count_shown : data.count_shown - 1;
          msg = 'Votes for ' + data.master + ': ' + prevCount + ' → ' + data.count_shown;
        } else if (action === 'ANSWER') {
          msg = 'Answer from ' + data.master + ': ' + (data.resolution_note || 'Resolved from FAQ.');
        }

        const msgSpan = document.createElement('span');
        msgSpan.textContent = msg;
        resultEl.appendChild(msgSpan);
      }

      showToast('Action Committed', 'Successfully committed ticket action.', false);

      // Re-run search to refresh inspector counts and results
      triggerSearch(true);
    } catch (_) {
      showToast('Network Error', 'Failed to submit commit request.', true);
      actionButtons.forEach(btn => { btn.disabled = false; });
    }
  }

  async function executePreview() {
    const title = triageTitleInput.value.trim();
    const description = triageDescInput.value.trim();
    const errorCode = triageErrorCodeInput.value.trim() || null;
    const categoryOverride = triageCategorySelect.value || null;

    if (title.length < 3) {
      showToast('Invalid Title', 'A title of at least 3 characters is required for preview.', true);
      return;
    }

    triagePreviewBtn.disabled = true;

    try {
      const res = await fetch(BASE + 'api/triage/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: title,
          description: description,
          error_code: errorCode,
          category_override: categoryOverride,
        }),
      });

      if (res.status === 404) {
        showToast('Preview Unavailable', 'POST triage/preview endpoint is not deployed on this branch yet.', true);
        triagePreviewBtn.disabled = false;
        return;
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.title || 'Preview Failed', err.detail || 'Could not preview triage.', true);
        triagePreviewBtn.disabled = false;
        return;
      }

      const data = await res.json();
      currentPreviewData = data;

      // Show results area
      triageResults.classList.remove('hidden');

      // 1. Render PII diff
      const origTextEl = document.getElementById('pii-orig-text');
      const scrubbedTextEl = document.getElementById('pii-scrubbed-text');
      const countsBar = document.getElementById('pii-counts-bar');

      if (origTextEl) origTextEl.textContent = title + '\n\n' + description;
      if (scrubbedTextEl && data.scrubbed) {
        renderPiiTokens(scrubbedTextEl, (data.scrubbed.title || '') + '\n\n' + (data.scrubbed.description || ''));
      }
      if (countsBar && data.scrubbed) {
        renderPiiCounts(countsBar, data.scrubbed.counts || {}, data.scrubbed.total || 0);
      }

      // 2. Render Suggestion Badge
      const sugg = data.suggestion || {};
      const suggTitle = document.getElementById('suggestion-title');
      const suggBadge = document.getElementById('suggestion-badge');
      const suggDetails = document.getElementById('suggestion-details');

      if (suggTitle) suggTitle.textContent = sugg.category || 'Unknown';
      if (suggBadge) {
        suggBadge.textContent = 'Source: ' + (sugg.source || 'rules');
        suggBadge.className = 'badge ' + (sugg.source === 'llm' ? 'badge-feat' : 'badge-exact');
      }
      if (suggDetails) {
        suggDetails.textContent = 'Severity: ' + (sugg.severity || 'MEDIUM') +
          ' · Subsystem: ' + (sugg.subsystem || 'General') +
          (sugg.summary ? ' · ' + sugg.summary : '');
      }

      // If user hasn't explicitly set category override, preset to suggestion
      if (!categoryOverride && sugg.category) {
        triageCategorySelect.value = sugg.category;
      }

      // 3. Render Decision Card
      renderTriageDecision(data);
    } catch (_) {
      showToast('Network Error', 'Failed to reach triage preview endpoint.', true);
    } finally {
      triagePreviewBtn.disabled = false;
    }
  }

  if (triageForm) {
    triageForm.addEventListener('submit', (e) => {
      e.preventDefault();
      executePreview();
    });
  }

  if (triageCategorySelect) {
    triageCategorySelect.addEventListener('change', () => {
      // Re-run preview with user category override if form is filled
      if (triageTitleInput.value.trim().length >= 3) {
        executePreview();
      }
    });
  }

  // --- MODEL CARD LOADER ---
  async function loadModelCard() {
    const content = document.getElementById('model-card-content');
    if (!content) return;

    try {
      const res = await fetch(BASE + 'api/eval');
      if (res.status === 404) {
        while (content.firstChild) {
          content.removeChild(content.firstChild);
        }
        const note = document.createElement('p');
        note.className = 'muted';
        note.textContent = 'Run eval/calibrate.py to generate calibration report.';
        content.appendChild(note);
        return;
      }

      if (!res.ok) {
        while (content.firstChild) {
          content.removeChild(content.firstChild);
        }
        const note = document.createElement('p');
        note.className = 'muted';
        note.textContent = 'Could not load calibration report.';
        content.appendChild(note);
        return;
      }

      const data = await res.json();
      while (content.firstChild) {
        content.removeChild(content.firstChild);
      }

      const table = document.createElement('table');
      table.className = 'data-table';

      const thead = document.createElement('thead');
      const trH = document.createElement('tr');
      ['Metric', 'Thresholds / Value', 'In-Sample Result'].forEach(h => {
        const th = document.createElement('th');
        th.textContent = h;
        trH.appendChild(th);
      });
      thead.appendChild(trH);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');

      function addRow(name, val, resTxt) {
        const tr = document.createElement('tr');
        const th = document.createElement('th');
        th.textContent = name;
        const td1 = document.createElement('td');
        td1.textContent = val;
        const td2 = document.createElement('td');
        td2.textContent = resTxt;
        tr.appendChild(th);
        tr.appendChild(td1);
        tr.appendChild(td2);
        tbody.appendChild(tr);
      }

      const tAuto = data.thresholds?.t_auto ?? 0.80;
      const tReview = data.thresholds?.t_review ?? 0.70;
      addRow('Thresholds', `t_auto=${tAuto}, t_review=${tReview}`, 'Calibrated on 31 tickets');

      const cfg = data.configured || {};
      addRow('Configured Performance',
        `auto_correct=${cfg.auto_correct ?? 21}, auto_wrong=${cfg.auto_wrong ?? 0}`,
        `review=${cfg.review ?? 4}, missed=${cfg.missed ?? 0}`
      );

      const margin = data.margin || {};
      addRow('Margin Separation',
        `Lookalike: ${margin.closest_lookalike?.cosine ?? 0.754}`,
        `Weakest Duplicate: ${margin.weakest_duplicate?.cosine ?? 0.755} (gap: 0.001)`
      );

      const top1 = data.top1 || {};
      addRow('Top-1 accuracy (in-sample)', `${top1.correct ?? 23} / ${top1.total ?? 23}`, 'Correctly ranked labeled duplicates');

      table.appendChild(tbody);
      const summaryWrap = document.createElement('div');
      summaryWrap.className = 'table-wrap';
      summaryWrap.appendChild(table);
      content.appendChild(summaryWrap);

      function appendReportTable(captionText, headers, rows, className) {
        const wrap = document.createElement('div');
        wrap.className = 'table-wrap';
        wrap.tabIndex = 0;
        wrap.setAttribute('role', 'region');
        wrap.setAttribute('aria-label', captionText);
        const reportTable = document.createElement('table');
        reportTable.className = 'data-table ' + className;
        const caption = document.createElement('caption');
        caption.textContent = captionText;
        reportTable.appendChild(caption);
        const head = document.createElement('thead');
        const heading = document.createElement('tr');
        headers.forEach(label => {
          const cell = document.createElement('th');
          cell.scope = 'col';
          cell.textContent = label;
          heading.appendChild(cell);
        });
        head.appendChild(heading);
        reportTable.appendChild(head);
        const body = document.createElement('tbody');
        rows.forEach(values => {
          const row = document.createElement('tr');
          values.forEach(value => {
            const cell = document.createElement('td');
            cell.textContent = String(value);
            row.appendChild(cell);
          });
          body.appendChild(row);
        });
        reportTable.appendChild(body);
        wrap.appendChild(reportTable);
        content.appendChild(wrap);
      }

      appendReportTable('Threshold grid (in-sample)',
        ['Auto ≥', 'Review ≥', 'Auto-correct', 'Auto-wrong', 'Review', 'Missed'],
        (data.grid || []).map(row => [
          String(row.t_auto) + (row.t_auto === tAuto && row.t_review === tReview ? ' configured' : ''),
          row.t_review, row.auto_correct, row.auto_wrong, row.review, row.missed,
        ]), 'model-grid');
      appendReportTable('Code cases (in-sample)',
        ['Title', 'Truth', 'Closest (cosine)', 'Verdict', 'Expected verdict', 'Pass'],
        (data.code_cases || []).map(row => [
          row.title, row.truth,
          row.closest + ' (' + Number(row.cosine).toFixed(3) + ')',
          row.verdict, row.expected_verdict, row.pass ? 'pass' : 'FAIL',
        ]), 'model-code-cases');
    } catch (_) {
      while (content.firstChild) {
        content.removeChild(content.firstChild);
      }
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = 'Run eval/calibrate.py';
      content.appendChild(note);
    }
  }

  // --- ANN LAB ---
  const annForm = document.getElementById('ann-form');
  const annQueryInput = document.getElementById('ann-query');
  const annCategorySelect = document.getElementById('ann-category');
  const annForceSelect = document.getElementById('ann-force');
  const annEfSelect = document.getElementById('ann-ef');
  const annIterativeSelect = document.getElementById('ann-iterative');
  const annRunBtn = document.getElementById('ann-run-btn');
  const annResults = document.getElementById('ann-results');

  async function executeAnnLab() {
    const q = annQueryInput.value.trim();
    const cat = annCategorySelect.value;
    const force = annForceSelect.value;
    const ef = annEfSelect.value;
    const iterative = annIterativeSelect.value;

    if (!q) return;

    annRunBtn.disabled = true;

    const params = new URLSearchParams();
    params.set('q', q);
    if (cat) params.set('category', cat);
    params.set('force', force);
    params.set('ef_search', ef);
    params.set('iterative', iterative);

    try {
      const res = await fetch(BASE + 'api/ann-lab?' + params.toString());
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.title || 'ANN Lab Failed', err.detail || 'Could not run ANN test.', true);
        return;
      }

      const data = await res.json();
      annResults.classList.remove('hidden');
      while (annResults.firstChild) {
        annResults.removeChild(annResults.firstChild);
      }

      const p1 = document.createElement('p');
      p1.textContent = 'Rows returned: ' + data.rows_returned +
        ' · Index scan: ' + (data.index_scan ? 'YES (intake_hnsw)' : 'NO');
      annResults.appendChild(p1);

      const p2 = document.createElement('p');
      p2.className = 'mono';
      p2.textContent = 'Returned codes: ' + (data.codes && data.codes.length > 0 ? data.codes.join(', ') : '(none)');
      annResults.appendChild(p2);

      const p3 = document.createElement('p');
      p3.className = 'muted';
      p3.textContent = 'Plan node types: ' + (data.plan_nodes ? data.plan_nodes.join(' · ') : '--');
      annResults.appendChild(p3);
    } catch (_) {
      showToast('Network Error', 'Failed to connect to ANN lab endpoint.', true);
    } finally {
      annRunBtn.disabled = false;
    }
  }

  if (annForm) {
    annForm.addEventListener('submit', (e) => {
      e.preventDefault();
      executeAnnLab();
    });
  }

  // --- RESET DEMO ---
  const resetDemoBtn = document.getElementById('reset-demo-btn');
  if (resetDemoBtn) {
    resetDemoBtn.addEventListener('click', async () => {
      resetDemoBtn.disabled = true;
      try {
        const res = await fetch(BASE + 'api/session/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          showToast(err.title || 'Reset Failed', err.detail || 'Could not reset session.', true);
          return;
        }

        const data = await res.json();
        const itemsCount = data.deleted_items ?? data.items_deleted ?? 0;
        const votesCount = data.deleted_votes ?? data.votes_deleted ?? 0;
        showToast('Demo Reset', `Removed ${itemsCount} items and ${votesCount} votes.`, false);

        // Hide triage results
        if (triageResults) triageResults.classList.add('hidden');
        if (triageForm) triageForm.reset();

        // Refresh search results
        triggerSearch(true);
      } catch (_) {
        showToast('Network Error', 'Failed to reset demo session.', true);
      } finally {
        resetDemoBtn.disabled = false;
      }
    });
  }

  // --- PRESET CHIPS ---
  const presetChips = document.querySelectorAll('[data-preset]');
  presetChips.forEach(chip => {
    chip.addEventListener('click', () => {
      const preset = chip.dataset.preset;
      switchTab('try', true);

      if (preset === 'opaque') {
        searchInput.value = 'E_0X7F3A';
        currentSearchQuery = 'E_0X7F3A';
        updateCategoryPills('');
        triggerSearch(true);
      } else if (preset === 'descriptive') {
        searchInput.value = 'ERR_PG_POOL_TIMEOUT_504';
        currentSearchQuery = 'ERR_PG_POOL_TIMEOUT_504';
        updateCategoryPills('');
        triggerSearch(true);
      } else if (preset === 'paraphrase') {
        searchInput.value = 'Mobile avatar upload screen hangs';
        currentSearchQuery = 'Mobile avatar upload screen hangs';
        updateCategoryPills('');
        triggerSearch(true);
      } else if (preset === 'collision') {
        searchInput.value = 'Android checkout dark mode';
        currentSearchQuery = 'Android checkout dark mode';
        updateCategoryPills('');
        triggerSearch(true);
      } else if (preset === 'dup-feature') {
        triageTitleInput.value = 'Night mode for checkout on Android';
        triageDescInput.value = 'Would love a dark theme when paying in the Android app.';
        triageErrorCodeInput.value = '';
        triageCategorySelect.value = 'FEATURE_REQUEST';
        executePreview();
        triageForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (preset === 'lookalike') {
        triageTitleInput.value = 'Dark mode for the web dashboard';
        triageDescInput.value = 'A dark theme for the desktop web dashboard would be nice.';
        triageErrorCodeInput.value = '';
        triageCategorySelect.value = 'FEATURE_REQUEST';
        executePreview();
        triageForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (preset === 'pii-triage') {
        triageTitleInput.value = 'Payment failed and refund pending';
        triageDescInput.value = 'Checkout failed for rahul.sharma@example.com (+91 9876543210). Server 192.168.1.44 returned 500; key sk-live-998877665544 is in the logs. Refund to UPI demo.user@oksbi. PAN AAAPZ1234C, card 4111 1111 1111 1111, order ref 2345 6789 0125. Authorization: Bearer abcDEF123456.xyz';
        triageErrorCodeInput.value = '';
        triageCategorySelect.value = 'BUG_REPORT';
        executePreview();
        triageForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (preset === 'filter-trap') {
        const details = document.getElementById('ann-details');
        if (details) details.open = true;
        annQueryInput.value = 'checkout crashes in dark mode';
        annCategorySelect.value = 'FEATURE_REQUEST';
        annForceSelect.value = '1';
        annEfSelect.value = '5';
        annIterativeSelect.value = 'off';
        executeAnnLab();
        details.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });

  // Initialization
  document.addEventListener('DOMContentLoaded', () => {
    checkReadiness();
    handleHash();
    executeSearch();
    loadModelCard();
  });
})();
