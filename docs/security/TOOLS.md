# Security tools: what was taken from the 12-repository list, and what was not

Rule 9 of the plan applies: take on no legal obligations, nothing under GPL, LGPL, AGPL or MPL goes into the product, and the tools below are
only ever *run* (in CI or by hand), never linked or shipped. The licences were read from each repository's LICENSE file on 19 November 2026.

| Tool | Licence | Used? | How |
|---|---|---|---|
| Trivy | Apache-2.0 | yes | `security.yml` scans the tree for misconfiguration and fixable HIGH/CRITICAL; shown in the run summary, not blocking (`sca.yml` already blocks on advisories) |
| Nuclei | MIT | yes | `security.yml`, weekly and on demand, via `tools/security/dast.py`: starts the real backend and scans it without and with the desktop cookie |
| gitleaks (instead of TruffleHog) | MIT | yes | `security.yml` scans the whole history; `.gitleaks.toml` lists the test fixtures and SHA-256 pins that are not secrets |
| Claude Code Security Review | MIT | yes, optional | `security.yml` reviews each pull request when the `ANTHROPIC_API_KEY` repository secret exists; skipped otherwise |
| TruffleHog | AGPL-3.0 | no | gitleaks does the same job under MIT |
| Semgrep | LGPL-2.1 (engine); rules under their own licence | no | CodeQL covers code analysis; the registry rules carry licence terms we do not want |
| ClamAV | GPL-2.0 | no | not needed for a build we produce ourselves; revisit if installers are ever scanned before release |
| Microsoft PyRIT | MIT | later (plan F14) | red-teaming the local assistant against prompt injection; a manual run, not a CI gate |
| Strix, Cloudflare Security Audit, NVIDIA SkillSpector, OpenAI Codex Security, BLA | not checked / not applicable | no | AI-agent pentest and skill-audit tools; the pieces that matter here are covered by the tools above and CodeQL |

Run them by hand:

```bash
gitleaks detect --source . --redact
python tools/security/dast.py --nuclei /path/to/nuclei
trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --ignore-unfixed .
```
