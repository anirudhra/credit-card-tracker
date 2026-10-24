/**
 * Smart Wallet Advisor & Multi-Card Perk Tracker — Frontend Controller
 */

let appState = null;
let currentView = "advisor"; // 'advisor' (landing page) or 'tracker'
let currentFilter = "all";
let currentAdvisorGroup = "all";
let currentSearchQuery = "";
let currentYear = new Date().getFullYear();

const fmtCurrency = (val) => {
  const num = Number(val) || 0;
  return num % 1 === 0 ? `$${num.toLocaleString()}` : `$${num.toFixed(2)}`;
};

function isSwipeCounterPerk(perk) {
  return perk.category === "Multiplier Bonus" && perk.name.includes("20+");
}

function fmtPerkUnit(perk, val) {
  const num = Number(val) || 0;
  if (isSwipeCounterPerk(perk)) {
    return `${Math.round(num)} swipes`;
  }
  return fmtCurrency(num);
}

async function apiCall(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || "Request failed");
  }
  return res.json();
}

async function loadState(cardId = null) {
  const params = new URLSearchParams({ year: String(currentYear) });
  if (cardId) params.set("card_id", String(cardId));
  appState = await apiCall(`/api/state?${params.toString()}`);
  renderAll();
}

function switchPrimaryView(viewName) {
  currentView = viewName;
  document.getElementById("viewAdvisor").hidden = viewName !== "advisor";
  document.getElementById("viewTracker").hidden = viewName !== "tracker";
  document.getElementById("tabBtnAdvisor").classList.toggle("active", viewName === "advisor");
  document.getElementById("tabBtnTracker").classList.toggle("active", viewName === "tracker");
}

function renderAll() {
  if (!appState) return;
  renderTopbar();
  renderSyncStatusBar();
  renderQuarterSpotlight();
  renderMerchantAdvisorGrid();
  renderUpcomingRotations();
  renderWalletStrip();
  renderEarningBanner();
  renderRoiHero();
  renderExpiringBanner();
  renderPerksList();
}

function renderTopbar() {
  const cardSelect = document.getElementById("cardSelect");
  cardSelect.innerHTML = "";
  for (const c of appState.cards) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `${c.name} ($${c.annual_fee})`;
    if (c.id === appState.active_card.id) opt.selected = true;
    cardSelect.appendChild(opt);
  }

  const yearSelect = document.getElementById("yearSelect");
  yearSelect.value = String(appState.summary.year);
}

// ---------------------------------------------------------------------------
// Landing Page: Live Issuer Sync Bar & Quarter Spotlight
// ---------------------------------------------------------------------------
function renderSyncStatusBar() {
  const syncRows = appState.issuer_sync_status || [];
  const onlineCount = syncRows.filter((r) => r.online).length;
  const vCount = appState.scraped_vendors_count || 0;
  const sched = appState.sync_schedule || {};
  const lastSync = sched.last_sync_utc || (syncRows.length > 0 ? syncRows[0].last_checked : "Just now");
  const syncDate = sched.last_sync_date || new Date().toISOString().slice(0, 10);
  const trig = sched.last_trigger || "";
  const triggerLabel =
    trig === "manual_button" || trig === "manual"
      ? "Manual Sync"
      : sched.did_sync_on_this_open
      ? "Just Synced (1st Open Today)"
      : `Already Synced Today (${syncDate} — Skipped)`;

  document.getElementById("syncSummaryLabel").textContent =
    `Vendor Sync (${onlineCount}/${syncRows.length} Issuer Sites Live • ${vCount} Named Vendors Scraped)`;
  document.getElementById("syncTimestampLabel").textContent =
    `✅ ${triggerLabel} • Last Synced: ${lastSync}`;

  const grid = document.getElementById("syncSourcesGrid");
  grid.innerHTML = "";
  for (const s of syncRows) {
    const div = document.createElement("div");
    div.className = "sync-source-card";
    const statusBadge = s.online
      ? `<span class="badge badge-activated">HTTP ${s.http_status} Live</span>`
      : `<span class="badge badge-activation">Cached</span>`;
    div.innerHTML = `
      <div class="sync-source-top">
        <strong>${s.card_name}</strong>
        ${statusBadge}
      </div>
      <p class="sync-source-rules">${s.verified_rules}</p>
      <div class="sync-source-links">
        <a href="${s.url}" target="_blank" rel="noopener">🔗 Official Terms Page</a>
        <a href="${s.activation_url}" target="_blank" rel="noopener">⚡ Activation Portal</a>
      </div>
    `;
    grid.appendChild(div);
  }
}

