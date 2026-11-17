/** The classic UI's seven built-in themes, each with a dark and a light variant. The colours are the values the classic app
 *  actually applies (measured from its --ax2-* variables), so a theme chosen here looks the same in both interfaces. */
export type Mode = 'dark' | 'light'
export interface Palette { bg: string; surface: string; surface2: string; surface3: string; text: string; text2: string; text3: string; accent: string; onAccent: string; ok: string; warn: string; bad: string }
export interface ThemeFamily { id: string; name: string; dark: Palette; light: Palette }

export const THEMES: ThemeFamily[] = [
  {
    id: 'nord',
    name: 'Nord',
    dark: { bg: '#232833', surface: '#2b313d', surface2: '#353c4a', surface3: '#414a5b', text: '#eceff4', text2: '#9aa4b6', text3: '#717c90', accent: '#5fd3e0', onAccent: '#062a2e', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#eceff4', surface: '#ffffff', surface2: '#e5e9f0', surface3: '#d8dee9', text: '#2e3440', text2: '#4c566a', text3: '#7b8797', accent: '#527ba5', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'nero-gold',
    name: 'Nero Gold',
    dark: { bg: '#131313', surface: '#262625', surface2: '#343331', surface3: '#4b4a48', text: '#ffffff', text2: '#cfcdc7', text3: '#96948f', accent: '#efd395', onAccent: '#201c13', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#f7f4eb', surface: '#ffffff', surface2: '#eee8d8', surface3: '#e3d9bb', text: '#262625', text2: '#5c5b57', text3: '#888782', accent: '#8d6a18', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'black-essence',
    name: 'Black Essence',
    dark: { bg: '#030303', surface: '#141414', surface2: '#242424', surface3: '#343434', text: '#f9f9f9', text2: '#c1c1c1', text3: '#8b8b8b', accent: '#d1d1d1', onAccent: '#111111', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#f2f2f2', surface: '#ffffff', surface2: '#e5e5e5', surface3: '#d1d1d1', text: '#171717', text2: '#5b5b5b', text3: '#828383', accent: '#343434', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'pastel-lavender',
    name: 'Pastel Lavender',
    dark: { bg: '#231f2e', surface: '#2d2839', surface2: '#383247', surface3: '#463f58', text: '#f4f0fb', text2: '#cbc3de', text3: '#9890b0', accent: '#cbb8f5', onAccent: '#2a2140', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#f6f1fd', surface: '#ffffff', surface2: '#ece4fa', surface3: '#ded2f4', text: '#2b2540', text2: '#5d5575', text3: '#8a82a0', accent: '#7a5fc0', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'pastel-mint',
    name: 'Pastel Mint',
    dark: { bg: '#1b2625', surface: '#243230', surface2: '#2e403d', surface3: '#3b504c', text: '#eff9f5', text2: '#bfd8cf', text3: '#8aa89d', accent: '#a8e6cf', onAccent: '#12302a', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#eefaf5', surface: '#ffffff', surface2: '#def3ea', surface3: '#cdebdf', text: '#1f3a32', text2: '#4f6b61', text3: '#7f9a90', accent: '#2f8a6a', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'minimal-slate',
    name: 'Minimal Slate',
    dark: { bg: '#1a1d21', surface: '#22262b', surface2: '#2b3037', surface3: '#373d46', text: '#eceff2', text2: '#b9c0c8', text3: '#86909b', accent: '#8fa3b8', onAccent: '#14181c', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#f4f6f8', surface: '#ffffff', surface2: '#e9edf1', surface3: '#dbe1e7', text: '#1d242b', text2: '#55616d', text3: '#84909b', accent: '#44586c', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'minimal-sand',
    name: 'Minimal Sand',
    dark: { bg: '#1a1815', surface: '#231f1b', surface2: '#2d2823', surface3: '#3a342d', text: '#f3efe9', text2: '#c4bcb1', text3: '#8f8579', accent: '#c4a484', onAccent: '#1a1410', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#f8f5f0', surface: '#ffffff', surface2: '#eee9e1', surface3: '#e1d9ce', text: '#2a241e', text2: '#5f564c', text3: '#8b8176', accent: '#86644a', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  // Three palettes added later, each built from three swatches (a light neutral, a dark and one bright colour). The swatch colours are used as they
  // are where they read well (the bright colour as the accent on the dark variant); the light variants take a darker accent so links stay readable.
  {
    id: 'teal-coral',
    name: 'Teal Coral',
    dark: { bg: '#0e2f2c', surface: '#14403c', surface2: '#1a4f4a', surface3: '#22615b', text: '#f3eee8', text2: '#c5cfc9', text3: '#93a8a2', accent: '#ff6845', onAccent: '#1b0a05', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#f3eee8', surface: '#fbf8f4', surface2: '#eae4dc', surface3: '#ddd5ca', text: '#0f3633', text2: '#4b6a66', text3: '#7a8f8b', accent: '#17645f', onAccent: '#f3eee8', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'neon-lime',
    name: 'Neon Lime',
    dark: { bg: '#08090b', surface: '#121316', surface2: '#1c1d21', surface3: '#27292e', text: '#faf8f3', text2: '#c2c2be', text3: '#8a8b88', accent: '#cdf22b', onAccent: '#08090b', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#faf8f3', surface: '#ffffff', surface2: '#efece4', surface3: '#e2ded3', text: '#08090b', text2: '#4a4b4d', text3: '#7c7d7f', accent: '#08090b', onAccent: '#cdf22b', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
  {
    id: 'golden-noir',
    name: 'Gold Noir',
    dark: { bg: '#08090b', surface: '#131417', surface2: '#1d1e22', surface3: '#2a2b30', text: '#f5f3ef', text2: '#c4c2bd', text3: '#8c8b87', accent: '#ffc900', onAccent: '#08090b', ok: '#2dba6e', warn: '#e8a838', bad: '#e25555' },
    light: { bg: '#f5f3ef', surface: '#ffffff', surface2: '#ebe8e1', surface3: '#ddd9cf', text: '#08090b', text2: '#4b4c4e', text3: '#7b7c7e', accent: '#8a6600', onAccent: '#ffffff', ok: '#207a4a', warn: '#8a5a00', bad: '#a83333' },
  },
]
