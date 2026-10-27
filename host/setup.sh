#!/bin/sh
# Set up an OpenDAP developer host (macOS). Safe to run again.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"

echo "== Homebrew packages"
brew bundle --file="$here/Brewfile"

echo "== Git LFS"
git lfs install --skip-repo

echo "== Rust targets"
# aarch64 Linux (Blackbird OS, glibc and musl), Cortex-M0+ (STM32U0), Cortex-M33 and RISC-V (RP2350)
rustup target add aarch64-unknown-linux-gnu aarch64-unknown-linux-musl \
  thumbv6m-none-eabi thumbv8m.main-none-eabihf riscv32imac-unknown-none-elf

echo "== Rust cargo tools"
cargo binstall -y --locked cargo-nextest cargo-deny cargo-zigbuild

echo "== KiCad"
kicad_cli="${KICAD_CLI:-/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli}"
if [ -x "$kicad_cli" ]; then
  "$kicad_cli" version
  kicad_py="/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
  # Interactive HTML BOM runs inside KiCad's own Python, which provides pcbnew.
  "$kicad_py" -m pip install --quiet --upgrade "InteractiveHtmlBom==2.12.0"
else
  echo "KiCad 10 not found: install it from https://www.kicad.org/download/macos/" >&2
fi

echo "== Design applications"
for app in FreeCAD Blender; do
  plist="/Applications/$app.app/Contents/Info.plist"
  if [ -f "$plist" ]; then
    v="$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$plist" 2>/dev/null || true)"
    [ -n "$v" ] || v="$(/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$plist" 2>/dev/null || echo unknown)"
    echo "$app $v"
  else
    echo "$app not found: install it from its official site" >&2
  fi
done

echo "== odapkit"
# The toolkit's one command (rfcs 0009), linked onto PATH; it finds every odapkit-* tool itself.
mkdir -p "$HOME/.local/bin"
ln -sf "$here/bin/odapkit" "$HOME/.local/bin/odapkit"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Add ~/.local/bin to PATH to run odapkit" >&2 ;; esac
echo "linked ~/.local/bin/odapkit; odapkit help lists the tools"

echo "== Generated trees out of the Spotlight index"
# odapkit noindex renames target/, .venv/ and test caches to <name>.noindex behind a symlink; the global
# Git ignore must cover both, because repositories ignore target/ as a directory only.
ignore="${XDG_CONFIG_HOME:-$HOME/.config}/git/ignore"
[ -n "$(git config --global core.excludesFile)" ] && ignore="$(git config --global core.excludesFile)"
mkdir -p "$(dirname "$ignore")"; touch "$ignore"
for pattern in '*.noindex' target .venv .pytest_cache .ruff_cache .mypy_cache; do
  grep -qxF "$pattern" "$ignore" || echo "$pattern" >> "$ignore"
done
echo "global Git ignore: $ignore; run odapkit noindex --apply <workspace> after builds create new trees"

echo "== Virtual T1 network"
# The LAN for the virtual T1 needs administrator rights once; it is not run automatically.
"$here/bin/odapkit-vmnet" status || true

echo "== Done"
