# Security reporting

This is a local developer tool. Keep the backend on loopback: it has no
authentication for internet-facing deployment. See [privacy](docs/PRIVACY.md).

After publication, use the repository's **Security → Report a vulnerability**
feature if the maintainer enables private reporting. No dedicated security email
or response-time guarantee exists yet. Avoid confidential documents, credentials,
or weaponized proof-of-concept files in public issues. A non-sensitive issue can
request a private contact.

Include affected version, browser/Python versions, minimal reproduction, impact,
and whether default loopback configuration was used. Check third-party advisories
against pinned dependency versions as well as the project code.

The publication package includes dated [core](docs/media/core-dependencies.json)
and [optional OCR](docs/media/ocr-dependencies.json) package-advisory inventories.
Refresh them before future releases; absence of recorded advisories is not a
complete security guarantee. The original-code MIT license does not waive
third-party dependency terms.