function renderQuarterSpotlight() {
  const q = appState.wallet_overview?.current_quarter || 4;
  const quarterThemes = {
    1: {
      name: "Q1 (Jan–Mar)",
      freedom: "Groceries (excl. Walmart/Target), Fitness Clubs & Norwegian Cruise Line",
      discover: "Grocery Stores, Wholesale Clubs & Select Streaming Services",
    },
    2: {
      name: "Q2 (Apr–Jun)",
      freedom: "Amazon.com, Whole Foods Market & Select Streaming Services",
      discover: "Restaurants & Home Improvement Stores",
    },
    3: {
      name: "Q3 (Jul–Sep)",
      freedom: "Gas Stations, EV Charging, Live Entertainment & Movie Theaters",
      discover: "Gas Stations, EV Charging, Public Transit & Drug Stores",
    },
    4: {
      name: "Q4 (Oct–Dec)",
      freedom: "PayPal, McDonald's, Pet Shops & Vet Services & Charities",
      discover: "Restaurants, Entertainment & Utilities / TV / Internet / Phone Bills",
    },
  };
  const info = quarterThemes[q] || quarterThemes[4];

  const advisorItems = appState.merchant_advisor || [];
  const freedomItem = advisorItems.find(
    (m) => m.is_rotating_now && m.linked_perk && (m.portal_url || "").includes("chasebonus.com")
  );
  const discoverItem = advisorItems.find(
    (m) => m.is_rotating_now && m.linked_perk && (m.portal_url || "").includes("discover.com")
  );

  const renderPerkActionHtml = (lp, cardShortName) => {
    if (!lp) {
      return `<span class="badge badge-activated">✓ Active ${info.name} ($1,500/qtr cap)</span>`;
    }
    if (lp.activation_done) {
      return `
        <span class="badge badge-activated spotlight-status-badge">
          ✓ ${info.name} Activated (${fmtCurrency(lp.remaining_amount)} / ${fmtCurrency(lp.max_amount)} left)
        </span>
      `;
    }
    return `
      <button type="button" class="btn btn-sm btn-warning-glow js-spotlight-activate" data-perk-id="${lp.perk_id}">
        ⚠️ Activate ${cardShortName} 5% (${fmtCurrency(lp.remaining_amount)} cap)
      </button>
    `;
  };

  const boaChoiceKey = appState.boa_choice_category || "home_improvement";
  const boaChoiceObj = (appState.boa_choice_options || []).find((o) => o.key === boaChoiceKey);
  const boaChoiceLabel = boaChoiceObj ? boaChoiceObj.label : "Home Improvement & Furnishings";

  const boaSelectOptionsHtml = [
    { key: "home_improvement", text: "🔨 3% Home Improvement & Furnishings" },
    { key: "online_shopping", text: "🛍️ 3% Online Shopping (Cable/Net/Phone)" },
    { key: "gas", text: "⛽ 3% Gas & EV Charging Stations" },
    { key: "dining", text: "🍽️ 3% Dining (Restaurants & Takeout)" },
    { key: "travel", text: "✈️ 3% Travel (Flights, Hotels, Transit)" },
    { key: "drug_stores", text: "💊 3% Drug Stores & Pharmacies" },
  ]
    .map(
      (opt) =>
        `<option value="${opt.key}" ${opt.key === boaChoiceKey ? "selected" : ""}>${opt.text}</option>`
    )
    .join("");

  const spotlight = document.getElementById("quarterSpotlight");
  spotlight.innerHTML = `
    <div class="spotlight-header-bar">
      <div class="spotlight-header-title">
        <span class="spotlight-tag">🔥 ACTIVE ${info.name} ROTATING 5% &amp; 3% CHOICE BONUSES</span>
        <span class="spotlight-header-sub">Quarterly $1,500/qtr 5% caps &amp; $2,500/qtr BoA 3%/2% cap across your portfolio</span>
      </div>
      <div class="spotlight-monthly-pills">
        <span class="spotlight-mini-pill">🚗 $10/mo CSR Lyft</span>
        <span class="spotlight-mini-pill">🍔 $35/mo CSR DoorDash</span>
        <span class="spotlight-mini-pill">🎬 $7/mo BCE Disney+</span>
        <span class="spotlight-mini-pill">⚡ EveryDay 20-Swipe +20%</span>
      </div>
    </div>

    <div class="spotlight-cards-grid">
      <!-- Box 1: Chase Freedom -->
      <div class="spotlight-subcard">
        <div class="spotlight-subcard-top">
          <div class="spotlight-subcard-head">
            <span class="spotlight-card-name">Chase Freedom®</span>
            <span class="spotlight-rate-pill">5x UR / 5% (~10% w/ CSR)</span>
          </div>
          <p class="spotlight-subcard-cats">${info.freedom}</p>
          <span class="spotlight-subcard-meta">Up to $1,500 combined spend in ${info.name} ($75 / 7,500 UR max)</span>
        </div>
        <div class="spotlight-subcard-footer">
          ${renderPerkActionHtml(freedomItem?.linked_perk, "Chase")}
          <a href="https://www.chasebonus.com/" target="_blank" rel="noopener" class="btn btn-sm btn-outline spotlight-portal-btn">
            🔗 Chase 5% Portal ↗
          </a>
        </div>
      </div>

      <!-- Box 2: Discover it Cash Back -->
      <div class="spotlight-subcard">
        <div class="spotlight-subcard-top">
          <div class="spotlight-subcard-head">
            <span class="spotlight-card-name">Discover it® Cash Back</span>
            <span class="spotlight-rate-pill">5% Cash Back</span>
          </div>
          <p class="spotlight-subcard-cats">${info.discover}</p>
          <span class="spotlight-subcard-meta">Up to $1,500 combined spend in ${info.name} ($75 cash back max)</span>
        </div>
        <div class="spotlight-subcard-footer">
          ${renderPerkActionHtml(discoverItem?.linked_perk, "Discover")}
          <a href="https://www.discover.com/credit-cards/cash-back/cashback-calendar.html" target="_blank" rel="noopener" class="btn btn-sm btn-outline spotlight-portal-btn">
            🔗 Discover 5% Portal ↗
          </a>
        </div>
      </div>

      <!-- Box 3: Bank of America Customized Cash Rewards -->
      <div class="spotlight-subcard">
        <div class="spotlight-subcard-top">
          <div class="spotlight-subcard-head">
            <span class="spotlight-card-name">BoA Customized Cash Rewards</span>
            <span class="spotlight-rate-pill spotlight-rate-pill-blue">★ 3% Choice + 2% Grocery/Costco</span>
          </div>
          <p class="spotlight-subcard-cats">
            ★ 3% Active: <strong style="color:#38bdf8">${boaChoiceLabel}</strong>
            <span style="color:#94a3b8;font-weight:500"> + 2% Grocery &amp; Wholesale Clubs (Costco Visa)</span>
          </p>
          <span class="spotlight-subcard-meta">Up to $2,500/qtr combined cap ($75–$131.25/qtr) • Can change 1x/month</span>
        </div>
        <div class="spotlight-subcard-footer">
          <select id="boaChoiceSelect" class="spotlight-boa-select" aria-label="Select Bank of America 3% Choice Category" title="Switch your active BoA 3% Choice Category">
            ${boaSelectOptionsHtml}
          </select>
          <a href="https://www.bankofamerica.com/credit-cards/products/cash-back-credit-card/" target="_blank" rel="noopener" class="btn btn-sm btn-outline spotlight-portal-btn" title="Open Bank of America Customized Cash Rewards page to change your 3% category in Online Banking">
            🔗 BoA 3% Portal ↗
          </a>
        </div>
      </div>
    </div>
  `;

  spotlight.querySelectorAll(".js-spotlight-activate").forEach((actBtn) => {
    actBtn.addEventListener("click", async () => {
      const pid = Number(actBtn.dataset.perkId);
      appState = await apiCall(`/api/perks/${pid}`, {
        method: "PUT",
        body: JSON.stringify({
          activation_done: true,
          year: currentYear,
          return_card_id: appState.active_card.id,
        }),
      });
      renderAll();
    });
  });

  const boaSelectEl = document.getElementById("boaChoiceSelect");
  if (boaSelectEl) {
    boaSelectEl.addEventListener("change", async (e) => {
      appState = await apiCall("/api/boa-choice-category", {
        method: "POST",
        body: JSON.stringify({
          choice_category: e.target.value,
          year: currentYear,
          card_id: appState?.active_card?.id,
        }),
      });
      renderAll();
      if (currentSearchQuery.trim().length >= 2) {
        runVendorLookup(currentSearchQuery);
      }
    });
  }
}

