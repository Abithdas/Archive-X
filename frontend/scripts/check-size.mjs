// Fails when the app's first load grows past the budget. Run after `npm run build`.
import { gzipSync } from 'node:zlib'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const BUDGET = { entryGzipKB: 220, anyChunkGzipKB: 130 }   // first-load script, and the largest single file
const dist = new URL('../dist/', import.meta.url).pathname
const html = readFileSync(join(dist, 'index.html'), 'utf8')
const entry = [...html.matchAll(/<script[^>]+src="\/(assets\/[^"]+\.js)"/g)].map((m) => m[1])
const preload = [...html.matchAll(/<link[^>]+rel="modulepreload"[^>]+href="\/(assets\/[^"]+\.js)"/g)].map((m) => m[1])
const kb = (f) => gzipSync(readFileSync(join(dist, f))).length / 1024
const first = [...new Set([...entry, ...preload])]
const firstKB = first.reduce((n, f) => n + kb(f), 0)
const sizes = readdirSync(join(dist, 'assets')).filter((f) => f.endsWith('.js') && !first.includes(`assets/${f}`)).map((f) => [f, kb(`assets/${f}`)]).sort((a, b) => b[1] - a[1])
const [bigName, bigKB] = sizes[0] ?? ['none', 0]
console.log(`First load: ${firstKB.toFixed(1)} KB gzip across ${first.length} file(s) (budget ${BUDGET.entryGzipKB})`)
console.log(`Largest lazy file: ${bigName} ${bigKB.toFixed(1)} KB gzip (budget ${BUDGET.anyChunkGzipKB})`)
let bad = false
if (firstKB > BUDGET.entryGzipKB) { console.error('First-load size is over budget: lazy-load the new code or raise the budget on purpose.'); bad = true }
if (bigKB > BUDGET.anyChunkGzipKB) { console.error(`${bigName} is over the lazy-file budget.`); bad = true }
process.exit(bad ? 1 : 0)
