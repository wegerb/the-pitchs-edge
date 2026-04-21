/**
 * The Pitch's Edge — static dashboard.
 * Reads data.json (produced by scripts/export_web_data.py) and renders the
 * fixture cards, KPIs, filters, and detail panels. Vanilla JS, no framework.
 */

const App = (() => {
  const LEAGUE_FLAG = {
    E0:  '🏴󠁧󠁢󠁥󠁮󠁧󠁿', E1: '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
    SP1: '🇪🇸', I1: '🇮🇹', D1: '🇩🇪', F1: '🇫🇷',
  };

  let payload = null;
  let leagueFilter = null;       // null = all
  let minEdge = 0;               // 0 = show all
  let currentDate = null;        // yyyy-mm-dd; null = all upcoming
  let showEdgesOnly = false;

  // ── date / format helpers ──
  function fmtDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }
  function fmtDisplayDate(d) {
    return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  }
  function fmtKickoff(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
    } catch { return ''; }
  }
  function fmtKickoffDay(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    } catch { return ''; }
  }
  function pct(v, places = 1) {
    if (v == null || Number.isNaN(v)) return '—';
    return `${(v * 100).toFixed(places)}%`;
  }
  function pctSigned(v, places = 1) {
    if (v == null || Number.isNaN(v)) return '—';
    const s = (v * 100).toFixed(places);
    return v > 0 ? `+${s}%` : `${s}%`;
  }
  function numPlain(v, places = 2) {
    if (v == null || Number.isNaN(v)) return '—';
    return v.toFixed(places);
  }

  function selectionLabel(market, sel, line, home, away) {
    if (market === '1X2') {
      return { home: `${home} to win`, draw: 'Draw', away: `${away} to win` }[sel] || sel;
    }
    if (market === 'OU') {
      const side = sel === 'over' ? 'Over' : 'Under';
      return `${side} ${line ?? ''} goals`.trim();
    }
    if (market === 'BTTS') {
      return sel === 'yes' ? 'Both teams to score' : 'BTTS — No';
    }
    return `${market} · ${sel}`;
  }

  function starsSvg(count) {
    return Array.from({ length: 5 }, (_, i) =>
      `<svg class="star ${i < count ? 'filled' : ''}" viewBox="0 0 20 20" fill="currentColor"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>`
    ).join('');
  }

  function formPips(formStr) {
    if (!formStr || formStr === '—') return '<span class="team-form"></span>';
    const pips = formStr.split('').map(c => {
      const letter = ['W', 'D', 'L'].includes(c) ? c : '';
      return letter ? `<span class="form-pip ${letter}">${letter}</span>` : '';
    }).join('');
    return `<span class="team-form" title="Last ${formStr.length} results (left=most recent)">${pips}</span>`;
  }

  // ── filtering ──
  function visibleFixtures() {
    if (!payload) return [];
    return payload.fixtures.filter(f => {
      if (leagueFilter && f.league_code !== leagueFilter) return false;
      if (currentDate && !f.kickoff.startsWith(currentDate)) return false;
      if (showEdgesOnly && f.edges.length === 0) return false;
      if (minEdge > 0) {
        if (!f.edges.length || f.edges[0].edge_pct < minEdge) return false;
      }
      return true;
    });
  }

  // ── renderers ──
  function renderKpis(fixtures) {
    const totalFix  = fixtures.length;
    const edgeCount = fixtures.reduce((s, f) => s + f.edges.length, 0);
    const strong    = fixtures.reduce((s, f) => s + f.edges.filter(e => e.tier === 'strong').length, 0);
    const topEdge   = fixtures.flatMap(f => f.edges).sort((a, b) => b.edge_pct - a.edge_pct)[0];

    return `
      <div class="kpi-row">
        <div class="kpi-card">
          <span class="kpi-label">Fixtures shown</span>
          <span class="kpi-value accent">${totalFix}</span>
          <span class="kpi-sub">after filters</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Edges flagged</span>
          <span class="kpi-value">${edgeCount}</span>
          <span class="kpi-sub">model beats market &gt; 2%</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Strong edges</span>
          <span class="kpi-value warning">${strong}</span>
          <span class="kpi-sub">7%+ model overlay</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">Best play</span>
          <span class="kpi-value ${topEdge ? 'accent' : ''}">${topEdge ? pctSigned(topEdge.edge_pct) : '—'}</span>
          <span class="kpi-sub">${topEdge ? `${topEdge.market} · ${topEdge.selection}` : 'none above 2%'}</span>
        </div>
      </div>
    `;
  }

  function renderFilters() {
    const leagues = payload.leagues;
    const leagueChips = [
      `<button class="chip-btn${leagueFilter == null ? ' active' : ''}" data-league="">All leagues</button>`,
      ...leagues.map(l => `<button class="chip-btn${leagueFilter === l.code ? ' active' : ''}" data-league="${l.code}">${l.name}</button>`),
    ].join('');

    const edgeChips = [
      [0,     'All'],
      [0.02,  '2%+'],
      [0.04,  '4%+'],
      [0.07,  '7%+'],
    ].map(([val, label]) =>
      `<button class="chip-btn${Math.abs(minEdge - val) < 1e-9 ? ' active' : ''}" data-edge="${val}">${label}</button>`
    ).join('');

    return `
      <div class="filter-bar">
        <span class="filter-label">Leagues</span>
        ${leagueChips}
        <span class="filter-label" style="margin-left:var(--space-3);">Min edge</span>
        ${edgeChips}
        <button id="toggle-edges-only" class="chip-btn${showEdgesOnly ? ' active' : ''}" style="margin-left:var(--space-3);">
          Edges only
        </button>
      </div>
    `;
  }

  function renderProbBar(model) {
    const h = (model.home_win * 100).toFixed(1);
    const d = (model.draw     * 100).toFixed(1);
    const a = (model.away_win * 100).toFixed(1);
    return `
      <div class="prob-bar">
        <div class="prob-seg home" style="width:${h}%" title="Home win ${h}%">${parseFloat(h) >= 12 ? h + '%' : ''}</div>
        <div class="prob-seg draw" style="width:${d}%" title="Draw ${d}%">${parseFloat(d) >= 12 ? d + '%' : ''}</div>
        <div class="prob-seg away" style="width:${a}%" title="Away win ${a}%">${parseFloat(a) >= 12 ? a + '%' : ''}</div>
      </div>
      <div class="prob-legend">
        <span class="home-label">Home ${pct(model.home_win)}</span>
        <span class="draw-label">Draw ${pct(model.draw)}</span>
        <span class="away-label">Away ${pct(model.away_win)}</span>
      </div>
    `;
  }

  function renderSignalStrip(model) {
    const chips = [];
    const overLine = model.over25 >= 0.6 ? 'positive' : (model.over25 <= 0.4 ? 'info' : '');
    chips.push(`<span class="signal-chip ${overLine}"><span class="signal-chip-label">Over 2.5</span> ${pct(model.over25)}</span>`);
    const btts = model.btts_yes >= 0.55 ? 'positive' : '';
    chips.push(`<span class="signal-chip ${btts}"><span class="signal-chip-label">BTTS</span> ${pct(model.btts_yes)}</span>`);
    chips.push(`<span class="signal-chip"><span class="signal-chip-label">xG</span> ${numPlain(model.xg_home, 2)} – ${numPlain(model.xg_away, 2)}</span>`);
    return `<div class="signal-strip">${chips.join('')}</div>`;
  }

  function renderMeta(fixture) {
    const m = fixture.model;
    // Model favourite among the three 1X2 outcomes.
    const favs = [
      { label: `${fixture.home.short} win`, p: m.home_win },
      { label: 'Draw',                      p: m.draw },
      { label: `${fixture.away.short} win`, p: m.away_win },
    ].sort((a, b) => b.p - a.p);
    const fav = favs[0];
    const favBadge = `<span class="pick-badge" title="Model's most likely 1X2 outcome">
         <svg viewBox="0 0 20 20" fill="currentColor"><path d="M10 2l2.39 4.84L18 7.66l-4 3.9.94 5.49L10 14.77l-4.94 2.28L6 11.56 2 7.66l5.61-.82L10 2z"/></svg>
         Model pick · ${fav.label} ${pct(fav.p)}
       </span>`;

    const best = fixture.edges[0];
    const betBadge = best
      ? `<span class="bet-badge" title="Best model-vs-market edge — this is a betting recommendation, not a prediction">
           <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>
           Bet · ${selectionLabel(best.market, best.selection, best.line, fixture.home.short, fixture.away.short)}
         </span>
         <span class="edge-chip tier-${best.tier}">${pctSigned(best.edge_pct)} edge</span>
         <span class="confidence-stars" title="Edge confidence: ${best.stars}/5">${starsSvg(best.stars)}</span>`
      : `<span class="bet-badge flat">No edge &gt; 2%</span>`;
    return `<div class="card-meta">${favBadge}${betBadge}</div>`;
  }

  function renderDetailPanel(fixture) {
    const m = fixture.model;
    const mk = fixture.market;
    const delta = mk ? {
      home: m.home_win - mk.home,
      draw: m.draw     - mk.draw,
      away: m.away_win - mk.away,
    } : null;

    const bestScoresBlock = m.top_scores && m.top_scores.length ? `
      <div>
        <div class="detail-section-title">Most likely scorelines</div>
        <div class="score-grid">
          ${m.top_scores.map((s, i) => {
            const [label, prob] = Array.isArray(s) ? s : [s.score, s.prob];
            return `<div class="score-cell ${i === 0 ? 'top1' : ''}">
              <div class="score">${label}</div>
              <div class="pct">${pct(prob)}</div>
            </div>`;
          }).join('')}
        </div>
      </div>
    ` : '';

    const marketsBlock = `
      <div>
        <div class="detail-section-title">Model vs market</div>
        <table class="stat-table">
          <tbody>
            <tr><td>${fixture.home.name} win</td><td>${pct(m.home_win)}${mk ? ` <span style="color:var(--color-text-faint)"> (mkt ${pct(mk.home)}, ${pctSigned(delta.home)})</span>` : ''}</td></tr>
            <tr><td>Draw</td><td>${pct(m.draw)}${mk ? ` <span style="color:var(--color-text-faint)"> (mkt ${pct(mk.draw)}, ${pctSigned(delta.draw)})</span>` : ''}</td></tr>
            <tr><td>${fixture.away.name} win</td><td>${pct(m.away_win)}${mk ? ` <span style="color:var(--color-text-faint)"> (mkt ${pct(mk.away)}, ${pctSigned(delta.away)})</span>` : ''}</td></tr>
            <tr><td>Over 2.5 goals</td><td>${pct(m.over25)}</td></tr>
            <tr><td>BTTS — Yes</td><td>${pct(m.btts_yes)}</td></tr>
            <tr><td>Expected goals</td><td>${numPlain(m.xg_home, 2)} – ${numPlain(m.xg_away, 2)}</td></tr>
          </tbody>
        </table>
      </div>
    `;

    const edgesBlock = fixture.edges.length ? `
      <div>
        <div class="detail-section-title">Recommended plays (¼ Kelly, 2% cap)</div>
        <div class="edge-table-wrap">
        <table class="edge-table">
          <thead><tr><th>Play</th><th>Book</th><th class="num">Price</th><th class="num">Edge</th><th class="num">Stake</th></tr></thead>
          <tbody>
            ${fixture.edges.map(e => {
              const marketTag = e.market + (e.line != null ? ` ${e.line}` : '');
              const sel = selectionLabel(e.market, e.selection, e.line, fixture.home.short, fixture.away.short);
              return `
              <tr>
                <td><span class="play-cell"><span class="mkt-tag">${marketTag}</span> ${sel}</span></td>
                <td class="book-cell" title="${e.book}">${e.book}</td>
                <td class="num">${numPlain(e.price, 2)}</td>
                <td class="num edge-positive">${pctSigned(e.edge_pct)}</td>
                <td class="num">${(e.kelly_fraction * 100).toFixed(2)}u</td>
              </tr>
            `;}).join('')}
          </tbody>
        </table>
        </div>
      </div>
    ` : '';

    return `<div class="detail-inner">
      ${marketsBlock}
      ${bestScoresBlock}
      ${edgesBlock}
    </div>`;
  }

  function renderCard(fixture, idx) {
    const leagueTag = LEAGUE_FLAG[fixture.league_code] || '';
    return `
      <article class="game-card" style="animation-delay:${Math.min(idx, 15) * 60}ms">
        <div class="game-card-header" data-expand>
          <div class="card-top">
            <span class="league-chip">${leagueTag} ${fixture.league_name}</span>
            <span class="kickoff-chip">${fmtKickoffDay(fixture.kickoff)} · ${fmtKickoff(fixture.kickoff)}</span>
          </div>
          <div class="teams-row">
            <div class="team-block home">
              <div class="team-name" title="${fixture.home.name}">${fixture.home.name}</div>
              ${formPips(fixture.home.form)}
            </div>
            <div class="vs-badge">
              <span class="vs-text">vs</span>
            </div>
            <div class="team-block away">
              <div class="team-name" title="${fixture.away.name}">${fixture.away.name}</div>
              ${formPips(fixture.away.form)}
            </div>
          </div>
          ${renderProbBar(fixture.model)}
          ${renderSignalStrip(fixture.model)}
          ${renderMeta(fixture)}
        </div>
        <div class="expand-indicator" data-expand>
          <span>Model details & edges</span>
          <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
        </div>
        <div class="detail-panel">${renderDetailPanel(fixture)}</div>
      </article>
    `;
  }

  function renderMethodology() {
    return `
      <section class="methodology">
        <div class="section-header">How it works</div>
        <div class="methodology-grid">
          <div class="method-card">
            <h3><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 3v18M3 12h18"/></svg>Dixon-Coles</h3>
            <p>Bivariate Poisson with a <code>rho</code> low-score correction and exponential time-decay (<code>xi=0.01</code>). Attack / defense strengths estimated per team, re-fit weekly.</p>
          </div>
          <div class="method-card">
            <h3><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M21 7h-6"/><path d="M21 7v6"/></svg>Shin devig</h3>
            <p>Book probabilities are devigged with Shin's method, which handles asymmetric overround more honestly than a flat proportional split.</p>
          </div>
          <div class="method-card">
            <h3><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9h6v6H9z"/></svg>Edge &amp; Kelly</h3>
            <p>Edge = <code>model_prob × price − 1</code>. Stake is ¼-Kelly, capped at 2% of bankroll to bound variance when the model is wrong.</p>
          </div>
          <div class="method-card">
            <h3><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>CLV &gt; win rate</h3>
            <p>We judge plays by <em>closing-line value</em> — if you consistently beat the closing price, you have an edge even when variance sends the win rate sideways.</p>
          </div>
        </div>
      </section>
    `;
  }

  // ── mount ──
  function render() {
    const main = document.getElementById('main-content');
    if (!payload) {
      main.innerHTML = `
        <div class="state-message">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
          <h2>Data unavailable</h2>
          <p>Couldn't load <code>data.json</code>. Run <code>python -m scripts.export_web_data</code> and refresh.</p>
        </div>`;
      return;
    }

    const fixtures = visibleFixtures();
    updateHeaderMeta(fixtures.length);

    const notes = Array.isArray(payload.pipeline_notes) ? payload.pipeline_notes : [];
    const totalAvailable = payload.stats.total_fixtures;
    const filtersActive = leagueFilter || minEdge > 0 || showEdgesOnly || currentDate;

    let grid;
    if (fixtures.length) {
      grid = `<div class="game-grid">${fixtures.map(renderCard).join('')}</div>`;
    } else if (totalAvailable === 0) {
      const noteList = notes.length
        ? `<ul class="pipeline-notes">${notes.map(n => `<li><code>${n}</code></li>`).join('')}</ul>`
        : '';
      grid = `<div class="state-message">
           <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
           <h2>No fixtures yet</h2>
           <p>Run the ingest pipeline so the DB has upcoming fixtures &amp; live odds, then regenerate this page.</p>
           ${noteList}
           <p><code>python -m scripts.fetch_fixtures &amp;&amp; python -m scripts.fetch_odds &amp;&amp; python -m scripts.export_web_data</code></p>
         </div>`;
    } else {
      grid = `<div class="state-message">
           <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
           <h2>No fixtures match the current filters</h2>
           <p>${filtersActive ? 'Clear filters or lower the edge threshold.' : 'Try a different date.'}</p>
         </div>`;
    }

    const fetchedAt = new Date(payload.generated_at).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' });
    const dataInfo = `<div class="data-info">Generated ${fetchedAt} · ${totalAvailable} fixtures across ${payload.stats.leagues_active} leagues</div>`;

    main.innerHTML = `
      ${renderKpis(fixtures)}
      ${dataInfo}
      ${renderFilters()}
      <div class="section-header">
        <svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
        Fixtures
      </div>
      ${grid}
      ${renderMethodology()}
    `;
    main.setAttribute('aria-busy', 'false');

    bindInteractions();
  }

  function bindInteractions() {
    document.querySelectorAll('[data-expand]').forEach(el => {
      el.addEventListener('click', () => {
        el.closest('.game-card').classList.toggle('expanded');
      });
    });

    document.querySelectorAll('.chip-btn[data-league]').forEach(btn => {
      btn.addEventListener('click', () => {
        const code = btn.dataset.league;
        leagueFilter = code || null;
        render();
      });
    });

    document.querySelectorAll('.chip-btn[data-edge]').forEach(btn => {
      btn.addEventListener('click', () => {
        minEdge = parseFloat(btn.dataset.edge);
        render();
      });
    });

    const toggle = document.getElementById('toggle-edges-only');
    if (toggle) toggle.addEventListener('click', () => { showEdgesOnly = !showEdgesOnly; render(); });
  }

  function updateHeaderMeta(count) {
    const badge = document.getElementById('game-count-badge');
    if (badge) badge.textContent = `${count} fixture${count === 1 ? '' : 's'}`;
    const headerDate = document.getElementById('header-date');
    if (headerDate) headerDate.textContent = currentDate
      ? fmtDisplayDate(new Date(currentDate + 'T12:00:00'))
      : 'All upcoming';
  }

  // ── boot ──
  async function load() {
    try {
      const res = await fetch(`data.json?t=${Date.now()}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      payload = await res.json();
      if (payload.disclaimer) {
        const d = document.getElementById('footer-disclaimer');
        if (d) d.textContent = payload.disclaimer;
      }
    } catch (e) {
      console.error('[data]', e);
      payload = null;
    }
    render();
  }

  function bindHeader() {
    const themeBtn = document.getElementById('theme-toggle');
    const root = document.documentElement;
    const savedTheme = localStorage.getItem('pe-theme') || 'dark';
    root.setAttribute('data-theme', savedTheme);
    updateThemeIcon(savedTheme);
    themeBtn.addEventListener('click', () => {
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      localStorage.setItem('pe-theme', next);
      updateThemeIcon(next);
    });

    const dateInput = document.getElementById('date-input');
    const prev = document.getElementById('date-prev');
    const next = document.getElementById('date-next');
    dateInput.addEventListener('change', () => {
      currentDate = dateInput.value || null;
      render();
    });
    prev.addEventListener('click', () => stepDate(-1));
    next.addEventListener('click', () => stepDate(1));

    document.getElementById('refresh-btn').addEventListener('click', () => {
      const btn = document.getElementById('refresh-btn');
      btn.classList.add('spinning');
      load().finally(() => setTimeout(() => btn.classList.remove('spinning'), 300));
    });
  }

  function stepDate(delta) {
    const base = currentDate ? new Date(currentDate + 'T12:00:00') : new Date();
    base.setDate(base.getDate() + delta);
    currentDate = fmtDate(base);
    document.getElementById('date-input').value = currentDate;
    render();
  }

  function updateThemeIcon(theme) {
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    toggle.innerHTML = theme === 'dark'
      ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`
      : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
  }

  return { init: () => { bindHeader(); load(); } };
})();

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', App.init);
} else {
  App.init();
}
