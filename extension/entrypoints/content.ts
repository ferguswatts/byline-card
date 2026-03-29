/**
 * Content script — injected into NZ news sites.
 * Detects journalist bylines and shows hover cards.
 * Pure DOM rendering — no React dependency (keeps bundle small).
 */

import { detectByline } from "../src/lib/detect";
import { matchJournalist, type JournalistData, type DataFile } from "../src/lib/match";
import { loadData } from "../src/lib/data";

const NZ_NEWS_DOMAINS = [
  "nzherald.co.nz",
  "stuff.co.nz",
  "rnz.co.nz",
  "1news.co.nz",
  "newsroom.co.nz",
  "thespinoff.co.nz",
  "interest.co.nz",
];

export default defineContentScript({
  matches: [
    "*://*.nzherald.co.nz/*",
    "*://*.stuff.co.nz/*",
    "*://*.rnz.co.nz/*",
    "*://*.1news.co.nz/*",
    "*://*.newsroom.co.nz/*",
    "*://*.thespinoff.co.nz/*",
    "*://*.interest.co.nz/*",
  ],
  runAt: "document_idle",

  async main() {
    const hostname = window.location.hostname.replace("www.", "");
    if (!NZ_NEWS_DOMAINS.some((d) => hostname.includes(d))) return;

    let data: DataFile;
    try {
      data = await loadData();
    } catch (e) {
      console.error("[Byline Card] Failed to load data:", e);
      return;
    }

    if (!data.journalists || Object.keys(data.journalists).length === 0) {
      console.log("[Byline Card] No journalist data loaded.");
      return;
    }

    const byline = detectByline(document, data.sites || {});
    if (!byline) {
      console.log("[Byline Card] No byline detected on this page.");
      return;
    }

    console.log(`[Byline Card] Byline detected: ${byline.name} @ ${byline.outlet}`);

    const match = matchJournalist(byline.name, byline.outlet, data);
    if (!match) {
      console.log(`[Byline Card] No match for: ${byline.name}`);
      return;
    }

    console.log(`[Byline Card] Matched: ${match.journalist.name}`);

    const bylineEl = findBylineElement(byline.name);
    if (!bylineEl) {
      console.log("[Byline Card] Could not find byline DOM element.");
      return;
    }

    // Add visual indicator
    bylineEl.style.borderBottom = "2px dotted #3b82f6";
    bylineEl.style.cursor = "pointer";
    bylineEl.title = "Byline Card available — hover to see journalist profile";

    let cardEl: HTMLDivElement | null = null;
    let isCardHovered = false;
    let isPinned = false;
    let hoverTimeout: ReturnType<typeof setTimeout> | null = null;

    function showCard() {
      if (cardEl) return;

      cardEl = document.createElement("div");
      cardEl.style.position = "absolute";
      cardEl.style.zIndex = "2147483647";

      const rect = bylineEl!.getBoundingClientRect();
      cardEl.style.left = `${rect.left + window.scrollX}px`;

      if (window.innerHeight - rect.bottom < 400) {
        cardEl.style.top = `${rect.top + window.scrollY - 8}px`;
        cardEl.style.transform = "translateY(-100%)";
      } else {
        cardEl.style.top = `${rect.bottom + window.scrollY + 8}px`;
      }

      const shadow = cardEl.attachShadow({ mode: "open" });
      shadow.innerHTML = buildCardHTML(match!.journalist, data.version);

      document.body.appendChild(cardEl);

      cardEl.addEventListener("mouseenter", () => {
        isCardHovered = true;
        if (hoverTimeout) { clearTimeout(hoverTimeout); hoverTimeout = null; }
      });
      cardEl.addEventListener("mouseleave", () => {
        isCardHovered = false;
        if (!isPinned) scheduleHide();
      });
      cardEl.addEventListener("click", () => { isPinned = true; });
    }

    function hideCard() {
      if (isPinned) return;
      if (cardEl) { cardEl.remove(); cardEl = null; }
      isCardHovered = false;
    }

    function scheduleHide() {
      if (hoverTimeout) clearTimeout(hoverTimeout);
      hoverTimeout = setTimeout(() => {
        if (!isCardHovered && !isPinned) hideCard();
      }, 200);
    }

    let showTimeout: ReturnType<typeof setTimeout> | null = null;
    bylineEl.addEventListener("mouseenter", () => {
      showTimeout = setTimeout(showCard, 300);
    });
    bylineEl.addEventListener("mouseleave", () => {
      if (showTimeout) { clearTimeout(showTimeout); showTimeout = null; }
      scheduleHide();
    });

    bylineEl.setAttribute("tabindex", "0");
    bylineEl.addEventListener("focus", showCard);
    bylineEl.addEventListener("blur", () => {
      setTimeout(() => { if (!isCardHovered && !isPinned) hideCard(); }, 200);
    });

    document.addEventListener("click", (e) => {
      if (isPinned && cardEl && !cardEl.contains(e.target as Node)) {
        isPinned = false; hideCard();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && cardEl) { isPinned = false; hideCard(); }
    });
  },
});

