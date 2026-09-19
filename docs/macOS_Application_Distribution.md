# Standalone macOS application distribution

The macOS application is a second launch and distribution mode for the same local
product. It embeds the existing browser interface in Cocoa WebKit, starts the same
loopback-only FastAPI application, and stores data in the existing
`~/Library/Application Support/nyc-housing-rag` workspace. The repository launcher
and advanced CLI remain available.

The first supported artifact is native to the Mac that builds it. Build the public
arm64 artifact on an Apple-silicon Mac. A universal build requires a universal2
Python and universal2-compatible native dependencies and is not part of the first
release target.

## Local development build

Requirements:

- macOS with Xcode Command Line Tools.
- `uv` and internet access for the locked dependency installation.
- Sufficient free space for a clean bundled Python environment and DMG staging.

Run:

```bash
scripts/build_macos_dmg.sh
```

The script creates an ad-hoc-signed application and DMG under `dist/macos/`, plus
a SHA-256 checksum. Before creating the DMG, it starts the bundled local service
from an isolated temporary workspace and verifies that the real application HTML
loads. An ad-hoc build is suitable for local verification only. It is not the
public download artifact.

The build uses an isolated environment under `build/macos/`; it does not add
desktop packages to the repository's development environment. It bundles source
manifests and parser code but does not bundle downloaded legal publications,
property records, credentials, or a user workspace.

## Signed and notarized release

A public direct-download build needs a Developer ID Application certificate. Store
notary credentials once with Apple's `notarytool`, using a keychain profile name,
then provide the identity and profile to the build:

```bash
MACOS_SIGNING_IDENTITY="Developer ID Application: ORGANIZATION (TEAMID)" \
MACOS_NOTARY_PROFILE="nyc-housing-notary" \
scripts/build_macos_dmg.sh
```

The script signs embedded Mach-O files and frameworks from the inside out, signs
the application with hardened runtime, creates and signs the DMG, submits it to
Apple, waits for acceptance, staples the ticket, and writes the checksum. Signing
and notary credentials remain in the macOS keychain and environment; they are not
written into the project or artifact.

Before publishing the download, verify the final files:

```bash
codesign --verify --deep --strict --verbose=2 \
  "dist/macos/NYC Housing Research.app"
spctl --assess --type execute --verbose=2 \
  "dist/macos/NYC Housing Research.app"
hdiutil verify dist/macos/NYC-Housing-Research-*.dmg
(cd dist/macos && shasum -a 256 -c NYC-Housing-Research-*.dmg.sha256)
```

Mount the DMG on a clean supported Mac, drag the application to Applications, and
exercise first launch, source installation, local search, Keychain credential
storage, personal-resource import/download, answer and property exports, offline
reopening, and Quit during an active source download. Confirm that no local server
process remains after Quit.

## Publishing and updates

Publish the notarized DMG and checksum as versioned release assets and link the DMG
from the project website or release page. Updating is manual: the user downloads a
new DMG and replaces the application in Applications. Their workspace remains in
Application Support and is shared with the repository/browser launch mode.

Every release must remain compatible with its declared workspace schema or include
a tested, backup-gated migration. Installing a new application does not rewrite or
discard an incompatible workspace. Corpus/source updates remain independent of
application updates and continue to use the Sources view.

## Shared implementation boundary

`app.desktop` owns the WebKit window and desktop process lifecycle.
`app.launcher.LocalApplicationServer` owns the loopback listener and ASGI server.
All product routes, templates, static assets, storage, retrieval, source, provider,
and export behavior remain shared with browser mode. A normal feature change is
therefore included in both modes when the next DMG is built.
