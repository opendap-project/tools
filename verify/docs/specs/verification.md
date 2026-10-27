# Verification

- **Status:** Approved (TK-013), 2026-10-22, without lab instruments (TK-012).
- **Scope:** how OpenDAP verifies its technical promises by software first (simulations, static checks and the virtual T1) and by measurement on hardware later, with every result recorded against requirement IDs.

Bit-perfect playback, DSD and DoP are verified by comparing captured bits, not by theory: the playback engine's output is captured at the ALSA boundary and compared with the source, with negative controls that must fail. A check that never fails proves nothing.

## 1. Verification levels

| Level | Name | Environment | What it can prove |
|---|---|---|---|
| **L0** | Static | Files only: specs, YAML, KiCad netlists, STEP, DT, image manifest, kernel package | Consistency, presence, rules (e.g. a driver exists, a net is wired as required) |
| **L1** | Model / simulation | ngspice, openEMS/Palace + scikit-rf, FreeCAD FEM/CalculiX, Python models | Physics estimates against a budget; ranks design variants |
| **L2** | Virtual T1 | QEMU `virt` + Blackbird OS image, QMP, SSH, `snd-aloop`, UHID, throttled block devices | Software behaviour exactly as shipped, at the kernel/userspace boundary |
| **L3** | Dev-kit hardware | An RK3566 SBC with mainline support, eval boards (DAC, fuel gauge, Wi-Fi module), a Pico 2 | Real drivers, real clocks, real power on the same SoC |
| **L4** | T1 prototype | EVT/DVT boards in the case, with faceplate and HATs | The product promise itself |
| **L5** | Accredited lab | EMC, safety, IP, hearing safety | Certification evidence |

**"Verified by software"** means: **an L0–L2 check passes, its negative control fails as expected, and the result is recorded against the requirement ID.** A report always states the highest level reached, so a promise verified at L2 is never shown as "measured".

## 2. Bit-perfect PCM, DSD (native and DoP) and rate switching

### 2.1 What "verified" means (pass criteria)

For one **test vector** played by the first-party engine on one **output path**:

1. **Payload identity.** After alignment, the captured stream contains the reference stream as **one contiguous, byte-identical run**, from the first to the last sample of the track. Everything outside that run is digital silence (all-zero PCM, or the DSD silence byte 0x69, or DoP frames whose payload is 0x69 with valid markers). No sample inside the run may differ: tolerance is **zero bits**.
2. **Format and rate identity.** While the track plays, the PCM substream's `hw_params` (`/proc/asound/card*/pcm*p/sub*/hw_params`, documented as "the hardware parameters set for this sub-stream" in the [ALSA proc docs](https://www.kernel.org/doc/html/latest/sound/designs/procfile.html)) shows:
   - `format` equal to the expected container (e.g. `S24_3LE`, `S32_LE`, `DSD_U32_BE`);
   - `rate` equal to the track's native rate (PCM), the DoP carrier rate (DSD rate ÷ 16) or the native DSD frame rate (DSD rate ÷ bits per container: DSD64 = 2,822,400 bit/s → 352,800 Hz for `DSD_U8`, 176,400 Hz for `DSD_U16_*`, 88,200 Hz for `DSD_U32_*`);
   - `channels` equal to the track's channel count.
3. **Exclusive path.** The substream's `status` shows the engine's PID as owner (**UNVERIFIED** that `owner_pid` is printed on 7.2; the `status` file is documented as "current status … elapsed time, H/W position, etc."), and no PipeWire node is linked to that card during the run (`pw-dump` shows the device reserved or absent).
4. **Rate switching.** For a playlist mixing rates and families (44.1 → 96 → 88.2 → 48 → DSD64 → 44.1), each track satisfies 1–3 with **its own** rate, and the transitions contain only silence.
5. **Unity volume.** With the engine volume at 0 dB, criteria 1–4 hold. With any other volume the engine must **report itself as not bit-perfect** (status API/UI flag), and the check verifies that the flag matches reality.
6. **Negative control.** The same vector routed through a known-lossy path fails criterion 1. If it does not, the run is invalid.

### 2.2 The software verification chain in the virtual T1 (L2, now)

```text
                                                    virtual T1 (Blackbird OS guest)
 host (macOS)                    ┌───────────────────────────────────────────────────────────────┐
 opendap verification check      │  test vector files ──► playback engine ──► hw:Loopback,0,N     │
   audio.bitperfect ──SSH──────► │   (FLAC/WAV/ALAC/DSF/DFF)    (exclusive ALSA)       │ snd-aloop │
   (orchestrates, compares)      │                                                    ▼           │
        ▲                        │  dap-verify capture: hw:Loopback,1,N ──► raw .bin + hw_params │
        │◄──scp results──────────│  snapshot of /proc/asound/Loopback/pcm0p/subN/{hw_params,status}│
        │                        └───────────────────────────────────────────────────────────────┘
  compare(reference decode, capture) → JSON report (requirement IDs, PASS/FAIL, first diff offset)
```

Steps:

1. **Load the loopback** with enough substreams: `modprobe snd-aloop pcm_substreams=2` (module parameter name per `aloop.c`; Debian builds `SND_ALOOP=m`, **UNVERIFIED** for the 7.2 backports config, to be checked first). Ship this as part of a **development system extension** (ADR blackbird-os-0004 already reserves dev extensions), never in the release image.
2. **Start capture first** at the expected format and rate. On `snd-aloop` the two ends are linked: once one end is open, the other must match its parameters. Opening capture first with the *expected* parameters turns a wrong engine choice into an `EINVAL` on the playback side, which is itself a useful assertion (behaviour from `aloop.c` constraints; **UNVERIFIED** in detail).
   - For per-track rate switching, the capture side must follow the playback side instead. The harness then opens capture *after* the engine opens playback, reads `hw_params` of the playback substream and opens capture with the same values.
3. **Capture the raw bytes** with a small purpose-built tool (`dap-verify capture`, Rust + `alsa` crate), not `arecord`. `arecord` writes WAV headers and cannot write DSD containers cleanly (**UNVERIFIED** for alsa-utils 1.2.16), and a dedicated tool can snapshot `hw_params` at each rate change and record xruns.
4. **Reference decode on the host.** For PCM: decode the source with FFmpeg to raw PCM in the same container (`-f s24le`, `s32le`, …) and hash it. FFmpeg 8 has the `hash`, `streamhash` and `framemd5` muxers ([FFmpeg formats](https://ffmpeg.org/ffmpeg-formats.html)) and a `null` muxer, and declares `dsf` and `iff` (DSDIFF) demuxers ([`allformats.c`](https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavformat/allformats.c)). For DSD the reference must be the **raw 1-bit stream**, not a decode: FFmpeg's DSD path is built to convert DSD to PCM, so OpenDAP needs its own small DSF/DFF reader (§2.5).
5. **Compare** with a purpose-built comparator: locate the reference's first 4,096 bytes in the capture (the vectors carry a unique sync preamble, §2.3), then compare byte by byte; report `first_mismatch_offset`, `mismatch_count`, `leading_silence`, `trailing_silence`, and the format/rate/channel observed vs expected.
6. **Record** the result (§3.3).

What this proves: the engine (and the decoder it links) delivers the file's samples unchanged, at the right rate and format, through a `hw:` device, with no plug, dmix, PipeWire graph, software volume or dither in the way. What it does not prove: anything below the ALSA PCM boundary (I2S, clocks, DAC).

### 2.3 Test vectors (to build)

Generated deterministically by a Rust crate (`opendap-testvectors`) from a seed, so any machine regenerates the same files and hashes. Each vector carries a 64-sample **sync preamble** (a fixed pseudo-random sequence) and an **end marker**, both in-band and full-scale, so the comparator can align without trusting timestamps.

| Vector | Content | What it catches |
|---|---|---|
| `prbs-full` | Uniform pseudo-random samples over the whole code range, every bit toggling, independent L/R seeds | Truncation, dither, gain ≠ 1, F32 round-trip of 32-bit, channel swap |
| `bitwalk` | One bit set per sample, walking LSB → MSB, alternating sign | Any single bit forced or dropped; byte-order errors in 3-byte containers |
| `extremes` | Runs of max positive, max negative, zero, ±1 LSB | Clipping, limiter, DC offset correction, "denormal" handling |
| `sine-997` | 997 Hz at −0.1 dBFS and −60 dBFS, then digital silence | Volume ramps, fades at start/stop, the silence-padding rule |
| `ramp16` | Every 16-bit code in order (65,536 samples per channel) | Lookup or remapping bugs in 16-bit paths |
| `channel-id` | Channel *n* carries the constant pattern 0x5A5A… xor *n* | Channel mapping/remapping (5.1 later, stereo now) |
| `low8-nonzero` (32-bit) | Random 32-bit samples whose low 8 bits are never all zero | The PipeWire F32 graph (negative control) |
| `jtest` | J-test: Fs/4 tone with an LSB square wave at Fs/192 (Dunn's J-test, **UNVERIFIED** exact definition to cite) | Reused later on hardware for jitter (analog metrics) |
| DSD `dsd-prbs` | Pseudo-random 1-bit stream (not musical, the DAC does not need to like it in L2) | Any bit corruption, bit-order reversal |
| DSD `dsd-silence-edge` | 0x69 silence, then data, then 0x69 | DoP marker continuity across silence; mute/unmute transitions |
| DSD `dsd-sine` | A 1 kHz sigma-delta modulated tone generated by a simple 5th-order modulator | Hardware-level sanity later (it actually sounds/measures as a tone) |

**Matrix (L2, per format):** rates {44.1, 48, 88.2, 96, 176.4, 192, 352.8, 384} kHz × containers {S16_LE, S24_3LE, S24_LE (in 32), S32_LE} × file formats {WAV, FLAC, ALAC (**UNVERIFIED** which formats the engine will support)} × channels {2}; DSD {DSD64, DSD128, DSD256} × {DSF, DFF} × output {native `DSD_U8`, `DSD_U16_LE/BE`, `DSD_U32_LE/BE`, DoP over `S24_3LE` and `S32_LE`}. FLAC at 32 bits is used only if the chosen libFLAC supports it (**UNVERIFIED**: FLAC ≥ 1.4 is believed to support 32-bit PCM). Rates above 384 kHz are capped by the DAC choice, not by the loopback.

Third-party "bit-perfect test files" exist (for example files whose decoded content says "pass" only when untouched, as used by HDCD/DTS-detect style tests); they are useful for **human spot checks** on hardware but cannot replace generated vectors, because their licences and content are not under OpenDAP's control (**UNVERIFIED** licences). The automated suite uses only generated vectors.

### 2.4 Constrained fake device for rate families and capabilities (to build)

`snd-aloop` accepts any rate, so it cannot show what the engine does when the device **refuses** a rate, or when the 44.1k family is only available after an MCLK switch (BRD-023). Two options, in order of preference:

1. **An ALSA `ioplug` PCM plugin** (`libasound_module_pcm_opendap_fake.so`, in `blackbird-os` dev repo) that enforces a **capability table** taken from the DAC datasheet and the board DT (allowed rates per MCLK, formats, DSD support yes/no), simulates the MCLK switch latency, and writes accepted samples to a file. The engine is pointed at it by device name in tests. It is user-space, so OS-002 (no custom kernel) is respected.
2. **Opening `snd-aloop` capture first with a fixed rate** to force refusals (§2.2 step 2). Cheaper, but it can model only one rate at a time.

Pass criteria added by the fake: the engine never asks for a rate outside the table; when the source rate is not supported, it **refuses or reports "not bit-perfect"** rather than resampling silently; when the family changes, it closes and reopens the PCM (observable as a new `hw_params`), and a configurable "settle" gap is respected.

### 2.5 DSD and DoP validators (to build)

**`dsd-src` reader (Rust):** DSF and DFF (DSDIFF) to a canonical in-memory form: per channel, a bit vector in **time order**.

- DSF: per channel 4096-byte blocks, interleaved per block; bits **LSB first** within each byte; last block zero-filled, so the reader must truncate to the declared sample count (spec annotation 3 in the [DSF spec](https://dsd-guide.com/sites/default/files/white-papers/DSFFileFormatSpec_E.pdf)).
- DFF: DSDIFF, MSB first, byte-interleaved channels (**UNVERIFIED** in this session; check against the Philips DSDIFF 1.5 spec before implementing).

#### `dop-check` (Rust, also exposed as `opendap verification check audio.dop` on capture files)

1. Accept S24_3LE or S32_LE frames (for S32 the DoP word is the top 24 bits; the low byte must be 0x00, non-zero low bytes are reported as a warning, since some DACs ignore them).
2. For every frame: all channels carry the same marker; markers alternate **0x05 ↔ 0xFA** from one frame to the next ([DoP v1.1 §2](https://dsd-guide.com/sites/default/files/white-papers/DoP_openStandard_1v1.pdf)). Count `marker_violations` and `marker_breaks` (a run interrupted by non-DoP PCM).
3. Extract the 16 payload bits, oldest bit (`t0`) in the most significant payload position, and append them to the channel's bit vector.
4. Check the PCM rate in `hw_params` = DSD rate ÷ 16 (176.4 kHz for DSD64, 352.8 kHz for DSD128 per the spec's §3 option 1). DSD256 at 705.6 kHz follows the same rule but is not in the v1.1 text (**UNVERIFIED** as a standard; widely used).
5. Optionally flag the spec's alternative 0x06/0xF9 marker scheme for 128FS on links limited to 176.4 kHz (spec §3 option 2) as "not used by OpenDAP" so it fails loudly instead of silently.
6. Compare the extracted bit vectors with `dsd-src`, aligned on the `dsd-prbs` preamble.

Pass: `marker_violations == 0` inside the DoP run, `marker_breaks == 0` for a single track, payload identical, rate correct.

**`dsd-native-check`:** the same comparison for `DSD_U8`, `DSD_U16_LE/BE`, `DSD_U32_LE/BE`. Byte order inside the container follows the format name; bit order inside each byte is MSB = oldest (**UNVERIFIED**: confirm against `snd-usb-audio` DSD quirk code or an ALSA developer before relying on it). Silence filling is checked against 0x69.

### 2.6 PipeWire vs exclusive ALSA (what the suite asserts)

| Path | Expected result | Why (source) |
|---|---|---|
| Engine → `hw:` exclusive (aloop) | PASS for all PCM containers, native DSD and DoP | No conversion in the path |
| Engine as PipeWire client, unity volume, `resample.disable`, matching rate, alone | PASS for S16 and S24; **FAIL for S32** `low8-nonzero`; **FAIL for DoP** | "The graph processing format is always float 32" ([pipewire-props(7)](https://docs.pipewire.org/page_man_pipewire-props_7.html)); a float-32 mantissa holds 24 bits |
| Same, with a notification sound mixed in | FAIL during the overlap | Mixing |
| Same, with `node.force-rate` ≠ track rate and `resample.disable` | Link refused | "The node will only be able to negotiate with the graph when the samplerates are compatible" (same source) |
| Engine with volume at −1 dB | FAIL on payload; engine flag says "not bit-perfect" | Criterion 5 |

These rows double as the **negative controls** of §2.1, criterion 6. PipeWire's dither modes are "only useful for conversion to a format with less than 24 bits and will be disabled otherwise" (same source), which the S16 row checks indirectly.

### 2.7 Real-hardware verification later (L3/L4)

| What | Method | Instrument |
|---|---|---|
| Bits on the I2S bus (PCM) and DSD lines | Capture BCLK/LRCK/SDATA (and DSD clock/data in DSD mode) at test pads; decode with sigrok's I2S decoder; compare with the same comparator | Logic analyser with ≥ 100 MS/s on 3–4 channels. 384 kHz × 32 bit × 2 ch = **24.576 MHz BCLK**; DSD256 = 11.29 MHz bit clock. sigrok support per model is **UNVERIFIED** |
| Bits to a USB DAC | Capture the isochronous OUT transfers on the T1 itself with `usbmon` and compare payloads; no external instrument | Software only (kernel `usbmon`, **UNVERIFIED** enabled in Debian arm64 config) |
| T1 as USB sound card (UAC2 gadget, BRD-034) | Host plays vectors to the T1; T1 captures and compares | Software only |
| Clock families exact (BRD-023) | Frequency counter / scope on MCLK test pad: 45.1584 MHz and 49.152 MHz within the oscillator's ppm; LRCK = rate exactly MCLK/N | Scope with frequency counter, or a counter with a TCXO/GPSDO reference |
| DAC actually in DSD mode | Read the DAC's status/mode register over I2C (**UNVERIFIED** that the CS43198 exposes a readable DSD-detect status) and measure the analog output of `dsd-sine` (it must be a 1 kHz tone, not −34 dB 88 kHz noise, per DoP v1.1 §2) | Audio analyser |
| End-to-end analog consistency | Play `sine-997` at 0 dBFS, measure level; compare 16/24/32-bit and DSD versions of the same tone | Audio analyser |

A **USB audio interface** on its own cannot prove bit-perfection of an analog output (the ADC adds noise and gain error). It can (a) receive S/PDIF from a USB DAC with a digital output and capture it bit-exactly, which verifies the T1's USB-host path end-to-end; (b) act as an L3 analog analyser for coarse metrics (analog metrics).

## 3. Architecture

### 3.1 Where things live

| Repository | Holds | Examples |
|---|---|---|
| `toolkit` | Host-side orchestration and analysers; the report schema; the traceability generator | `crates/opendap` verification commands; `crates/opendap-verify-model` (schema); `crates/opendap-testvectors`; `crates/opendap-audiocheck` (PCM comparator, `dop-check`, `dsd-native-check`, DSF/DFF reader); `crates/opendap-dose` reused from `system` or vice versa; Python analysis for THD+N/J-test under `tools/analysis/` with `uv` |
| `blackbird-os` | Guest-side helpers in the **development repository** (OS-015), the QEMU profiles and the fidelity path | `dap-verify` package (capture harness, `hw_params` snapshots, `faceplate-emu`, fixtures); dev system extension that loads `snd-aloop`; `build/dap-qemu.sh --fidelity` (U-Boot); scenario scripts for update/rollback |
| `system` | Unit and contract tests of the services | Playback engine (exclusive ALSA, rate switching, bit-perfect flag), dose service, battery/thermal readers with fixtures |
| `t1` | Product verification plan and matrix; faceplate interface source YAML and golden HID descriptors | `docs/verification/` (plan + generated status), `docs/specs/faceplate-interface.yaml` |
| `board` | Simulations and bring-up tests | `sim/power/` model, `sim/audio/` ngspice, `sim/si/` openEMS/scikit-rf, `test/bringup/` checklists, netlist rules |
| `hat-standard` | Manifest schema, reference implementation, test vectors, channel budget | `vectors/eeprom/`, `sim/channel/` |
| `case`, `faceplates/*` | Mechanical studies and checks | FEM studies, tolerance stacks, STEP checks |
| `kicad-lib` | Design-rule test boards (KLIB-002) | Expected-violation files |

### 3.2 CLI surface (aligned with the CLI conventions)

The CLI conventions use `noun verb` with a closed verb set that includes `check`, `list`, `get`.

| Command | Purpose | Target |
|---|---|---|
| `opendap verification list [--requirement BRD-023] [--level L2]` | List checks, their requirement IDs and levels | — |
| `opendap verification check <check-id>… [--target vm:default\|usb:…\|net:…]` | Run checks; NDJSON progress; final JSON report | VM, device or host |
| `opendap verification get <run-id>` | Read a stored result | — |
| `opendap requirement get <ID>` | Promise, checks, latest results, highest level reached | — |
| `opendap requirement list --status unverified` | What is not covered | — |
| Alias `opendap verify <area> [--bitperfect …]` | Documented shortcut mapping to `verification check` | — |

Initial check IDs: `audio.bitperfect.pcm`, `audio.bitperfect.dsd-native`, `audio.bitperfect.dop`, `audio.rate-switch`, `audio.negative-control`, `audio.analog` (needs analyser), `hearing.dose`, `power.model`, `power.proxies`, `power.suspend`, `driver.mainline`, `image.reproducible`, `image.signature`, `image.verity`, `update.rollback.*`, `vm.self-test`, `faceplate.hid.<profile>`, `hat.manifest`, `hat.channel`, `interface.consistency`, `chip.rp2350.*`, `mech.envelope`, `mech.stack.*`, `kicad.rules`.

`opendap doctor` gains checks for verification prerequisites: QEMU, the fidelity firmware, the dev extension, FFmpeg, `uv`, a Pico 2 present, and any bench instrument a check declares.

### 3.3 Results and traceability

**Result record** (JSON, `"schema":"opendap.verification-result/v1"`), one per check run:

```json
{"schema":"opendap.verification-result/v1",
 "check":"audio.bitperfect.dop","requirements":["OS-020","BRD-022"],
 "level":"L2","target":{"kind":"vm","name":"default","image":"blackbird-os-qemu 2026.10.22+abc123"},
 "tools":{"opendap":"0.1.0","dop-check":"0.1.0","ffmpeg":"8.1.1","kernel":"7.2.9+deb13-arm64"},
 "inputs":[{"vector":"dsd-prbs-dsd64.dsf","sha256":"…"}],
 "observed":{"hw_params":{"format":"S24_3LE","rate":176400,"channels":2},
             "marker_violations":0,"payload_mismatch_bits":0},
 "negative_control":{"check":"audio.negative-control.dop-pipewire","result":"fail-as-expected"},
 "result":"pass","started":"…","duration_s":42,
 "evidence":["results/2026-10-22T10-00Z/audio.bitperfect.dop/capture.bin.zst"]}
```

- **Storage (no CI):** results under `.state/verification/` in the workspace (not committed); a **release** attaches the result set (and evidence hashes) as an artefact and commits a regenerated `docs/verification/status.md` in each repository. This matches the existing local-release model.
- **Traceability:** checks declare their requirement IDs in code (attribute or registry), and `opendap requirement list` builds the matrix from the requirement READMEs + check registry + latest results. A requirement with no check is reported as **unverified**, never silently green.
- **Automation tiers** (no CI):
  - **pre-commit (seconds):** L0 checks on changed files: `interface.consistency`, netlist rules, `kicad.rules` (existing hooks), schema validation of manifests, dose and power-model unit tests, `driver.mainline` when the DT or parts list changes.
  - **on demand / pre-release (minutes to an hour):** L2 suites against the virtual T1 (`audio.*`, `faceplate.*`, `vm.self-test`, `update.*`, `image.*`).
  - **bench sessions (L3/L4):** the same commands with `--target usb:…`, plus instrument-backed checks; results recorded the same way.

## 4. First checks to run in the virtual T1 (all currently UNVERIFIED)

1. `snd-aloop` loads on the 7.2 backports kernel and `aplay -l`/`/proc/asound/cards` lists `Loopback`.
2. Playback opened with `DSD_U32_BE` at 88.2 kHz, `S24_3LE` at 352.8 kHz and `S32_LE` at 384 kHz succeeds on the loopback, and `hw_params` reports them.
3. `status` exposes the owning PID.
4. `/dev/uhid` exists and a `BUS_I2C` device with the core-set descriptor produces `KEY_PLAYPAUSE` etc.
5. QMP `input-send-event` supports multitouch slots in QEMU 11.1.
6. `s2idle` suspend and resume in the guest, woken by QMP.
7. U-Boot `qemu_arm64` boots under HVF (otherwise TCG for the fidelity path).
8. Debian config symbols: `SND_ALOOP`, `USB_DUMMY_HCD`, `LEDS_USER`, `IIO_SIMPLE_DUMMY`, `I2C_STUB`, `USB_MON`.

## 5. Traceability

Legend: **Now** = software/simulation/static verification available before hardware; **Later** = measurement on hardware; **Tool** = E existing / B to build. Levels as in §1.

| Req ID | Status | Promise | Software verification now | Hardware measurement later | Tool |
|---|---|---|---|---|---|
| OS-020 | Confirmed | Bit-perfect PCM, native DSD and DoP | L2 aloop capture vs generated vectors; `hw_params` oracle; DoP marker/payload check; native DSD check; PipeWire negative controls | I2S/DSD lines via logic analyser; `usbmon` for USB DACs; analog `dsd-sine` check | B: testvectors, audiocheck, dap-verify capture, dop-check; E: snd-aloop, FFmpeg 8, sigrok |
| BRD-023 | Draft | Two MCLK families, DAC master, no resampling at any rate | L2 constrained fake PCM (ioplug) with the clock table; rate-switch playlist | Counter on MCLK (45.1584/49.152 MHz, ppm); LRCK = MCLK/N; per-track `hw_params` on device | B: ioplug fake; E: scope/counter |
| BRD-022 | Draft | 2× CS43198 dual mono + 4× OPA1622; DSD capable | L0 netlist; L1 ngspice output stage; L2 DSD formats exposed by the codec driver (needs the real driver, so L3) | Analyser per channel; DSD mode readback | E: ngspice, audio analyser |
| T1-008 | Confirmed | Audio quality top priority | Synthetic-verified analysers; datasheet budget | THD+N, DNR, crosstalk, Zout, FR, J-test | B: analysis scripts; E: audio analyser |
| BRD-011 | Confirmed | 3.5 mm and 4.4 mm outputs | L0 netlist/STEP: both jacks present | Per-jack measurements | E |
| BRD-024 | Draft | 3.5 SE, 4.4 balanced (Pentaconn) | L0 netlist topology rule | Balanced vs SE level (+6 dB), crosstalk | B: rule; E: audio analyser |
| BRD-012 | Confirmed | Speaker independent of DAC | L0 netlist rule; L2 routing config consistency | Speaker plays with DAC powered down | B |
| SYS-012 | Draft | Sound dose 7-day, 85 dB(A), per-headphone | Golden dose vectors; window edge cases; L2 accelerated-clock warning test | Voltage per volume step (audio analyser); lab acoustic | B: dose crate |
| T1-009 | Confirmed | Max battery life | Power model; L2 wakeups/CPU proxies; s2idle test | power analyser scenarios, rundowns | B: model, proxies; E: power analyser |
| T1-012 | Confirmed | Standard cell ≥ 2,000 mAh | L0 BOM/case check of cell part and capacity | Capacity test (discharge at C/5) | E: electronic load |
| BRD-008 | Confirmed | Power down unused blocks | L0 every block has an enable/rail switch; L2 runtime-PM policy tests on fixtures | Per-rail off current via sense points | B |
| BRD-009 | Confirmed | Low-power mode | L2 s2idle enter/resume health | Suspend current, resume latency | E: power analyser |
| BRD-020 | Draft | RK809 PMIC | `driver.mainline` for `rk809` | Rail sequencing on scope | B |
| BRD-007 | Confirmed | Protection and safety | ngspice (where models run); netlist rules | Trip tests with supply/load | E/B |
| BRD-010 | Confirmed | Monitor CPU and temperature | Service tests on sysfs fixtures | Thermocouple/thermal camera vs reported | B |
| BRD-026 | Draft | eFuse per HAT rail | L0 netlist rule; ngspice | Short-circuit trip on each rail | B |
| HCS-015 | Draft | Power classes A/B/F | Schema + manifest vectors for classes; power-path sim | Source/sink tests with powerbank HAT | B |
| BRD-001 | Confirmed | Mainline drivers only | `driver.mainline` on pinned Debian kernel | Probe success on L3/L4 | B |
| OS-001 | Confirmed | Kernel ≥ 7 | Manifest check | — | B (trivial) |
| OS-002, OS-006 | Confirmed | No custom kernel / embedded builds | L0: image build log contains no kernel/toolchain compile; kernel from Debian archive | — | B |
| OS-007 | Draft | Kernel updatable as prebuilt package | L2: install newer backports kernel package in dev mode / via image update; boots | Same on device | E: apt, RAUC |
| FPT-008 | Draft | GT911 touch | `driver.mainline` (`goodix`) | Touch accuracy jig | B |
| FPT-018 | Confirmed | Panel with mainline driver (or OpenDAP adds it) | `driver.mainline` on the panel compatible | Panel init on L3 | B |
| BRD-025 | Draft | Pre-certified Wi-Fi/BT module | `driver.mainline` for module; certificate IDs recorded | iperf3, BT codecs | B/E |
| OS-004, OS-005, OS-010, OS-021 | Confirmed | Trimmed image from code and prebuilt packages; minimal default | Manifest allow-list + size budget; rebuild diff | Image fits/boots on device | B: image check; E: mkosi, diffoscope |
| OS-011 | Confirmed | Signed, versioned releases | Signature/verity verification + tamper tests | — | E: RAUC, veritysetup; B |
| OS-012 | Confirmed | Simple flashing | `dap flash --dry-run` plan tests | MaskROM flash + read-back on SBC/T1 | B (`rockusb`) |
| OS-013 | Confirmed | Updates from Debian and OpenDAP repos | `security-check.py`; APT repo snapshot test | — | E |
| OS-014 | Draft | OTA | L2 RAUC streaming install from local HTTP | Over Wi-Fi on device | E: RAUC |
| OS-017 | Draft | MaskROM recovery; automatic A/B rollback | L2 fidelity path scenarios (bad gate, power loss, verity corruption, bad signature) | Same with power switch; MaskROM recovery | B: scenarios; E: U-Boot, RAUC, labgrid |
| BRD-016, BRD-035 | Confirmed | MaskROM via main/power button | L0 netlist (button to boot-mode strap) | Enter MaskROM and flash | B/E |
| BRD-017 | Confirmed | Recovery key | L0 netlist; U-Boot script test in fidelity path (key → recovery) | Physical test | B |
| BRD-018, BRD-034 | Confirmed | USB-C OTG, flashing, host, charging… | L2 gadget roles with `dummy_hcd` (UNVERIFIED) | Each role on device; PD with USB-PD analyser | E/B |
| BRD-039 | Confirmed | Early console on the USB-C path | — (hardware path) | Console capture during boot | E |
| BRD-019 | Confirmed | Boot without RP firmware | L0 no boot-critical dependency on coprocessor service | Boot with RP2350 held in reset | B |
| OS-009, OS-016 | Confirmed | Run Blackbird OS on host (QEMU), headless automation | `vm.self-test` | — | E: dap-qemu.sh |
| OS-018 | Confirmed | Virtual T1 mimics hardware | `vm.self-test` (display, touch, buttons, audio; battery/RP/HAT gaps listed) | Compare with device behaviour | B |
| OS-019 | Confirmed | Remote control (desktop, SSH, screenshots, input) | QMP screenshot/press/tap round-trip tests | Same over USB/net on device | E: dap-qmp.py; B |
| OS-008, HCS-013 | Confirmed | Blackbird OS flashes/operates/debugs every chip | Pico 2 stand-in: load/verify/TBYB fallback/SWD | SoC-driven SWD on T1 and HATs | E: picotool, debugprobe, OpenOCD; B: chip check |
| OS-015 | Confirmed | Dev tools in a separate repo, tested on device | L2 install OpenOCD from dev repo, run `--version` and a self-test | Same on device | B |
| BRD-004 | Confirmed | SoC reaches every chip directly | L0 netlist: every programmable chip's debug/boot pins reach SoC GPIO/bus | Flash each chip from SoC | B |
| BRD-005 | Confirmed | Helper MCU never in a data path | L0 netlist rule | — | B |
| BRD-014, BRD-015 | Confirmed | RP coprocessor, flashable from Blackbird OS | Pico 2 stand-in | On board | E/B |
| T1-023, T1-024, FPK-009, FPC-009 | Confirmed | Standardized binary commands (HID core + vendor page) | `faceplate-emu` over UHID: every usage → expected event; vendor page → hidraw | Real controller over `i2c-hid`; I2C capture | B: faceplate-emu; E: hid-tools 0.12 |
| T1-020 | Confirmed | Identification by interface (HID VID/PID/version) | UHID test with each profile's IDs | Real faceplates | B |
| T1-018 | Confirmed | Faceplate interface defined | L0 spec completeness check (pinout, dimensions, anchoring present) | — | B |
| FPT-002, FPT-013 | Confirmed | 3.5" IPS touch display | L2 480×800 mode + touch mapping | Panel/touch on device | E/B |
| FPT-003, FPT-016, FPK-006, FPC-006 | Confirmed | Developer-controllable status LED | L2 `uleds` (UNVERIFIED) LED class test | LED on device | B |
| FPT-004, FPT-017, BRD-031 | Confirmed | Microphones (faceplate main, ambient top right) | L2 capture path bit-exact via aloop | Sensitivity/noise | B/E |
| FPT-010 | Draft | ALS + mic next to LED | L2 service fixtures (IIO dummy UNVERIFIED) | Lux meter | B |
| FPT-014, FPK-004, FPC-004 | Confirmed | Display + ALS dimming | L2 dimming logic on fixtures | Lux sweep vs backlight | B |
| FPT-015, FPK-005, FPC-005 | Confirmed | Display driver on the faceplate | L0 netlist split check | — | B |
| FPK-007/008, FPC-007/008 | Confirmed (should) | OLED, physical controls | Descriptor profiles for classic/cassette in `faceplate-emu` | Real faceplates | B |
| BRD-027 | Draft | Privacy LED wired to mic power | L0 netlist rule | Toggle mic power, observe LED | B |
| BRD-032 | Confirmed | IMU | `driver.mainline`; service fixtures | Six-orientation gravity test | B |
| BRD-036, T1-014, T1-015 | Confirmed | Buttons and layout; Action accessible | L2 QMP press → events; L0 case export feature positions | Physical test | E/B |
| T1-022 | Confirmed | Action button deeper travel, clearer click | Tolerance stack + switch datasheet | Force-displacement curves | B; E: force gauge |
| HCS-004, HCS-005 | Confirmed | Max speed per lane; compromises justified | Touchstone cascade vs budget; justification doc check | VNA/TDR; link speed/width; AER | B: channel check; E: scikit-rf, PyBERT |
| HCS-006, HCS-014 | Confirmed / Draft | One interconnect (pogo), passive HAT pads | L0 spec/STEP check | Contact resistance over cycles | B |
| BRD-006 | Confirmed | High-speed buses direct to HAT (muxes allowed) | L0 netlist rule | Link training | B |
| BRD-013 | Confirmed | Board owns flat-cable spec | L0 export present with Touchstone | — | B |
| BRD-033 | Confirmed (should) | PCIe on board optional | — | — | — |
| HCS-007, HCS-009, HCS-017 | Confirmed / Draft | EEPROM with HAT+ format + OpenDAP record | Reference parser + vectors + fuzzing | Real EEPROM read by SoC | B |
| HCS-010 | Confirmed | Manifest signed and verified | Signature vectors (valid/invalid/tampered) | — | B |
| HCS-008, HCS-011 | Confirmed | No central registry; reverse-domain via the package endpoint | L2 resolve ID through local package-endpoint stand-in | — | B |
| BRD-029, BRD-030 | Confirmed | Wi-Fi/BT; item tracking | `driver.mainline`; protocol tests on host BT (UNVERIFIED feasibility) | iperf3, range tests | B/E |
| BRD-038, OS-022 | Confirmed | SD/TF; caching in eMMC/RAM | L2 throttled virtio disk, underrun and cache tests | `fio` on real SD | B |
| BRD-037 | Draft | 4 GB RAM / 32 GB storage | L2 runs at 4 GB/32 GB (already the VM size); memory budget test | — | E |
| T1-013 | Confirmed | ~ID-1 footprint | L0 STEP bounding box | Calipers | B |
| T1-021 | Draft | Coordinate system, ISO 2768-m | L0 export origin/axes check | CMM/calipers on parts | B |
| T1-017 | Draft | IP65 (Commercial) | Gasket compression FEM; no OSS ingress check | Spray pre-check; lab IP65 | E: CalculiX; lab |
| CASE-006, HCS-012, HCS-020, CASE-024 | Confirmed | Swappable back/HAT case; screwed parts | L0 assembly interference; boss FEM | Fit and cycle tests | E/B |
| CASE-025 | Confirmed | Snap-on plain back | Snap-fit strain FEM | Cycle test | E |
| CASE-020, CASE-021, HCS-019 | Confirmed | Polymer, graphite grip finish, metal lock allowed | L0 material fields in exports | Sample inspection | B |
| T1-007 | Confirmed | Assembled and simulated from exports | L0 `component.yaml` + assembly build from exports only | — | B |
| KLIB-002 | Draft | Design rules with triggering test boards | DRC expects listed violations | — | E: kicad-cli; B |
| SYS-001…SYS-011 | Draft | Capabilities, bus, activities, registry, packages via the package endpoint | Contract tests once APIs exist; L2 egress-blocked package-endpoint test | — | B |
| DS-001 | Confirmed | UI follows design system | Screenshot comparison in L2 | — | B |
| TK-001…TK-011, T1-025 | Confirmed | Toolkit CLI properties | Cargo tests, schema/exit-code conformance, install-script tests in temp HOME | — | E/B |
| T1-001, BLIB-002 | Confirmed | Open source; licences | `reuse lint` (pre-commit) | — | E: reuse |
| T1-002, T1-003, BRD-003, HCS-001…003 | Confirmed | General-purpose gadget; HATs and faceplates | Covered by P14–P18 | — | — |
| T1-019 | Confirmed | Faceplates not swapped regularly | Informs flex/connector cycle rating (no test) | — | — |
| KLIB-001, FLIB-001, BLIB-001 | Confirmed | Shared libraries | Library lint (existing skills) | — | E |
| CAM-005, NAS-005/006, OSC-005, PWR-005, RAD-005, REC-005, VNA-005 (+ *-001…004) | Confirmed / Draft | HAT products | Per-HAT plans; REC reuses the bit-perfect harness | Per-HAT bench tests | B |

## 6. Sources (accessed 2026-10-21/22)

- Linux `snd-aloop` formats and rates: <https://raw.githubusercontent.com/torvalds/linux/master/sound/drivers/aloop.c>
- Linux DSD silence/physical widths: <https://raw.githubusercontent.com/torvalds/linux/master/sound/core/pcm_misc.c>
- ALSA proc files (`hw_params`, `status`): <https://www.kernel.org/doc/html/latest/sound/designs/procfile.html>
- QEMU virtio-snd formats/rates: <https://raw.githubusercontent.com/qemu/qemu/master/hw/audio/virtio-snd.c>
- QEMU QMP reference: <https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html>
- DoP open standard v1.1: <https://dsd-guide.com/sites/default/files/white-papers/DoP_openStandard_1v1.pdf>
- Sony DSF file format specification: <https://dsd-guide.com/sites/default/files/white-papers/DSFFileFormatSpec_E.pdf>
- PipeWire properties (F32 graph, resample/channelmix/dither, force-rate): <https://docs.pipewire.org/page_man_pipewire-props_7.html>
- FFmpeg formats (hash muxers): <https://ffmpeg.org/ffmpeg-formats.html> ; registry (dsf, iff, null): <https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavformat/allformats.c>
- ALSA project releases (alsa-lib/utils 1.2.16, 1.2.16.1): <https://www.alsa-project.org/wiki/Main_Page>
- UHID: <https://www.kernel.org/doc/html/latest/hid/uhid.html> ; header: <https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/uhid.h>
- hid-tools 0.12: <https://pypi.org/project/hid-tools/>
- test_power parameters: <https://raw.githubusercontent.com/torvalds/linux/master/drivers/power/supply/test_power.c>
- U-Boot on QEMU arm64: <https://docs.u-boot.org/en/latest/board/emulation/qemu-arm.html>
- RAUC releases: <https://github.com/rauc/rauc/releases> ; integration: <https://rauc.readthedocs.io/en/latest/integration.html>
- dm-verity: <https://docs.kernel.org/admin-guide/device-mapper/verity.html>
- Renode releases: <https://github.com/renode/renode/releases>
- rp2040js: <https://github.com/wokwi/rp2040js>
- picotool README: <https://raw.githubusercontent.com/raspberrypi/picotool/master/README.md>
- debugprobe: <https://github.com/raspberrypi/debugprobe>