function renderMerchantAdvisorGrid() {
  const container = document.getElementById("advisorCardsGrid");
  container.innerHTML = "";
  const items = appState.merchant_advisor || [];
  const q = currentSearchQuery.trim().toLowerCase();

  const filtered = items.filter((item) => {
    if (currentAdvisorGroup === "rotating" && !item.is_rotating_now) return false;
    if (currentAdvisorGroup === "needs_activation") {
      const needsAct =
        (item.linked_perk && item.linked_perk.requires_activation && !item.linked_perk.activation_done) ||
        item.special_activation.includes("MUST ACTIVATE") ||
        item.special_activation.includes("require");
      if (!needsAct) return false;
    } else if (
      !["all", "rotating", "needs_activation"].includes(currentAdvisorGroup) &&
      item.group !== currentAdvisorGroup
    ) {
      return false;
    }

    if (!q) return true;
    const haystack = `${item.title} ${item.merchants} ${item.best_card} ${item.runner_up} ${item.why} ${item.special_activation}`.toLowerCase();
    return haystack.includes(q);
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="perk-card" style="grid-column: 1 / -1">
        <h3>No matching store or bill found for "${currentSearchQuery}"</h3>
        <p class="perk-caveats" style="margin-top:0.35rem">
          For any general uncategorized store, swipe <strong>Bank of America Travel Rewards (1.5x–2.62x)</strong>, <strong>Chase Freedom via PayPal (5x UR in Q4)</strong>, or <strong>Amex EveryDay (1.2x MR when hitting 20 monthly swipes)</strong>.
        </p>
      </div>
    `;
    return;
  }

  for (const item of filtered) {
    const card = document.createElement("article");
    card.className = `advisor-card ${item.is_rotating_now ? "rotating-highlight" : ""}`;

    const lp = item.linked_perk;
    let liveTrackerHtml = "";
    if (lp) {
      const actBadge = lp.requires_activation
        ? lp.activation_done
          ? `<button type="button" class="badge badge-activated advisor-act-btn" data-perk-id="${lp.perk_id}" data-act="0" title="Click to toggle activation status">✓ Perk Activated</button>`
          : `<button type="button" class="badge badge-activation advisor-act-btn" data-perk-id="${lp.perk_id}" data-act="1" title="Click once you've activated this in the card portal">⚠️ Needs Activation — Click When Done</button>`
        : `<span class="badge badge-activated">✓ Auto-Applied</span>`;

      liveTrackerHtml = `
        <div class="advisor-tracker-footer">
          <div class="tracker-cap-pill">
            <span>📊 <strong>${lp.period_label}:</strong> ${fmtCurrency(lp.remaining_amount)} left of ${fmtCurrency(lp.max_amount)}</span>
          </div>
          <div class="tracker-footer-btns">
            ${actBadge}
            <button type="button" class="btn btn-sm btn-outline advisor-jump-btn" data-card-id="${lp.card_id}" data-perk-id="${lp.perk_id}">
              Log Spend →
            </button>
          </div>
        </div>
      `;
    }

    const validityBadge = item.is_rotating_now
      ? `<span class="badge badge-rotating">${item.validity_badge || "🔥 Active 5% Quarter"}</span>`
      : `<span class="badge badge-activated">${item.validity_badge || "🟢 Valid Now"}</span>`;

    card.innerHTML = `
      <div class="advisor-card-header">
        <div class="advisor-badge-row">
          ${validityBadge}
        </div>
        <div class="advisor-title-wrap">
          <span class="advisor-icon">${item.icon}</span>
          <div class="advisor-title-text">
            <h3 class="advisor-title">${item.title}</h3>
            <p class="advisor-merchants">${item.merchants}</p>
          </div>
        </div>
      </div>

      <div class="advisor-winner-box">
        <div class="winner-top">
          <span class="winner-label">🏆 USE THIS CARD</span>
          <span class="winner-rate">${item.best_rate}</span>
        </div>
        <div class="winner-card-name">${item.best_card}</div>
        <div class="winner-runnerup"><strong>Runner-Up / Backup:</strong> ${item.runner_up}</div>
      </div>

      <p class="advisor-why">${item.why}</p>

      <div class="advisor-activation-box">
        <div class="act-box-header">
          <span>⚡ Special Activations &amp; Offer Stacking</span>
          <a href="${item.portal_url}" target="_blank" rel="noopener" class="portal-link">Open Issuer Portal ↗</a>
        </div>
        <p>${item.special_activation}</p>
      </div>

      ${liveTrackerHtml}
    `;

    // Bind activation toggle right from the landing page
    const actBtn = card.querySelector(".advisor-act-btn");
    if (actBtn) {
      actBtn.addEventListener("click", async () => {
        const pid = Number(actBtn.dataset.perkId);
        const newAct = actBtn.dataset.act === "1";
        appState = await apiCall(`/api/perks/${pid}`, {
          method: "PUT",
          body: JSON.stringify({
            activation_done: newAct,
            year: currentYear,
            return_card_id: appState.active_card.id,
          }),
        });
        renderAll();
      });
    }

    // Bind jump to tracker bubble
    const jumpBtn = card.querySelector(".advisor-jump-btn");
    if (jumpBtn) {
      jumpBtn.addEventListener("click", async () => {
        const cid = Number(jumpBtn.dataset.cardId);
        const pid = Number(jumpBtn.dataset.perkId);
        appState = await apiCall(`/api/cards/${cid}`, {
          method: "PUT",
          body: JSON.stringify({ set_active: true, year: currentYear }),
        });
        switchPrimaryView("tracker");
        renderAll();
        setTimeout(() => {
          const el = document.getElementById(`perk-card-${pid}`);
          if (el) {
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            el.style.borderColor = "#38bdf8";
            setTimeout(() => (el.style.borderColor = ""), 1800);
          }
        }, 80);
      });
    }

    container.appendChild(card);
  }
}

function renderUpcomingRotations() {
  const container = document.getElementById("upcomingRotationsGrid");
  if (!container) return;
  container.innerHTML = "";

  const upcomingItems = appState.upcoming_rotations || [];
  for (const item of upcomingItems) {
    const card = document.createElement("article");
    card.className = "upcoming-card";
    card.innerHTML = `
      <div class="upcoming-card-top">
        <div class="upcoming-title-wrap">
          <span class="advisor-icon">${item.icon || "🗓️"}</span>
          <div>
            <span class="badge badge-lifestyle">${item.cadence_badge || item.cadence || "Upcoming"}</span>
            <h3 class="upcoming-title">${item.title}</h3>
          </div>
        </div>
      </div>

      <div class="upcoming-highlight-box">
        <div class="upcoming-card-label">💳 ${item.cards || item.card || ""}</div>
        <div class="upcoming-highlight-text">${item.highlight}</div>
      </div>

      <p class="advisor-why">${item.description || item.details || ""}</p>

      <div class="upcoming-footer">
        <span>⚡ <strong>Action / Prep:</strong> ${item.action_note || item.action_required || ""}</span>
      </div>
    `;
    container.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// View 2: Perks & Bubbles Tracker Rendering
// ---------------------------------------------------------------------------
function renderWalletStrip() {
  const strip = document.getElementById("walletStrip");
  strip.innerHTML = "";
  const cardsSummary = appState.wallet_overview?.cards || [];

  for (const c of cardsSummary) {
    if (c.is_legacy_duplicate && c.id !== appState.active_card.id) continue;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `wallet-pill ${c.id === appState.active_card.id ? "active" : ""}`;
    const shortFee = c.annual_fee > 0 ? `$${c.annual_fee}` : "$0 AF";
    const warnDot =
      c.unactivated_count > 0
        ? `<span class="pill-warn" title="${c.unactivated_count} perk(s) need activation">⚠️</span>`
        : "";
    btn.innerHTML = `
      <span class="pill-name">${c.name.replace(" (Refreshed $795)", "")}</span>
      <span class="pill-meta">${fmtCurrency(c.counted_used)} / ${fmtCurrency(c.counted_max)} • ${shortFee}</span>
      ${warnDot}
    `;
    btn.addEventListener("click", async () => {
      appState = await apiCall(`/api/cards/${c.id}`, {
        method: "PUT",
        body: JSON.stringify({ set_active: true, year: currentYear }),
      });
      renderAll();
    });
    strip.appendChild(btn);
  }
}

function renderEarningBanner() {
  const banner = document.getElementById("earningBanner");
  const txt = appState.active_card.earning_summary;
  if (!txt) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  document.getElementById("earningSummaryText").textContent = txt;
}

function renderRoiHero() {
  const s = appState.summary;
  const card = appState.active_card;

  document.getElementById("metricUsed").textContent = `$${s.counted_used.toFixed(2)}`;
  document.getElementById("metricUsedSub").textContent = `of $${s.counted_max.toFixed(2)} (${s.total_utilization_pct}% utilized)`;

  document.getElementById("metricFee").textContent = `$${s.effective_annual_fee.toFixed(2)}`;
  const auInfo = card.au_count > 0 ? ` (+${card.au_count} AU)` : "";
  document.getElementById("metricFeeSub").textContent =
    s.effective_annual_fee === 0
      ? `$0 Annual Fee Keeper Card`
      : `Anniversary: ${card.anniversary_date}${auInfo}`;

  const netEl = document.getElementById("metricNet");
  const netSubEl = document.getElementById("metricNetSub");
  const netCard = document.getElementById("netValueCard");

  if (s.net_value >= 0) {
    netEl.textContent = `+$${s.net_value.toFixed(2)}`;
    netEl.style.color = "#34d399";
    netSubEl.textContent =
      s.effective_annual_fee === 0
        ? `✅ Pure net profit ($0 Annual Fee)`
        : `✅ Annual fee 100% beaten! (${s.breakeven_pct}% of AF)`;
    netCard.classList.add("positive-roi");
  } else {
    netEl.textContent = `-$${Math.abs(s.net_value).toFixed(2)}`;
    netEl.style.color = "#f87171";
    netSubEl.textContent = `$${s.amount_to_breakeven.toFixed(2)} more needed to break even`;
    netCard.classList.remove("positive-roi");
  }

  document.getElementById("metricAllMax").textContent = fmtCurrency(s.all_perks_max);
  document.getElementById("metricAllSub").textContent = `${fmtCurrency(s.hard_credits_max)} hard credits/caps + ${fmtCurrency(s.lifestyle_max)} perks`;

  const maxScale = Math.max(s.counted_max, s.effective_annual_fee, 1);
  const fillPct = Math.min(100, (s.counted_used / maxScale) * 100);
  const markerEl = document.getElementById("breakevenMarker");

  document.getElementById("breakevenFill").style.width = `${fillPct}%`;

  if (s.effective_annual_fee > 0) {
    markerEl.hidden = false;
    const markerPct = Math.min(98, Math.max(2, (s.effective_annual_fee / maxScale) * 100));
    markerEl.style.left = `${markerPct}%`;
    document.getElementById("breakevenMarkerLabel").textContent = `Breakeven ${fmtCurrency(s.effective_annual_fee)}`;
    document.getElementById("breakevenBadge").textContent = `${s.breakeven_pct}% of Annual Fee Recovered`;
  } else {
    markerEl.hidden = true;
    document.getElementById("breakevenBadge").textContent = `${s.total_utilization_pct}% of Annual Rewards Claimed ($0 AF)`;
  }

  document.getElementById("toggleLifestyleRoi").checked = Boolean(s.include_lifestyle_in_roi);
  document.getElementById("unactivatedCountBadge").textContent = String(s.unactivated_count);

  const pillsContainer = document.getElementById("categoryBreakdownPills");
  pillsContainer.innerHTML = "";
  for (const [cat, vals] of Object.entries(s.category_breakdown)) {
    const pill = document.createElement("span");
    pill.className = "cat-pill";
    pill.innerHTML = `${cat}: <strong>${fmtCurrency(vals.used)}</strong> / ${fmtCurrency(vals.max)}`;
    pillsContainer.appendChild(pill);
  }
}

function renderExpiringBanner() {
  const banner = document.getElementById("expiringBanner");
  const chipsContainer = document.getElementById("expiringChips");
  const alerts = appState.expiring_alerts || [];

  if (alerts.length === 0) {
    banner.hidden = true;
    return;
  }

  banner.hidden = false;
  document.getElementById("expiringTitle").textContent = `${alerts.length} Credit${alerts.length > 1 ? "s" : ""} / Rotating Cap${alerts.length > 1 ? "s" : ""} Active & Expiring in Current Period`;
  chipsContainer.innerHTML = "";

  for (const a of alerts) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `expiring-chip ${a.urgency === "critical" ? "critical" : ""}`;
    chip.innerHTML = `
      <span><strong>${fmtCurrency(a.remaining_amount)}</strong> ${a.perk_name} (${a.period_label})</span>
      <span class="chip-days">${a.days_left === 0 ? "Expires today!" : `${a.days_left}d left`}</span>
    `;
    chip.addEventListener("click", () => {
      const el = document.getElementById(`perk-card-${a.perk_id}`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.style.borderColor = "#38bdf8";
        setTimeout(() => (el.style.borderColor = ""), 1800);
      }
    });
    chipsContainer.appendChild(chip);
  }
}

function renderPerksList() {
  const container = document.getElementById("perksList");
  container.innerHTML = "";

  const filtered = appState.perks.filter((p) => {
    if (currentFilter === "all") return true;
    if (currentFilter === "monthly") return p.frequency === "monthly";
    if (currentFilter === "semi_annual") {
      return [
        "semi_annual",
        "semi_annual_bookings",
        "quarterly",
        "q1_only",
        "q2_only",
        "q3_only",
        "q4_only",
      ].includes(p.frequency);
    }
    if (currentFilter === "annual_group") {
      return ["annual", "anniversary", "quadrennial"].includes(p.frequency);
    }
    if (currentFilter === "unactivated") {
      return p.requires_activation && !p.activation_done;
    }
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = `<div class="perk-card"><p class="perk-caveats">No perks match the current filter.</p></div>`;
    return;
  }

  const activePerks = filtered.filter((p) => !p.is_expired_perk);
  const expiredPerks = filtered.filter((p) => p.is_expired_perk);

  const buildPerkElement = (perk) => {
    const cardEl = document.createElement("article");
    cardEl.className = `perk-card ${perk.is_enabled ? "" : "perk-disabled"} ${perk.is_expired_perk ? "perk-expired-quarter" : ""}`;
    cardEl.id = `perk-card-${perk.id}`;

    const isWide = perk.frequency !== "monthly";
    const isSwipe = isSwipeCounterPerk(perk);

    let activationHtml = "";
    if (perk.is_expired_perk) {
      activationHtml = `<span class="badge badge-expired-qtr">Ended (Past Quarter)</span>`;
    } else if (perk.requires_activation) {
      if (perk.activation_done) {
        activationHtml = `<span class="badge badge-activated" data-toggle-activate="${perk.id}" title="Click to toggle activation status">✓ Activated</span>`;
      } else {
        activationHtml = `<span class="badge badge-activation" data-toggle-activate="${perk.id}" title="Click to mark as activated in issuer portal">⚠️ Needs Activation</span>`;
      }
    }

    const typeBadgeHtml =
      perk.credit_type === "lifestyle_perk"
        ? `<span class="badge badge-lifestyle">Lifestyle / Ecosystem Perk</span>`
        : "";

    const tallyText = isSwipe
      ? `${Math.round(perk.annual_used)} / ${Math.round(perk.annual_max)} swipes`
      : `${fmtCurrency(perk.annual_used)} <span style="font-weight:400;color:#94a3b8;font-size:0.9rem">/ ${fmtCurrency(perk.annual_max)}</span>`;

    cardEl.innerHTML = `
      <div class="perk-top">
        <div class="perk-title-group">
          <div class="perk-title-row">
            <span class="perk-title">${perk.name}</span>
            <span class="badge badge-cat">${perk.category}</span>
            ${typeBadgeHtml}
            ${activationHtml}
          </div>
          <p class="perk-caveats">${perk.caveats || ""}</p>
        </div>

        <div class="perk-stats-right">
          <div class="perk-tally">
            <div class="perk-tally-amount" style="color: ${
              perk.annual_used >= perk.annual_max - 0.01
                ? "#34d399"
                : perk.annual_used > 0
                ? "#38bdf8"
                : "#f1f5f9"
            }">
              ${tallyText}
            </div>
            <div class="perk-tally-sub">${perk.utilization_pct}% completed</div>
          </div>
          <button class="btn btn-sm btn-outline" data-edit-perk="${perk.id}" title="Edit or disable perk">⚙️</button>
        </div>
      </div>

      <div class="bubbles-grid ${isWide ? "wide-bubbles" : ""}" id="bubbles-${perk.id}"></div>
    `;

    const bubblesGrid = cardEl.querySelector(`#bubbles-${perk.id}`);

    for (const period of perk.periods) {
      const bubble = document.createElement("div");
      bubble.className = `period-bubble status-${period.status} ${period.is_current ? "is-current" : ""}`;
      bubble.title = `${period.full_label}: ${fmtPerkUnit(perk, period.used_amount)} / ${fmtPerkUnit(perk, period.max_amount)}${period.notes ? ` — Note: ${period.notes}` : ""}\nLeft-click to toggle 100%, right-click or click ✎ for partial progress.`;

      const checkIcon = period.status === "completed" ? "✓" : period.status === "partial" ? "½" : "";
      const amtLabel =
        period.status === "partial"
          ? `${fmtPerkUnit(perk, period.used_amount)} / ${fmtPerkUnit(perk, period.max_amount)}`
          : `${fmtPerkUnit(perk, period.used_amount > 0 ? period.used_amount : period.max_amount)}`;

      bubble.innerHTML = `
        <div class="bubble-top">
          <span>${period.label}</span>
          <span class="bubble-check">${checkIcon}</span>
        </div>
        <div class="bubble-amount">${amtLabel}</div>
        ${period.notes ? `<div class="bubble-note-indicator">📝 ${period.notes}</div>` : ""}
        <button type="button" class="bubble-edit-btn" title="Set partial progress or add note">✎</button>
      `;

      bubble.addEventListener("click", async (e) => {
        if (e.target.closest(".bubble-edit-btn")) return;
        appState = await apiCall("/api/usage", {
          method: "POST",
          body: JSON.stringify({
            perk_id: perk.id,
            year: currentYear,
            period_key: period.key,
            mode: "toggle",
          }),
        });
        renderAll();
      });

      const openPartialModal = (e) => {
        e.preventDefault();
        e.stopPropagation();
        openUsageModal(perk, period);
      };
      bubble.addEventListener("contextmenu", openPartialModal);
      bubble.querySelector(".bubble-edit-btn").addEventListener("click", openPartialModal);

      bubblesGrid.appendChild(bubble);
    }

    const actBtn = cardEl.querySelector("[data-toggle-activate]");
    if (actBtn) {
      actBtn.addEventListener("click", async () => {
        appState = await apiCall(`/api/perks/${perk.id}`, {
          method: "PUT",
          body: JSON.stringify({
            activation_done: !perk.activation_done,
            year: currentYear,
          }),
        });
        renderAll();
      });
    }

    cardEl.querySelector("[data-edit-perk]").addEventListener("click", () => {
      openPerkModal(perk);
    });

    return cardEl;
  };

  for (const perk of activePerks) {
    container.appendChild(buildPerkElement(perk));
  }

  if (expiredPerks.length > 0) {
    const detailsEl = document.createElement("details");
    detailsEl.className = "expired-perks-details";
    detailsEl.innerHTML = `
      <summary class="expired-perks-summary">
        <span>🗂️ Past / Expired Rotating Quarters (${expiredPerks.length}) — Historical Log Only</span>
        <span class="expired-perks-hint">Click to expand past Q1/Q2/Q3 logs</span>
      </summary>
      <div class="expired-perks-list"></div>
    `;
    const expList = detailsEl.querySelector(".expired-perks-list");
    for (const perk of expiredPerks) {
      expList.appendChild(buildPerkElement(perk));
    }
    container.appendChild(detailsEl);
  }
}

