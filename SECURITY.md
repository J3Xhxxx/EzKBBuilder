# Security

EzKBBuilder v0.1 is a local, single-user workbench. It binds to `127.0.0.1` by default and has no account authentication or per-user authorization. Do not expose it directly to the public internet or an untrusted network. Host and browser-origin checks reduce cross-site request risks; they do not authenticate other software running on your computer.

The configured source root grants access to supported text files beneath that directory. Use a dedicated source directory when working with sensitive files. Web sources require explicit host allowlisting and public-address checks. Source text is treated as untrusted model input, but model audit and citation validation cannot guarantee that all prompt injection or factual errors will be detected.

Real API calls send supplied source material and/or retrieved knowledge to your configured providers. Local workspaces retain source snapshots, card versions, responses, and vectors. Keep credentials in an uncommitted `.env` or environment variables, and review provider terms before processing confidential material. The repository does not contain API keys or private workspaces.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** if private reporting is available. Otherwise, open an issue requesting a private contact channel, with only the affected component and a non-sensitive summary. Do not post credentials, private source material, or exploit details publicly while arranging private communication.

Only the latest development version is currently maintained. There is no guaranteed response-time commitment.
