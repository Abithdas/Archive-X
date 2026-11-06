/* ============================================================================
 * FILE: js/192-shimmer-skeleton.js
 * AREA: Loading feedback
 * LOAD: Classic script, after js/75-loading-feedback-patterns.js (which delegates here).
 * Shimmer Skeleton markup builders. Styles live in css/192-shimmer-skeleton.css.
 *
 * Every builder reuses the REAL class names of the content it stands in for, so the
 * placeholder takes its size from the same CSS as the real thing (card width and
 * height, grid tracks, text metrics) at every breakpoint. Nothing here animates:
 * the shimmer is a single CSS transform sweep.
 *
 *   AXSkeleton.cards(kind, count)        markup for a run of cards (video, channel, performer, studio)
 *   AXSkeleton.show(target, kind, count) fill a real grid container; returns false if it already has content
 *   AXSkeleton.clear(target)             remove a skeleton from a container
 *   AXSkeleton.later(fn, ms)             run fn only if loading takes longer than ms (no flash on fast loads)
 *   AXSkeleton.entityPage(kind)          performer / studio detail page
 *   AXSkeleton.reviewCards(n)            scene-match review cards
 *   AXSkeleton.amrRows(n)                auto-match review rows
 *   AXSkeleton.markers(n), .keyValues(n), .lines(n), .tiles(n), .rows(n), .inline(width)
 * ============================================================================ */