function buildCardHTML(j: JournalistData, version: string): string {
  const dist = j.distribution;
  const maxPct = Math.max(dist.left, dist.centre_left, dist.centre, dist.centre_right, dist.right, 1);

  const confColors: Record<string, { bg: string; text: string }> = {
    low: { bg: "rgba(245,158,11,0.1)", text: "#b45309" },
    medium: { bg: "rgba(107,114,128,0.1)", text: "#4b5563" },
    high: { bg: "rgba(16,185,129,0.1)", text: "#047857" },
  };
  const conf = confColors[j.confidence] || confColors.low;

  const barRow = (label: string, pct: number) =>
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="width:80px;font-size:12px;font-weight:500;color:#444;text-align:right;flex-shrink:0">${label}</span>
      <div style="flex:1;height:8px;background:#f3f4f6;border-radius:4px;overflow:hidden">
        <div style="width:${(pct / maxPct) * 100}%;height:100%;background:#3b82f6;border-radius:4px"></div>
      </div>
      <span style="width:32px;font-size:13px;font-weight:600;color:#1a1a1a;text-align:right;flex-shrink:0">${pct}%</span>
    </div>`;

  const connectionsHTML = j.connections.length > 0
    ? `<div style="padding:12px 16px;border-bottom:1px solid #f0f0f0">
        <div style="font-size:11px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px">Documented connections</div>
        ${j.connections.map((c) =>
          `<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;font-size:13px;line-height:1.4">
            <span style="color:#1a1a1a"><span style="color:#888;font-size:12px">${c.type}:</span> ${c.target}${c.role ? `<span style="color:#666">, ${c.role}</span>` : ""}</span>
            <a href="${c.source}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;font-size:11px;margin-left:8px;flex-shrink:0">source</a>
          </div>`
        ).join("")}
        ${j.facts.map((f) =>
          `<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;font-size:12px;color:#444;margin-top:6px">
            <span>${f.text}</span>
            <a href="${f.source}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;font-size:11px;margin-left:8px;flex-shrink:0">source</a>
          </div>`
        ).join("")}
      </div>`
    : "";

  const lowConfWarning = j.confidence === "low"
    ? `<div style="font-size:11px;color:#b45309;margin-top:4px;padding:4px 8px;background:rgba(245,158,11,0.06);border-radius:4px">Limited data (${j.article_count} articles) — scores may shift</div>`
    : "";

  return `
    <style>
      :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
      @keyframes fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
      @media (prefers-reduced-motion: reduce) { @keyframes fadein { from { opacity:1; } to { opacity:1; } } }
      a:hover { text-decoration: underline !important; }
    </style>
    <div role="dialog" aria-label="Journalist profile: ${j.name}" style="
      width:340px;max-height:480px;overflow-y:auto;background:#fff;border:1px solid #e5e5e5;
      border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.08),0 1px 3px rgba(0,0,0,0.04);
      animation:fadein 150ms ease-out;
    ">
      <!-- Header -->
      <div style="padding:12px 16px;border-bottom:1px solid #f0f0f0">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div>
            <div style="font-size:15px;font-weight:600;color:#1a1a1a">${j.name}</div>
            <div style="font-size:13px;color:#666;margin-top:2px">${j.beat ? j.beat + " · " : ""}${j.outlet}</div>
          </div>
          <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
            <span style="font-size:10px;color:#6b7280;font-weight:500;white-space:nowrap">Byline Card</span>
            ${j.methodology?.includes("demo") ? `<span style="font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;background:#fff3cd;color:#92400e;border:1px solid #fcd34d;letter-spacing:0.3px">DEMO DATA</span>` : ""}
          </div>
        </div>
        <div style="display:flex;gap:8px;margin-top:6px;align-items:center">
          <span style="font-size:12px;color:#666">${j.article_count} articles</span>
          <span style="font-size:11px;font-weight:500;padding:2px 6px;border-radius:4px;background:${conf.bg};color:${conf.text}">${j.confidence} confidence</span>
        </div>
        ${lowConfWarning}
      </div>

      <!-- Distribution chart -->
      <div style="padding:12px 16px;border-bottom:1px solid #f0f0f0">
        <div style="font-size:11px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Coverage distribution</div>
        ${barRow("Left", dist.left)}
        ${barRow("Centre-Left", dist.centre_left)}
        ${barRow("Centre", dist.centre)}
        ${barRow("Centre-Right", dist.centre_right)}
        ${barRow("Right", dist.right)}
      </div>

      <!-- Connections -->
      ${connectionsHTML}

      <!-- Footer -->
      <div style="padding:8px 16px;display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#999">
        <span>AI-scored · Updated ${version}</span>
        <a href="https://github.com/ferguswatts/byline-card" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none">About</a>
      </div>
    </div>
  `;
}

function findBylineElement(name: string): HTMLElement | null {
  const nameLower = name.toLowerCase();

  const authorLinks = document.querySelectorAll('a[rel="author"], .author-name, .byline a');
  for (const el of authorLinks) {
    if (el.textContent?.toLowerCase().includes(nameLower)) {
      return el as HTMLElement;
    }
  }

  const candidates = document.querySelectorAll(
    "article header *, .article-header *, .story-header *, .byline, [class*='author'], [class*='byline']",
  );
  for (const el of candidates) {
    const text = el.textContent?.trim() || "";
    if (text.toLowerCase().includes(nameLower) && text.length < 200) {
      return el as HTMLElement;
    }
  }

  return null;
}
