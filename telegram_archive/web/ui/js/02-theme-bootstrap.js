/* ============================================================================
 * FILE: js/02-theme-bootstrap.js
 * PURPOSE: Applies the saved theme before first paint. Served inline in the page head.
 * AREA: Theming
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
(function(){
  // Storage can be unavailable (blocked site data, some privacy modes): reading it
  // throws. Read defensively so the theme attributes are always applied; ~2,400
  // stylesheet selectors depend on html[data-theme-preset].
  const localStorage={getItem(k){try{return window.localStorage.getItem(k);}catch(e){return null;}}};
  try{
    const aliases={'nobuf-dark':'nobuf','nobuf-light':'nobuf','solarized-light':'solarized'};
    const presets={nord:true,'nero-gold':true,'black-essence':true,'pastel-lavender':true,'pastel-mint':true,'minimal-slate':true,'minimal-sand':true};
    const root=document.documentElement;
    const safeColor=value=>typeof value==='string'&&value.length<=160&&!/[;{}<>\\]/.test(value)&&window.CSS&&CSS.supports&&CSS.supports('color',value);
    const systemMode=()=>{try{return window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';}catch(e){return 'dark';}};
    let legacyMode=localStorage.getItem('ax_theme_mode')||'dark';
    let mode=legacyMode==='light'||legacyMode==='dark'?legacyMode:systemMode();
    let savedCustom=null;
    try{
      const id=localStorage.getItem('ax_active_custom_theme')||'';
      const themes=JSON.parse(localStorage.getItem('ax_custom_themes_v1')||'[]');
      if(id&&Array.isArray(themes))savedCustom=themes.find(t=>t&&t.id===id)||null;
    }catch(e){}
    if(savedCustom){
      const p=(savedCustom.palettes&&savedCustom.palettes[mode])||savedCustom.palette;
      if(p&&['bg','surface','primary','secondary','text','subtext','border','hover'].every(k=>safeColor(p[k]))){
        const mix=(a,n,b)=>`color-mix(in srgb, ${a} ${n}%, ${b})`;
        const values={
          '--ax-preset-bg':p.bg,'--ax-preset-surface':p.surface,'--ax-preset-surface-alt':mix(p.surface,88,p.text),
          '--ax-preset-elevated':mix(p.surface,76,p.text),'--ax-preset-selected':mix(p.surface,70,p.primary),
          '--ax-preset-accent':p.primary,'--ax-preset-accent-hover':mix(p.primary,86,p.text),
          '--ax-preset-secondary':p.secondary,'--ax-preset-text':p.text,'--ax-preset-subtext':p.subtext,
          '--ax-preset-muted':mix(p.subtext,74,p.bg),'--ax-preset-on-accent':mode==='dark'?p.bg:'#ffffff',
          '--ax-preset-border':p.border,'--ax-preset-border-strong':mix(p.border,62,p.text),
          '--ax-overlay-light':p.hover,'--ax-overlay-dark':mode==='dark'?'rgba(0,0,0,.48)':'rgba(0,0,0,.14)',
          '--ax-scrim':mode==='dark'?'rgba(0,0,0,.64)':'rgba(0,0,0,.28)'
        };
        Object.entries(values).forEach(([k,v])=>root.style.setProperty(k,v));
        root.dataset.theme=mode;root.dataset.themeMode=mode;root.dataset.themePreset='custom';root.dataset.axCustomTheme=savedCustom.id;root.style.colorScheme=mode;
      }
    }
    if(root.dataset.themePreset!=='custom'){
      let savedPreset=localStorage.getItem('ax_theme_preset')||'nord';
      savedPreset=aliases[savedPreset]||savedPreset;
      const preset=presets[savedPreset]?savedPreset:'nord';
      root.dataset.theme=mode;root.dataset.themeMode=mode;root.dataset.themePreset=preset;root.style.colorScheme=mode;
    }
    const density=localStorage.getItem('ax_grid_density');
    root.dataset.gridDensity=['compact','default','spacious'].includes(density)?density:'default';
    // Apply app-wide native layout zoom before first paint. Keep the scaled
    // root at 100% width so its descendants reflow against the available viewport
    // instead of stretching into an artificial inverse-scaled canvas.
    const rawScale=Number(localStorage.getItem('ax_ui_scale')||100);
    const scale=Number.isFinite(rawScale)?Math.max(70,Math.min(130,Math.round(rawScale))):100;
    root.dataset.uiScale=String(scale);
    root.style.setProperty('--ax-ui-zoom',String(scale/100));
  }catch(e){}
  const r=document.documentElement;
  if(!r.dataset.themePreset){r.dataset.themePreset='nord';}
  if(!r.dataset.theme){r.dataset.theme='dark';r.dataset.themeMode='dark';r.style.colorScheme='dark';}
  if(!r.dataset.uiScale){r.dataset.uiScale='100';r.style.setProperty('--ax-ui-zoom','1');}
})();
