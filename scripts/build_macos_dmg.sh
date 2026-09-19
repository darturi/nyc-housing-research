#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
packaging_dir="$project_root/packaging/macos"
build_root="$project_root/build/macos"
dist_root="$project_root/dist/macos"
app_path="$dist_root/NYC Housing Research.app"
architecture="${MACOS_ARCH:-$(uname -m)}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS application must be built on macOS." >&2
  exit 2
fi
if [[ "$architecture" != "arm64" && "$architecture" != "x86_64" && "$architecture" != "universal2" ]]; then
  echo "Unsupported MACOS_ARCH: $architecture" >&2
  exit 2
fi

iconset="$build_root/AppIcon.iconset"
icon_path="$build_root/AppIcon.icns"

rm -rf "$build_root" "$dist_root"
mkdir -p "$iconset" "$dist_root"

for size in 16 32 128 256 512; do
  sips -s format png -z "$size" "$size" "$packaging_dir/AppIcon.svg" \
    --out "$iconset/icon_${size}x${size}.png" >/dev/null
  double_size=$((size * 2))
  sips -s format png -z "$double_size" "$double_size" "$packaging_dir/AppIcon.svg" \
    --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$icon_path"

cd "$project_root"
export UV_PROJECT_ENVIRONMENT="$build_root/venv"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_root/.uv-cache}"
uv sync --locked --no-install-project --extra desktop
version="$("$build_root/venv/bin/python" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
dmg_path="$dist_root/NYC-Housing-Research-${version}-${architecture}.dmg"
cd "$packaging_dir"
MACOS_ARCH="$architecture" MACOS_ICON_PATH="$icon_path" \
  "$build_root/venv/bin/python" "$packaging_dir/setup.py" \
  py2app --bdist-base "$build_root/py2app" --dist-dir "$dist_root"

identity="${MACOS_SIGNING_IDENTITY:--}"
sign_args=(--force --sign "$identity")
if [[ "$identity" != "-" ]]; then
  sign_args+=(--timestamp --options runtime --entitlements "$packaging_dir/entitlements.plist")
fi

while IFS= read -r -d '' candidate; do
  if file "$candidate" | grep -q 'Mach-O'; then
    codesign "${sign_args[@]}" "$candidate"
  fi
done < <(find "$app_path/Contents" -type f -print0)

while IFS= read -r framework; do
  codesign "${sign_args[@]}" "$framework"
done < <(find "$app_path/Contents" -type d -name '*.framework' | sort -r)

codesign "${sign_args[@]}" "$app_path"
codesign --verify --deep --strict --verbose=2 "$app_path"

bundle_pythonpath="$app_path/Contents/Resources/lib/python312.zip:$app_path/Contents/Resources/lib/python3.12:$app_path/Contents/Resources/lib/python3.12/lib-dynload"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$bundle_pythonpath" \
  "$app_path/Contents/MacOS/python" "$packaging_dir/smoke_built_app.py"
codesign --verify --deep --strict --verbose=2 "$app_path"

staging="$build_root/dmg"
mkdir -p "$staging"
ditto "$app_path" "$staging/NYC Housing Research.app"
ln -s /Applications "$staging/Applications"
hdiutil create -volname "NYC Housing Research" -srcfolder "$staging" \
  -ov -format UDZO "$dmg_path" >/dev/null

if [[ "$identity" != "-" ]]; then
  codesign --force --timestamp --sign "$identity" "$dmg_path"
fi
if [[ -n "${MACOS_NOTARY_PROFILE:-}" ]]; then
  if [[ "$identity" == "-" ]]; then
    echo "MACOS_NOTARY_PROFILE requires a Developer ID signing identity." >&2
    exit 2
  fi
  xcrun notarytool submit "$dmg_path" \
    --keychain-profile "$MACOS_NOTARY_PROFILE" --wait
  xcrun stapler staple "$dmg_path"
  xcrun stapler validate "$dmg_path"
fi

dmg_name="$(basename "$dmg_path")"
(cd "$dist_root" && shasum -a 256 "$dmg_name" > "$dmg_name.sha256")
echo "Created $dmg_path"
echo "Checksum: $dmg_path.sha256"
