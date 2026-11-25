# Privacy and permissions

This describes the shipped 0.11.1 code, not a hosted service.

## Data flow

The extension sends selected PDFs/arXiv URLs, queries, crop requests, and document
metadata to its configured backend. The default is your own machine at
`http://localhost:7878`. A custom backend URL sends that data to the chosen server.

arXiv preparation contacts arXiv for versions, PDFs, and available TeX. Core search
uses no remote inference API. Optional OCR downloads upstream weights on first
recognition and runs locally. Installation contacts package registries. There is
no configured analytics, telemetry, or account integration.

Chrome local storage contains settings, history, recent papers, bookmarks, and
notes. Backend caches can contain PDFs, source text, formulas, and geometry.
Do not share `.formula_cache`. Indexed OCR additions are session data.

## Manifest permissions

| Permission | Purpose |
| --- | --- |
| `tabs`, `activeTab` | Identify and navigate the paper tab |
| `scripting` | Inject supported page helpers |
| `debugger` | Native PDF compatibility path when allowed; Chrome may show a debugger notice |
| `storage` | Settings, history, notes, and bookmarks |
| `contextMenus` | Context-menu formula-search actions |
| `downloads` | Result exports and bookmark backups |
| `sidePanel` | Search beside the paper |
| HTTP/HTTPS/file host access | Read user-selected PDFs across hosts and local files; reach the backend |

Broad host permissions support papers on many sites. Local file access requires
a separate Chrome opt-in. The manifest also allows the Chrome PDF viewer
extension to access required preview resources and messaging.

## Local server

The startup script binds to `127.0.0.1`. Browser-origin checks reject origins
outside extensions and loopback. This is not authentication: originless programs
and other local extensions can reach the service. There is no internet-facing
authentication or multi-user isolation. Use loopback on a trusted machine.

To clear retained data, uninstall/clear extension local storage and remove backend
caches through your file manager after stopping the server. Back up bookmarks first.
