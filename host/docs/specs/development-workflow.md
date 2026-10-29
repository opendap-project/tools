# Development workflow

- **Status:** Draft, 2026-10-23. Development happens on the host, with VMs as the runtime. Cross-repository changes: [RFC 0006](https://github.com/opendap-project/rfcs/blob/main/text/0006-development-workflow.md), [RFC 0007](https://github.com/opendap-project/rfcs/blob/main/text/0007-virtual-devices.md) (virtual devices) and [RFC 0008](https://github.com/opendap-project/rfcs/blob/main/text/0008-repository-restructure.md) (repositories by audience, `odap` names).
- **Builds on:**
  - TK-001, TK-010 and TK-011 (host tools, UNIX only, tested on macOS);
  - Blackbird OS OS-009, OS-016, OS-018 and OS-019 (run, control and automate the system on the host);
  - system ADR 0001, ADR 0002, ADR 0006, ADR 0008, ADR 0011, ADR 0012 and ADR 0013 (Rust, the composer and session, Slint, the dependency set, package layers, hardware descriptions).
- **Verified** on macOS 27 (Apple silicon), 2026-10-23: every command in §4 and §5.

## 1. Summary

1. **Code is written, built and tested on the developer host.** Linux virtual machines never compile OpenDAP software.
2. **The build VM builds images and the sysroot, nothing else.** It runs mkosi, because mkosi needs Linux; toolkit-image's `odapkit image` drives it.
3. **Virtual devices and the T1 are runtimes.** Software reaches them as development system extensions (package layer L3 of ADR 0011), without rebuilding the image.
4. **The fastest loop that can show a change is the one to use:** domain and core crates natively, the product UI in a window, the virtual device for the Linux device stack, the T1 for the hardware.
5. **Tools are one task each, named by their domain** (`odapkit image`, `odap-vdev`…), find other repositories through the workspace's `repos.tsv`, and pass other repositories' content to builds as inputs, never as paths written into a definition.

## 2. Roles

| Machine | Does | Never does |
|---|---|---|
| **Host** (macOS, or Linux) | Edit, `cargo check`, `cargo test`, `cargo clippy`; run the product UI natively; cross-build for aarch64 (`odapkit cross`); deploy (`odapkit deploy`); run and drive virtual devices (`odap-vdev`); every repository check (pre-commit, REUSE) | Hold anything another machine needs in order to build |
| **Build VM** (Lima, toolkit-image `vm/build-vm.yaml`, the workspace and `~/.local/share/opendap` mounted) | `mkosi` builds: images (`qemu`, `oci`, later device profiles) and the `sysroot` profile | Compile OpenDAP software; hold a Rust toolchain; keep state that is not in the image definition |
| **Virtual devices** (QEMU, [virtual-devices](https://github.com/opendap-project/blackbird/tree/main/emulator) `odap-vdev`) | Run Blackbird OS on a virtual model: display, touch, buttons, audio, battery, USB, microSD, LAN (VD-001) | Build anything |
| **T1** | Run Blackbird OS on the hardware | Build anything |

On a Linux host the build VM is optional: mkosi can run on the host itself. Only macOS is tested (TK-011).

## 3. Toolchain

| Piece | Pin | Where it comes from |
|---|---|---|
| Rust | `rust-toolchain.toml` in each repository: an exact version (1.99.1), `rustfmt`, `clippy`, the targets the repository builds | rustup, which reads the file |
| Cross linker | zig (0.17.0 verified) with cargo-zigbuild (0.24.0) | `Brewfile`, `setup.sh` |
| Target | `aarch64-unknown-linux-gnu.2.41`: the image's glibc (Debian trixie, libc6 2.41) | toolkit-image `odapkit cross` |
| Sysroot | The image's own packages plus their `-dev` packages, built by the `sysroot` profile | toolkit-image `odapkit sysroot` |
| Image builder | mkosi in the build VM | toolkit-image `odapkit image` |
| Development key | One ed25519 key, host-wide in `~/.local/share/opendap/dev-ssh` | toolkit-image `odapkit dev-key` |
| Emulator | QEMU with HVF on macOS, built from its pinned release with the patches OpenDAP carries (`qemu/patches/`: the D-Bus display's sound); socket_vmnet for a LAN address | `bin/odapkit qemu install` (its build tools from `Brewfile`); `bin/odapkit vmnet install` |
| Maintainers' command | `odw` ([command standard](command-standard.md) §1–§4), a Rust binary from `crates/odw-dispatch`, built with the pinned Rust into this checkout's `target/release/odw` and linked as `~/.local/bin/odw`; `bin/odapkit` runs it under its earlier name and is linked as `~/.local/bin/odapkit` | `setup.sh` (`cargo build --release --locked --manifest-path crates/odw-dispatch/Cargo.toml --target-dir target`) |

- **Sysroot.** Linking against libinput, libseat, GBM, EGL, Wayland or ALSA needs the target's headers and libraries.
  - It is built from the image definition, so binaries link against exactly what the image ships.
  - macOS volumes are case-insensitive and Linux headers are not (`ipt_ecn.h` and `ipt_ECN.h`), so it lives in a case-sensitive APFS sparse image under `~/.cache/opendap/`, with Spotlight indexing off.
  - Rebuild it when the image's package set changes.
- **`odw` and `odapkit`.** `odw TOOL` finds the tool in the `tools.toml` of each workspace root (`repos.tsv` order; `ODAP_<ROOT>_DIR` moves a root, so `ODAP_TOOLKIT_HOST_DIR=<worktree>` runs a worktree's tools), then as `odw-TOOL` or `odapkit-TOOL` on `PATH`, and replaces itself with it. `odapkit` runs the `odw` its own checkout built, else the one on `PATH`, after setting `ODAP_TOOLKIT_HOST_DIR` to its checkout when unset; without a build it exits 1 with the `cargo build` command to run. Rebuild `odw` after changing `crates/odw-dispatch` (run `setup.sh` again, or the `cargo build` above); `tools.toml` changes need no rebuild.
- **Pinning the remaining tools** (zig, cargo-zigbuild, QEMU) is open. Homebrew installs current versions today; mise is a candidate.

## 4. Loops

| Loop | Command | For | Takes |
|---|---|---|---|
| **Host tests** | `cargo test -p <crate>` | Domain and core crates, which are platform-agnostic (Rust crate architecture §4); adapters with fakes | Seconds |
| **UI on the host** | `cargo run -p blackbird-apps-player` | Product UI: layout, tokens, type, icons, touch with the pointer, with Blackbird UI's published outputs | Seconds |
| **Virtual device** | `odapkit deploy` in the Cargo workspace, then `odap-vdev screen capture / input press ROLE / input tap`, the `--window`, and `odap-vdev ssh journalctl -u blackbird-composer` | The composer, the boot UI, and anything that needs DRM, libinput, logind, systemd, ALSA or a board's description | Seconds to deploy; a cross-build is incremental |
| **T1** | The same extensions over SSH on the USB network link (`ODAP_DEVICE_SSH`), once the hardware exists | Timing, power, audio quality, the real panel, buttons and LED | — |
| **Image** | `odapkit image build qemu`, then `odap-vdev start` | Changes to the image definition: packages, units, configuration | Minutes |

**Development extensions.** `odapkit deploy` cross-builds the workspace's binaries (the default list and the units to restart are in `[workspace.metadata.odap]` of its `Cargo.toml`), stages them as `/var/lib/extensions/odap-dev-<workspace>/usr/bin/…` with `ID=_any`, merges them with `systemd-sysext refresh`, syncs the disk and restarts the units. Several workspaces deploy side by side (system and toolkit-verify today). `systemd-sysext unmerge` and removing the directories return the device to its image. Development extensions never go into images and are not how software ships (§6).

## 5. What lives where

| Content | Repository | Reaches the device as |
|---|---|---|
| Services, the composer, the boot UI, the product host, the SDK | `system` | Development: an extension. Release: `.deb` packages in the OpenDAP repositories (L2), built with cargo-deb (**not built yet**) |
| Tokens, fonts, icons, brand files | `blackbird-ui` `export/` and `brand/svg/` | Compiled in at build time, found through `repos.tsv` |
| Image definition: packages, units, session, boot | `blackbird-os/image/` | The image (L1); its outputs in `image/mkosi.output/` |
| Virtual hardware: layers, models, the virtual board's support package | `virtual-devices` | QEMU's devices; the support package passed into `qemu` images by `odapkit image`; the description through fw_cfg |
| Maintainer tools: image, sysroot, cross-build, deploy, development key, security check | `toolkit-image` | Host only |
| Test instrumentation (`odap-inject`) | `toolkit-verify` | A development extension only |

### Generated trees

Build outputs, virtual environments and test caches (`target/`, `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`) are never indexed by macOS Spotlight: indexing the thousands of files a build writes keeps the host busy and hot. `odapkit noindex --apply <path>` renames each tree to `<name>.noindex`, which Spotlight skips by name, and leaves a relative symlink `<name>` in its place, so every tool keeps its path; it is safe to run again. While a build runs (`cargo`, `rustc`, `pytest`, any session's) it moves nothing and says it skipped, because moving a tree under a running build breaks it (`--force` moves anyway), so a check stage can end with it; `odapkit noindex --check` fails while a tree is not linked. A tree is linked only when it carries the marker its own tool writes (`CACHEDIR.TAG` for Cargo, pytest, ruff and mypy; `pyvenv.cfg` for a virtual environment), and dependency trees (`node_modules`) are never searched, so folders that only share the name are left alone. `setup.sh` adds `*.noindex` and the symlink names to the global Git ignore, because a repository's `target/` pattern matches directories only. mkosi output and cache trees are not linked yet: a symlinked tree has not been verified inside the build VM. `node_modules` is linked only with `--node-modules`.

## 6. From development to release

- **Packages.** A change is released as a `.deb` in the OpenDAP repositories (L2), and images are built from those packages (OS-010). Development extensions are only a shortcut to the same files.
- **Checks before a commit:** `cargo test`; `cargo clippy`; for anything with a screen, the virtual device with screenshots; the repository's pre-commit (REUSE).
- **Definition of done** for anything with a screen: exercised on a virtual device, driven through `odap-vdev`, checked functionally and visually from its screenshots, and on the T1 once the hardware exists.

## 7. Conformance with the defined stack

| Decision | In the bring-up |
|---|---|
| ADR 0002 and ADR 0012: own compositor on Smithay, session of a dedicated user | `blackbird-composer` (`/usr/lib/blackbird/composer`) on Smithay (a pinned revision of master, ADR 0008 O2), started by `blackbird-composer.service` as `blackbird` in a logind session through PAM; libseat on logind. No third-party compositor |
| Composer spec §2: DRM/KMS, GBM, GLES, libinput | `DrmCompositor`, `GbmAllocator`, `GlesRenderer`, libinput through the session |
| Composer spec §5: no damage, no frame | A frame only after a client commit or while the splash screen fades; one per refresh; a timer, not frames, waits out the splash screen's minimum |
| Composer spec §9, OS-035: boot | The boot UI (`/usr/lib/blackbird/boot`) shows the splash screen from the first display; the composer takes over without a blank frame; consoles only in the debug boot mode |
| ADR 0006: Slint behind the renderer interface | `/usr/lib/blackbird/player` in Slint with FemtoVG and the software renderer compiled in, selected at run time |
| ADR 0013: hardware described by data | Buttons are roles from the device description, which comes from the hardware (fw_cfg on virtual boards); no board in code |
| ADR 0008: `tracing` to journald | `tracing-journald` when started by systemd, stderr otherwise |
| Design system §18.1: tokens as the single source | Slint globals generated from `opendap.tokens.json`, read from `export/` |