// ---------------------------------------------------------------------------
// Modal Logic
// ---------------------------------------------------------------------------
function openUsageModal(perk, period) {
  const isSwipe = isSwipeCounterPerk(perk);
  document.getElementById("usagePerkId").value = perk.id;
  document.getElementById("usagePeriodKey").value = period.key;
  document.getElementById("usageModalTitle").textContent = `${perk.name} — ${period.label}`;
  document.getElementById("usageModalSubtitle").textContent = `Period: ${period.full_label} (Max: ${fmtPerkUnit(perk, period.max_amount)})`;
  document.getElementById("usageAmountLabel").textContent = isSwipe
    ? "Transactions / Swipes Made This Month (Goal: 20)"
    : "Amount Used / Claimed ($)";

  const amtInput = document.getElementById("usageAmountInput");
  const slider = document.getElementById("usageAmountSlider");
  amtInput.step = isSwipe ? "1" : "0.01";
  slider.step = isSwipe ? "1" : "0.5";
  amtInput.max = period.max_amount;
  amtInput.value = period.used_amount;
  slider.max = period.max_amount;
  slider.value = period.used_amount;

  document.getElementById("usageDateInput").value =
    period.used_date || new Date().toISOString().slice(0, 10);
  document.getElementById("usageNotesInput").value = period.notes || "";

  document.getElementById("modalUsage").hidden = false;
}

