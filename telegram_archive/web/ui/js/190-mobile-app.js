/* ============================================================================
 * FILE: js/190-mobile-app.js
 * AREA: Native mobile shell (Capacitor app in Archive-X-Mobile/)
 * LOAD: Classic script, last layer in web/html.py.
 * The native app appends "ArchiveXMobile/<version>" to the WebView user agent.
 * In a normal browser this file does nothing.
 * ============================================================================ */
(function () {
  'use strict';
  var ua = navigator.userAgent || '';
  if (!/ArchiveXMobile\//.test(ua)) return;

  var root = document.documentElement;
  root.classList.add('ax-native');
  var isIOS = /iPhone|iPad|iPod/.test(ua);
  if (isIOS) root.classList.add('ax-native-ios'); else root.classList.add('ax-native-android');

  // The app's own connect screen is served from the WebView's local origin.
  var shell = (isIOS ? 'capacitor://localhost' : 'https://localhost') + '/?change=1';
  function changeServer() { location.href = shell; }

  function chip(cls, label) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.textContent = label;
    b.addEventListener('click', changeServer);
    return b;
  }

  function mount() {
    // Login screen: small link below the form.
    if (!document.getElementById('ax-native-change-server-login') && document.getElementById('login-submit')) {
      var link = chip('ax-native-change-server', 'Change server');
      link.id = 'ax-native-change-server-login';
      var submit = document.getElementById('login-submit');
      submit.parentNode.insertBefore(link, submit.nextSibling);
    }
    // Signed-in account menu: next to Sign out.
    var actions = document.querySelector('.ax2-menu-account-actions');
    if (actions && !document.getElementById('ax-native-change-server-menu')) {
      var item = chip('btn sm', 'Change server');
      item.id = 'ax-native-change-server-menu';
      item.setAttribute('role', 'menuitem');
      actions.appendChild(item);
    }
  }

  mount();
  // The UI renders views dynamically; keep the controls present.
  new MutationObserver(function () { mount(); }).observe(document.body || root, { childList: true, subtree: true });
})();
