# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in 灵台 MCP Server, please report it privately before disclosing it publicly.

**Do not open public GitHub issues for security vulnerabilities.**

Contact us via:
- Open a **private advisory** on GitHub: [edrc-shawn/lingtai/security/advisories](https://github.com/edrc-shawn/lingtai/security/advisories)
- Or email: [security@lingtai.dev](mailto:security@lingtai.dev) (placeholder — update as needed)

## What to Include

Please provide:
- A clear description of the vulnerability
- Steps to reproduce (PoC preferred)
- Affected version(s) and configuration
- Potential impact assessment

We aim to acknowledge receipt within 48 hours and provide an initial assessment within 5 business days.

## Scope

This MCP Server connects to local file systems, Obsidian vaults, and external APIs. Key security concerns include:

| Area | Risk |
|------|------|
| **Command injection** | Tool parameters should never be used to construct shell commands via string concatenation |
| **Path traversal** | File access must be scoped to the vault directory |
| **Prompt injection** | External data should not be treated as executable instructions |
| **Credential leakage** | API keys must go through environment variables, not hardcoded defaults |
| **Data exfiltration** | Tools accessing local files should declare their read scope transparently |

## Supported Versions

Only the latest release of `main` branch receives security patches.

## Disclosure Policy

After a fix is released, we will publish a security advisory on GitHub within 30 days, detailing the vulnerability and resolution.