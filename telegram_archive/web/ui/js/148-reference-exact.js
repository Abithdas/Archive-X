/* ============================================================================
 * FILE: js/148-reference-exact.js
 * AREA: Design system and global polish
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X — reference-exact runtime: page-enter motion, header scroll states,
   card "•••" menu trigger, star pop and select-mode body class. Idempotent. */
(function () {
  'use strict';
  if (window.__rxRuntime) return;
  window.__rxRuntime = true;
  var doc = document;
  // Page entrance motion is owned by AXMotion, not by an additional sv wrapper.

  /* header: shadow once scrolled, slide away on scroll-down (phone / short landscape) */
  var lastY = 0, ticking = false;
  function onScroll() {
    ticking = false;
    var top = doc.getElementById('topbar');
    if (!top) return;
    var y = window.pageYOffset || doc.documentElement.scrollTop || 0;
    var hd = doc.getElementById('hdr'); if (hd) hd.classList.toggle('scrolled', y > 4);
    var small = false;
    try { small = (onScroll._mq || (onScroll._mq = window.matchMedia('(max-width:599px), (max-height:500px) and (orientation:landscape)'))).matches; } catch (e) {}
    var hide = small && y > 120 && y > lastY + 2;
    var show = y < lastY - 2 || y <= 120;
    if (hide) top.classList.add('rx-hidden');
    else if (show || !small) top.classList.remove('rx-hidden');
    lastY = y;
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(onScroll); }
  }, { passive: true });

  /* cards: add the "•••" action trigger */
  function decorate(root) {
    var cards = (root || doc).querySelectorAll('#vg > .vc');
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i];
      if (c.querySelector(':scope > .rx-more3')) continue;
      var cid = c.getAttribute('data-cid'), mid = c.getAttribute('data-mid');
      if (!cid || !mid) continue;
      var b = doc.createElement('button');
      b.type = 'button';
      b.className = 'rx-more3';
      b.setAttribute('aria-label', 'Video actions');
      b.setAttribute('aria-haspopup', 'menu');
      b.innerHTML = '<i></i><i></i><i></i>';
      b.addEventListener('click', (function (cc) {
        return function (ev) {
          ev.stopPropagation();
          var t = cc.querySelector('.vn');
          if (typeof window.toggleVideoCardActionMenu === 'function')
            window.toggleVideoCardActionMenu(ev, cc.getAttribute('data-cid'), cc.getAttribute('data-mid'), t ? t.textContent.trim() : '');
        };
      })(c));
      c.appendChild(b);
    }
  }
  var vgObs = null, vgEl = null;
  function watchGrid() {
    var vg = doc.getElementById('vg');
    if (vg === vgEl) return;
    if (vgObs) vgObs.disconnect();
    vgEl = vg;
    if (!vg) return;
    decorate(vg);
    vgObs = new MutationObserver(function () { decorate(vg); });
    vgObs.observe(vg, { childList: true });
  }

  /* star pop + select-mode body class */
  doc.addEventListener('click', function (ev) {
    var s = ev.target && ev.target.closest && ev.target.closest('.vc .fav-btn');
    if (!s) return;
    s.classList.remove('rx-pop');
    void s.offsetWidth;
    s.classList.add('rx-pop');
  }, true);
  function syncSelect() {
    var bar = doc.getElementById('mapbar');
    var on = !!bar && bar.style.display !== 'none' && getComputedStyle(bar).display !== 'none';
    doc.body.classList.toggle('rx-selecting', on);
  }

  var stage = null, stageObs = null;
  function boot() {
    var app = doc.getElementById('app') || doc.body;
    stageObs = new MutationObserver(function () { watchGrid(); syncSelect(); });
    stageObs.observe(app, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
    watchGrid(); syncSelect(); onScroll();
  }
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot); else boot();
})();
