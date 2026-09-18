#!/bin/sh
# Run with: sh start.sh [options]. No Python or uv installation is required.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
uv_version=0.11.14
uv_dir="$repo_dir/.bootstrap/uv/$uv_version"
uv_bin="$uv_dir/uv"
offline=false
for argument in "$@"; do
    case "$argument" in
        --offline) offline=true ;;
        --help|-h)
            printf '%s\n' 'Usage: sh start.sh [--data-dir PATH] [--config PATH] [--port PORT]' \
                '                   [--no-browser] [--skip-core] [--setup-only] [--offline]' \
                '' 'Installs the runtime, prepares free source search, and opens the app.' \
                'OpenAI configuration is optional in browser Settings. No paid work runs.'
            exit 0 ;;
    esac
done

fail() { printf '\n%s\n' "$1" 'See docs/Troubleshooting.md, then rerun this command.' >&2; exit 3; }
trap 'printf "\nSetup stopped. Rerun the same command when ready.\n" >&2; exit 130' INT TERM

printf '%s\n' 'NYC Housing Research' '[1/4] Preparing the application runtime...'
if [ ! -x "$uv_bin" ]; then
    # Reuse only the tested version; never upgrade a user-managed uv installation.
    existing_uv=$(command -v uv || true)
    if [ -n "$existing_uv" ] && "$existing_uv" --version | \
        awk -v expected="$uv_version" '$2 == expected { found=1 } END { exit !found }'; then
        uv_bin="$existing_uv"
    else
        [ "$offline" = false ] || fail 'The runtime is missing. Run once with internet access.'
        printf '%s\n' "Downloading uv $uv_version from astral.sh into .bootstrap (no administrator access)."
        installer=$(mktemp "${TMPDIR:-/tmp}/nyc-housing-installer.XXXXXXXX")
        trap 'rm -f -- "$installer"' EXIT
        installer_url="https://astral.sh/uv/$uv_version/install.sh"
        if command -v curl >/dev/null 2>&1; then
            curl --proto '=https' --tlsv1.2 -fLsS --connect-timeout 20 --max-time 180 \
                "$installer_url" -o "$installer" || fail 'The runtime download failed. Check your internet connection.'
        elif command -v wget >/dev/null 2>&1; then
            wget --https-only --timeout=30 --tries=2 -qO "$installer" "$installer_url" || \
                fail 'The runtime download failed. Check your internet connection.'
        else
            fail 'This system needs curl or wget to download the runtime.'
        fi
        UV_UNMANAGED_INSTALL="$uv_dir" sh "$installer" || fail 'The runtime installer could not finish.'
        [ -x "$uv_bin" ] || fail 'The runtime installer did not produce the expected executable.'
    fi
fi

# Keep the environment tied to this checkout, even when another venv is active.
export UV_PROJECT_ENVIRONMENT="$repo_dir/.venv"
export UV_PYTHON_DOWNLOADS=automatic
export PYTHONUNBUFFERED=1
if [ "$offline" = true ]; then export UV_OFFLINE=1; fi
printf '%s\n' '[2/4] Preparing Python 3.12 and locked dependencies (including secure key storage)...'
"$uv_bin" sync --project "$repo_dir" --python 3.12 --locked --extra credentials --inexact || \
    fail 'Dependency setup failed. Check the error above; no source installation was started.'

# Run from the caller's directory so relative workspace paths mean what they type.
"$uv_bin" run --project "$repo_dir" --no-sync --no-env-file \
    nyc-housing start "$@"