(function () {
  'use strict';
  if (window.AXSkeleton) return;

  // Deterministic width cycles so neighbouring cards differ like real titles do.
  const TITLE_A = [92, 78, 88, 96, 70, 84, 90];
  const TITLE_B = [64, 52, 70, 48, 58, 66, 44];
  const NAME = [78, 62, 86, 54, 70, 92, 66];
  const pick = (list, i) => list[Math.abs(i) % list.length];

  const bar = (w, extra = '', style = '') =>
    `<span class="ax-sk ax-sk-line${extra ? ' ' + extra : ''}" style="--w:${w}%${style ? ';' + style : ''}"></span>`;
  const chip = (w) => `<span class="ax-sk ax-sk-chip" style="--w:${w}px"></span>`;
  const status = (label) => `<span class="ax-sk-status">${label}</span>`;

  // ---- Card kinds (placed directly in the real grid container) -------------------
  const CARD = {
    video(i) {
      // Roughly two thirds of real cards have a tag row; the rest are shorter.
      const tags = i % 3 !== 1;
      return `<div class="vc ax2-library-card ax-sk-card" aria-hidden="true">
        <div class="vt"><span class="ax-sk ax-sk-fill"></span><span class="ax-sk ax-sk-badge is-res" style="--w:${pick([38, 34, 42], i)}px"></span><span class="ax-sk ax-sk-badge is-dur" style="--w:${pick([54, 48, 58], i)}px"></span><span class="ax-sk ax-sk-circle is-fav"></span></div>
        <div class="vi">
          <div class="video-title-row"><div class="vn">${bar(pick(TITLE_A, i))}${bar(pick(TITLE_B, i))}</div></div>
          <div class="video-card-entities video-card-performers"><span class="ax-sk ax-sk-avatar"></span><span class="ax-sk ax-sk-avatar"></span><span class="ax-sk ax-sk-line" style="width:${pick([64, 52, 72], i)}px;flex:0 0 auto"></span></div>
          <div class="video-card-channel"><span class="ax-sk ax-sk-avatar" style="--s:13px"></span>${bar(pick(NAME, i + 2))}</div>
          ${tags ? `<div class="video-card-entities video-card-tags">${chip(pick([74, 60, 82], i))}${chip(pick([66, 78, 56], i))}</div>` : ''}
        </div>
      </div>`;
    },
    channel(i) {
      return `<div class="pcard channel-file-card channel-profile-card ax2-channel-card ax-sk-card" aria-hidden="true">
        <div class="channel-profile-avatar ax2-channel-avatar"><span class="ax-sk ax-sk-fill ax-sk-circle"></span></div>
        <div class="channel-profile-title ax2-channel-name">${bar(pick([84, 70, 92, 62], i))}</div>
        <div class="channel-profile-count ax2-channel-count">${bar(pick([38, 46, 32], i))}</div>
      </div>`;
    },
    performer(i) {
      return `<div class="pcard performer-card ax-sk-card" aria-hidden="true">
        <div class="pimg"><span class="ax-sk ax-sk-fill"></span></div>
        <div class="p-overlay">
          <div class="pname">${bar(pick(NAME, i))}</div>
          <div class="performer-tile-footer"><div class="performer-tile-stat">${bar(pick([34, 42, 28], i))}</div></div>
        </div>
      </div>`;
    },
    studio(i) {
      return `<div class="pcard ax-sk-card" aria-hidden="true">
        <div class="pimg"><span class="ax-sk ax-sk-fill" style="--ax-sk-r:12px"></span></div>
        <div class="pinfo">
          <div class="pname-row"><div class="pname">${bar(pick(NAME, i + 1))}</div></div>
          <div class="studio-tile-footer"><div class="pcount studio-tile-stat">${bar(pick([48, 56, 40], i))}</div></div>
        </div>
      </div>`;
    },
  };

  const LABEL = { video: 'videos', channel: 'channels', performer: 'performers', studio: 'studios' };

  function cards(kind, count = 8) {
    const make = CARD[kind] || CARD.video;
    const n = Math.max(1, Math.min(60, Number(count) || 8));
    return status(`Loading ${LABEL[kind] || 'content'}`) + Array.from({ length: n }, (_, i) => make(i)).join('');
  }

  const REAL_CONTENT = '.vc:not(.ax-sk-card),.pcard:not(.ax-sk-card),.entity-empty-state,.ax2-channel-card:not(.ax-sk-card)';

  function show(target, kind, count) {
    if (!target || target.querySelector(REAL_CONTENT)) return false;
    target.innerHTML = cards(kind, count);
    target.dataset.axSkeleton = kind;
    target.setAttribute('aria-busy', 'true');
    return true;
  }
  function clear(target) {
    if (!target || !target.dataset.axSkeleton) return;
    // Remove only the placeholders (and their screen-reader label); real cards stay.
    target.querySelectorAll(':scope > .ax-sk-card, :scope > .ax-sk-status').forEach((node) => node.remove());
    delete target.dataset.axSkeleton;
    target.removeAttribute('aria-busy');
  }
  function later(fn, ms = 140) {
    let timer = setTimeout(() => { timer = 0; try { fn(); } catch (_) {} }, ms);
    return {
      cancel() { if (timer) { clearTimeout(timer); timer = 0; } },
      get shown() { return timer === 0; },
    };
  }

  // ---- Detail page ----------------------------------------------------------------
  function entityPage(kind) {
    const studio = kind === 'studio';
    const media = studio
      ? `<div class="ax2-studio-media"><div class="studio-profile-logo ax2-studio-logo"><span class="ax-sk ax-sk-fill" style="--ax-sk-r:14px"></span></div></div>`
      : `<div class="ax2-entity-media performer-profile-avatar"><span class="ax-sk ax-sk-fill" style="--ax-sk-r:14px"></span></div>`;
    const tabs = (studio ? [104, 118, 112, 96] : [106, 104, 144, 102]).map((w) => `<span class="ax-sk" style="--w:${w}px"></span>`).join('');
    return `<div class="ax-sk-group ax-sk-page" aria-hidden="true">
      ${status(`Loading ${studio ? 'studio' : 'performer'}`)}
      <div class="entity-detail-backbar ax2-entity-backbar"><span class="ax-sk ax-sk-circle" style="width:40px;height:40px"></span><span class="ax-sk ax-sk-line" style="--w:180px;width:180px"></span></div>
      <section class="ax2-entity-hero ${studio ? 'ax2-studio-hero' : 'ax2-performer-hero'} ax-sk-hero">
        ${media}
        <div class="ax2-entity-main">
          <div class="ax2-entity-eyebrow">${bar(14, '', 'width:90px')}</div>
          <h1 class="ax2-entity-title">${bar(46)}</h1>
          <div class="ax2-entity-pills">${chip(74)}${chip(96)}${chip(88)}</div>
          <div class="ax2-entity-bio">${bar(92)}${bar(78)}${bar(56, 'ax-sk-bio-extra')}</div>
          <div class="ax2-entity-actions"><span class="ax-sk ax-sk-btn" style="--w:150px"></span><span class="ax-sk ax-sk-btn" style="--w:150px"></span><span class="ax-sk ax-sk-btn" style="--w:120px"></span></div>
        </div>
      </section>
      <section class="ax2-entity-details-card ax-sk-details">
        <div class="ax2-entity-details-head">${bar(14, '', 'width:90px')}</div>
        <div class="performer-overview-details"><div class="performer-overview-detail">${bar(30)}${bar(52)}</div></div>
      </section>
      <div class="entity-detail-tabs ax2-entity-tabs ax-sk-tabs">${tabs}</div>
      <div class="entity-tab-panel active"><div class="entity-video-grid">${cards('video', 8).replace(/<span class="ax-sk-status">[^<]*<\/span>/, '')}</div></div>
    </div>`;
  }

  // ---- Review lists -----------------------------------------------------------------
  function reviewCards(n = 3) {
    const frame = () => `<figure class="ax-scene-match-frame"><div class="ax-scene-match-frame-image"><span class="ax-sk ax-sk-fill" style="--ax-sk-r:8px"></span></div><figcaption>${bar(40, '', 'margin-inline:auto')}</figcaption></figure>`;
    return status('Loading saved suggestions and unresolved videos') + Array.from({ length: n }, (_, i) => `
      <article class="ax-scene-match-review ax-sk-card" aria-hidden="true">
        <div class="ax-scene-match-review-content">
          <div class="ax-scene-match-review-title"><span class="ax-sk ax-sk-line" style="width:${pick([38, 46, 32], i)}%"></span><span class="ax-sk ax-sk-chip" style="--w:64px"></span></div>
          <div class="ax-scene-match-review-identity">${bar(pick([62, 54, 70], i))}</div>
          <div class="ax-scene-match-provider-record">${bar(pick([48, 56, 42], i))}</div>
          <div class="ax-scene-match-preview-grid">
            <div class="ax-scene-match-provider-preview"><div class="ax-scene-match-preview-label">${bar(30)}</div><span class="ax-sk"></span></div>
            <div class="ax-scene-match-local-preview"><div class="ax-scene-match-preview-label">${bar(36)}</div><div class="ax-scene-match-frame-grid">${frame()}${frame()}${frame()}${frame()}</div></div>
          </div>
          <div class="ax-scene-match-details ax-sk-details-row">${bar(22)}</div>
        </div>
        <div class="ax-scene-match-review-actions"><span class="ax-sk ax-sk-btn" style="--w:88px;height:32px"></span><span class="ax-sk ax-sk-btn" style="--w:78px;height:32px"></span></div>
      </article>`).join('');
  }

  function amrRows(n = 4) {
    return status('Loading earlier filename matches') + Array.from({ length: n }, (_, i) => `
      <div class="amr-item ax-sk-card" aria-hidden="true">
        <div class="amr-thumb"><span class="ax-sk ax-sk-fill" style="--ax-sk-r:8px"></span></div>
        <div class="amr-body">
          <div class="amr-file-row">${bar(pick([58, 66, 50], i))}</div>
          <div class="amr-applied">${bar(pick([72, 64, 80], i))}</div>
          <div class="amr-why">${bar(pick([46, 54, 40], i))}</div>
        </div>
        <div class="amr-actions"><span class="ax-sk ax-sk-btn" style="--w:62px;height:30px"></span><span class="ax-sk ax-sk-btn" style="--w:62px;height:30px"></span></div>
      </div>`).join('');
  }

  // ---- Small building blocks for panels and dialogs ---------------------------------
  function lines(n = 3, widths = [92, 78, 54]) {
    return `<div class="ax-sk-lines" role="status" aria-label="Loading">${Array.from({ length: n }, (_, i) => bar(widths[i % widths.length])).join('')}</div>`;
  }
  function keyValues(n = 4) {
    return `<div class="ax-sk-kv" role="status" aria-label="Loading">${Array.from({ length: n }, (_, i) => bar(70, '', `--w:70%`) + bar(pick([88, 62, 76, 94], i))).join('')}</div>`;
  }
  function tiles(n = 6) {
    return `<div class="ax-sk-tiles" role="status" aria-label="Loading">${Array.from({ length: n }, (_, i) => `<div class="ax-sk-tile">${bar(pick([46, 38, 54], i))}${bar(pick([72, 64, 80], i))}</div>`).join('')}</div>`;
  }
  function rows(n = 4, opts = {}) {
    const lead = opts.lead === false ? '' : `<span class="ax-sk ax-sk-lead"></span>`;
    return `<div class="ax-sk-rows ax-sk-stagger" role="status" aria-label="Loading">${Array.from({ length: n }, (_, i) =>
      `<div class="ax-sk-row"${opts.lead === false ? ' style="grid-template-columns:minmax(0,1fr) auto"' : ''}>${lead}<div class="ax-sk-body">${bar(pick(TITLE_A, i))}${bar(pick(TITLE_B, i))}</div></div>`).join('')}</div>`;
  }
  function markers(n = 3) {
    return `<div role="status" aria-label="Loading scene markers">${Array.from({ length: n }, (_, i) =>
      `<div class="ax-sk-marker"><span class="ax-sk ax-sk-chip" style="--w:54px"></span>${bar(pick(TITLE_A, i + 1))}</div>`).join('')}</div>`;
  }
  function inline(width = '8ch') {
    return `<span class="ax-sk ax-sk-inline" style="--w:${width}" role="status" aria-label="Loading"></span>`;
  }

  window.AXSkeleton = { cards, show, clear, later, entityPage, reviewCards, amrRows, lines, keyValues, tiles, rows, markers, inline, bar };
})();