function openPerkModal(perk = null) {
  const isEdit = Boolean(perk);
  document.getElementById("perkModalTitle").textContent = isEdit ? "Edit Perk" : "Add Custom Perk";
  document.getElementById("editPerkId").value = isEdit ? perk.id : "";
  document.getElementById("perkNameInput").value = isEdit ? perk.name : "";
  document.getElementById("perkCategoryInput").value = isEdit ? perk.category : "Travel";
  document.getElementById("perkTypeInput").value = isEdit ? perk.credit_type : "hard_credit";
  document.getElementById("perkFreqInput").value = isEdit ? perk.frequency : "monthly";
  document.getElementById("perkAmountInput").value = isEdit ? perk.period_amount : 15;
  document.getElementById("perkCaveatsInput").value = isEdit ? perk.caveats : "";
  document.getElementById("perkReqActivation").checked = isEdit ? Boolean(perk.requires_activation) : false;
  document.getElementById("perkActivationDone").checked = isEdit ? Boolean(perk.activation_done) : true;
  document.getElementById("btnDeletePerk").hidden = !isEdit;

  document.getElementById("modalPerk").hidden = false;
}

function bindEvents() {
  // Primary mode switcher
  document.getElementById("tabBtnAdvisor").addEventListener("click", () => switchPrimaryView("advisor"));
  document.getElementById("tabBtnTracker").addEventListener("click", () => switchPrimaryView("tracker"));

  // Live Issuer Website Sync button
  const syncBtn = document.getElementById("btnSyncIssuers");
  syncBtn.addEventListener("click", async () => {
    const origText = syncBtn.textContent;
    syncBtn.textContent = "⏳ Querying Websites...";
    syncBtn.disabled = true;
    try {
      appState = await apiCall("/api/sync-issuers", {
        method: "POST",
        body: JSON.stringify({ year: currentYear, card_id: appState?.active_card?.id }),
      });
      renderAll();
      document.getElementById("syncSourcesDrawer").hidden = false;
    } finally {
      syncBtn.textContent = origText;
      syncBtn.disabled = false;
    }
  });

  // Toggle Live Sources Drawer
  document.getElementById("btnToggleSyncDetails").addEventListener("click", () => {
    const drawer = document.getElementById("syncSourcesDrawer");
    drawer.hidden = !drawer.hidden;
  });

  // Merchant Search Input + Live Vendor Lookup
  const searchInput = document.getElementById("merchantSearchInput");
  const clearBtn = document.getElementById("btnClearSearch");
  const liveBox = document.getElementById("liveVendorResultBox");
  let lookupTimer = null;

  async function runVendorLookup(queryStr) {
    const q = queryStr.trim();
    if (q.length < 2) {
      liveBox.hidden = true;
      return;
    }
    const res = await apiCall(`/api/vendor-lookup?q=${encodeURIComponent(q)}`);
    if (!res || !res.best_card) {
      liveBox.hidden = true;
      return;
    }
    let matchesHtml = "";
    if (res.scraped_matches && res.scraped_matches.length > 0) {
      const itemsHtml = res.scraped_matches
        .slice(0, 5)
        .map((m) => {
          const badgeClass =
            m.rule_type === "excluded"
              ? "badge-activation"
              : m.rule_type === "special"
              ? "badge-lifestyle"
              : "badge-activated";
          const badgeLabel =
            m.rule_type === "excluded"
              ? "⚠️ Official Exclusion"
              : m.rule_type === "special"
              ? "★ Special Rule"
              : "✓ Official Issuer List";
          return `
            <div class="scraped-match-item">
              <div>
                <strong>${m.vendor_name}</strong>
                <span class="badge ${badgeClass}">${badgeLabel} (${m.source_issuer})</span>
              </div>
              <p>${m.official_rule}</p>
            </div>
          `;
        })
        .join("");
      matchesHtml = `
        <div class="scraped-matches-list">
          <div class="scraped-list-title">🌐 Direct Matches Scraped from Official Issuer Websites:</div>
          ${itemsHtml}
        </div>
      `;
    } else if (res.web_snippet) {
      matchesHtml = `
        <div class="scraped-matches-list">
          <div class="scraped-list-title">🌐 Live Web Merchant Classification for "${res.query}":</div>
          <p class="perk-caveats">${res.web_snippet}</p>
        </div>
      `;
    }

    liveBox.hidden = false;
    liveBox.innerHTML = `
      <div class="live-vendor-header">
        <div>
          <span class="badge badge-cat">Live Lookup: ${res.detected_category}</span>
          <strong style="margin-left:0.5rem;font-size:1.02rem">Verdict for "${res.query}"</strong>
        </div>
        <span class="winner-rate">${res.best_rate}</span>
      </div>
      <div class="live-vendor-winner">
        🏆 <strong>Best Card to Swipe:</strong> <span style="color:#38bdf8;font-weight:800">${res.best_card}</span>
        <span style="color:#94a3b8;font-size:0.8rem;margin-left:0.5rem">(Backup: ${res.runner_up})</span>
      </div>
      <p class="perk-caveats">⚡ <strong>Activation / Stacking Tip:</strong> ${res.activation_tip}</p>
      ${matchesHtml}
    `;
  }

  searchInput.addEventListener("input", () => {
    currentSearchQuery = searchInput.value;
    clearBtn.hidden = !currentSearchQuery;
    renderMerchantAdvisorGrid();
    clearTimeout(lookupTimer);
    if (currentSearchQuery.trim().length >= 2) {
      lookupTimer = setTimeout(() => runVendorLookup(currentSearchQuery), 260);
    } else {
      liveBox.hidden = true;
    }
  });

  document.getElementById("btnLiveVendorLookup").addEventListener("click", () => {
    runVendorLookup(searchInput.value);
  });

  clearBtn.addEventListener("click", () => {
    searchInput.value = "";
    currentSearchQuery = "";
    clearBtn.hidden = true;
    liveBox.hidden = true;
    renderMerchantAdvisorGrid();
  });

  // Advisor Group Filter Tabs
  document.querySelectorAll("#advisorGroupTabs .filter-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll("#advisorGroupTabs .filter-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      currentAdvisorGroup = tab.dataset.group;
      renderMerchantAdvisorGrid();
    });
  });

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(btn.dataset.close).hidden = true;
    });
  });

  document.getElementById("cardSelect").addEventListener("change", async (e) => {
    const cid = Number(e.target.value);
    appState = await apiCall(`/api/cards/${cid}`, {
      method: "PUT",
      body: JSON.stringify({ set_active: true, year: currentYear }),
    });
    renderAll();
  });

  document.getElementById("yearSelect").addEventListener("change", async (e) => {
    currentYear = Number(e.target.value);
    await loadState(appState.active_card.id);
  });

  document.getElementById("toggleLifestyleRoi").addEventListener("change", async (e) => {
    appState = await apiCall(`/api/cards/${appState.active_card.id}`, {
      method: "PUT",
      body: JSON.stringify({
        include_lifestyle_in_roi: e.target.checked,
        year: currentYear,
      }),
    });
    renderAll();
  });

  document.querySelectorAll("#frequencyTabs .filter-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll("#frequencyTabs .filter-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      currentFilter = tab.dataset.filter;
      renderPerksList();
    });
  });

  const amtInput = document.getElementById("usageAmountInput");
  const slider = document.getElementById("usageAmountSlider");
  amtInput.addEventListener("input", () => (slider.value = amtInput.value));
  slider.addEventListener("input", () => (amtInput.value = slider.value));
  document.getElementById("btnUsageMax").addEventListener("click", () => {
    amtInput.value = amtInput.max;
    slider.value = amtInput.max;
  });
  document.getElementById("btnUsageZero").addEventListener("click", () => {
    amtInput.value = 0;
    slider.value = 0;
  });

  document.getElementById("formUsage").addEventListener("submit", async (e) => {
    e.preventDefault();
    appState = await apiCall("/api/usage", {
      method: "POST",
      body: JSON.stringify({
        perk_id: Number(document.getElementById("usagePerkId").value),
        year: currentYear,
        period_key: document.getElementById("usagePeriodKey").value,
        mode: "set",
        used_amount: Number(amtInput.value),
        used_date: document.getElementById("usageDateInput").value,
        notes: document.getElementById("usageNotesInput").value,
      }),
    });
    document.getElementById("modalUsage").hidden = true;
    renderAll();
  });

  document.getElementById("btnCardSettings").addEventListener("click", () => {
    const c = appState.active_card;
    document.getElementById("cardNameInput").value = c.name;
    document.getElementById("cardAnnualFeeInput").value = c.annual_fee;
    document.getElementById("cardAnnivDateInput").value = c.anniversary_date;
    document.getElementById("cardAuFeeInput").value = c.au_fee;
    document.getElementById("cardAuCountInput").value = c.au_count;
    document.getElementById("cardWebhookInput").value = c.webhook_url || "";
    document.getElementById("modalCardSettings").hidden = false;
  });

  document.getElementById("formCardSettings").addEventListener("submit", async (e) => {
    e.preventDefault();
    appState = await apiCall(`/api/cards/${appState.active_card.id}`, {
      method: "PUT",
      body: JSON.stringify({
        name: document.getElementById("cardNameInput").value,
        annual_fee: Number(document.getElementById("cardAnnualFeeInput").value),
        anniversary_date: document.getElementById("cardAnnivDateInput").value,
        au_fee: Number(document.getElementById("cardAuFeeInput").value),
        au_count: Number(document.getElementById("cardAuCountInput").value),
        webhook_url: document.getElementById("cardWebhookInput").value,
        year: currentYear,
      }),
    });
    document.getElementById("modalCardSettings").hidden = true;
    renderAll();
  });

  document.getElementById("btnDeleteCard").addEventListener("click", async () => {
    if (!confirm(`Delete "${appState.active_card.name}"?`)) return;
    try {
      appState = await apiCall(`/api/cards/${appState.active_card.id}`, { method: "DELETE" });
      document.getElementById("modalCardSettings").hidden = true;
      renderAll();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("btnAddPerk").addEventListener("click", () => openPerkModal(null));

  document.getElementById("formPerk").addEventListener("submit", async (e) => {
    e.preventDefault();
    const editId = document.getElementById("editPerkId").value;
    const payload = {
      card_id: appState.active_card.id,
      name: document.getElementById("perkNameInput").value,
      category: document.getElementById("perkCategoryInput").value,
      credit_type: document.getElementById("perkTypeInput").value,
      frequency: document.getElementById("perkFreqInput").value,
      period_amount: Number(document.getElementById("perkAmountInput").value),
      caveats: document.getElementById("perkCaveatsInput").value,
      requires_activation: document.getElementById("perkReqActivation").checked,
      activation_done: document.getElementById("perkActivationDone").checked,
      year: currentYear,
    };

    if (editId) {
      appState = await apiCall(`/api/perks/${editId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
    } else {
      appState = await apiCall("/api/perks", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    }
    document.getElementById("modalPerk").hidden = true;
    renderAll();
  });

  document.getElementById("btnDeletePerk").addEventListener("click", async () => {
    const editId = document.getElementById("editPerkId").value;
    if (!editId || !confirm("Delete this perk?")) return;
    appState = await apiCall(`/api/perks/${editId}`, { method: "DELETE" });
    document.getElementById("modalPerk").hidden = true;
    renderAll();
  });

  const presetSelect = document.getElementById("newCardPresetSelect");
  presetSelect.addEventListener("change", () => {
    document.getElementById("customCardFields").hidden = presetSelect.value !== "custom";
  });

  document.getElementById("btnAddCard").addEventListener("click", () => {
    document.getElementById("modalAddCard").hidden = false;
  });

  document.getElementById("formAddCard").addEventListener("submit", async (e) => {
    e.preventDefault();
    appState = await apiCall("/api/cards", {
      method: "POST",
      body: JSON.stringify({
        preset_key: presetSelect.value,
        name: document.getElementById("newCardCustomName").value || "Custom Card",
        annual_fee: Number(document.getElementById("newCardCustomFee").value || 0),
      }),
    });
    document.getElementById("modalAddCard").hidden = true;
    renderAll();
  });

  document.getElementById("btnDownloadIcs").addEventListener("click", () => {
    window.location.href = `/api/calendar.ics?card_id=${appState.active_card.id}&year=${currentYear}`;
  });

  document.getElementById("btnWebhookDigest").addEventListener("click", async () => {
    const res = await apiCall("/api/webhook/notify", {
      method: "POST",
      body: JSON.stringify({
        card_id: appState.active_card.id,
        year: currentYear,
        send: false,
      }),
    });
    document.getElementById("webhookPreviewText").textContent = res.message;
    document.getElementById("webhookDispatchUrl").value = res.webhook_url || "";
    document.getElementById("webhookStatusMsg").textContent = "";
    document.getElementById("modalWebhook").hidden = false;
  });

  document.getElementById("btnCopyDigest").addEventListener("click", async () => {
    const txt = document.getElementById("webhookPreviewText").textContent;
    await navigator.clipboard.writeText(txt);
    document.getElementById("webhookStatusMsg").textContent = "✅ Copied digest to clipboard!";
  });

  document.getElementById("btnSendWebhookNow").addEventListener("click", async () => {
    const url = document.getElementById("webhookDispatchUrl").value.trim();
    const statusEl = document.getElementById("webhookStatusMsg");
    if (!url) {
      statusEl.textContent = "⚠️ Enter a Webhook URL first (e.g. Discord, Slack, or ntfy.sh).";
      return;
    }
    statusEl.textContent = "Sending...";
    const res = await apiCall("/api/webhook/notify", {
      method: "POST",
      body: JSON.stringify({
        card_id: appState.active_card.id,
        year: currentYear,
        webhook_url: url,
        send: true,
      }),
    });
    if (res.dispatch && res.dispatch.sent) {
      statusEl.textContent = `✅ Delivered! (HTTP ${res.dispatch.status_code})`;
    } else {
      statusEl.textContent = `❌ Failed: ${res.dispatch?.error || res.dispatch?.reason}`;
    }
  });

  document.getElementById("btnExportJson").addEventListener("click", async () => {
    const data = await apiCall("/api/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `credit-card-tracker-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
  });

  document.getElementById("inputImportJson").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    const payload = JSON.parse(text);
    appState = await apiCall("/api/import", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderAll();
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await loadState();
});
