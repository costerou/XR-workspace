# VITURE SDK licence: what it allows us

Recorded 2026-11-10 for the Rust rebuild (phase 0, plan decision 3 and question 3 in
[../../rust-rebuild-plan.md](../../rust-rebuild-plan.md)). This is a reading of the published
terms by an engineer, not legal advice. Where it says "ask VITURE", only VITURE can settle it.

## The text

- [viture-sdk-license-agreement-2025-09.txt](viture-sdk-license-agreement-2025-09.txt): the
  VITURE SDK License Agreement, "Effective September 2025", fetched 2026-11-10 from
  <https://www.viture.com/viture-sdk-license-agreement>.
- [viture-sdk-open-source-notice-2025-09.txt](viture-sdk-open-source-notice-2025-09.txt): the
  open-source notice VITURE publishes next to it. For the "XR Glasses SDK" it lists Flatbuffers,
  OpenCV 4.2.0, HIDAPI, yaml-cpp, Eigen, libusb (LGPL-2.1), tiny-AES-c, libuvc, cpp-httplib,
  Mbed TLS and raylib.

No licence file ships with the SDK we load. The installed package and its upstream source were
searched:

| What | Result |
| --- | --- |
| `/usr/lib/xr-driver/libglasses.so` | SHA-256 `8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e`, the hash `tools/pro2_runtime` pins. |
| Owner | `pacman -Qo`: package `xr-driver-git 2.11.9-1`. Its declared licence, `GPL-3.0-or-later`, covers XRLinuxDriver, not the VITURE binaries. |
| Package files | `pacman -Ql xr-driver-git`: no licence, notice or readme file for VITURE. Nothing under `/usr/share/licenses` for VITURE. |
| Upstream | PKGBUILD builds <https://github.com/wheaney/XRLinuxDriver> at `36e2139dd482f5984112d0c3ebafcc07a78f99c0`. Its `lib/x86_64/viture/libglasses.so` (blob `ce1300b3`, VITURE SDK v2.4.0, commit `dd0d592`, 2026-08-14) is byte-identical to the installed file once makepkg's `.gnu_debuglink` section is removed from both. Its only `LICENSE` is the GPL-3.0 text. |
| SDK headers upstream | `include/sdks/viture_*.h`: "Copyright (C) 2025 VITURE Inc. All rights reserved." No licence grant. |
| Upstream README | VITURE row: "Official collaboration. Closed source SDK", linking VITURE's developer page. |
| VITURE developer page | <https://www.viture.com/developer/glasses-sdk/glasses>: no licence on the page; sample code says "Copyright (C) 2026 Viture Inc. All rights reserved." |
| `strings libglasses.so` | No licence or copyright text. |

The agreement says it "applies to any SDK that includes, displays, or links to this Agreement".
The copy we load does none of those. It is still VITURE's only published SDK terms, so we treat
it as binding. XRLinuxDriver ships the binaries under its own arrangement with VITURE. That
arrangement does not pass to us.

## What it allows

Section 1 grants a limited, personal, non-transferable, non-sublicensable, royalty-free licence
to:

1. Install and use the SDK in object code form only, to develop programs designed to function
   with VITURE products ("Developed Programs").
2. Distribute the SDK in object code form only, as a component of Developed Programs.

**Loading `libglasses.so` from our own host is allowed.** The C++ bridge and the planned Rust
`xr-pro2-bridge` open the user's installed copy with `dlopen` and call its exported C API to
drive VITURE glasses. That is use in object code form for a Developed Program. Nothing in the
agreement ties the host language.

**Redistribution is allowed only under conditions we do not meet**, so we do not redistribute.
Section 2 requires every distribution to:

- come with an end-user licence that forbids reverse engineering of the SDK and makes end users
  indemnify VITURE;
- carry all VITURE copyright and trademark notices;
- never put the SDK under a licence that requires source disclosure, modification rights or
  free redistribution (a copyleft or permissive open-source licence for the bundle).

This repo is public on GitHub and carries no licence or EULA of its own. Bundling the SDK would
need a VITURE-specific EULA and a closed-licence bundle. Ask VITURE (`bd@viture.com`) first.

## What it forbids that touches this project

Section 2 also says we will not:

| Clause (paraphrased; see the text) | Where this project stands |
| --- | --- |
| Modify, disassemble, decompile, reverse engineer or tamper with the SDK. | Static disassembly of the installed binary is recorded in [../driver-provider-audit.md](../driver-provider-audit.md) (line 17), [../protocol-evidence.md](../protocol-evidence.md) (line 60) and [../../tracking-3dof-ab.md](../../tracking-3dof-ab.md) (line 28). `spike/driver/research/dof3_preload.c` wraps an SDK call with `LD_PRELOAD`. **Do no more of this.** Whether to remove the passages already published is Cody's call. |
| Incorporate any part of the SDK into other software, or create a derivative work. | `dlopen` of the unmodified installed file is use, not incorporation. Keep it that way: no static linking, no copying the file, no patching it. |
| Copy, distribute, sublicense or publish the SDK for others to copy. | No VITURE binary or header is tracked in git (`tests/pro2/test_sdk_licence.py` checks it). The bundle in `tools/pro2_runtime/package.py` copies only git-tracked files plus our own binaries. |
| Build a program that "exposes and/or discloses header file information". | `tools/pro2_runtime/bridge.cpp` and `docs/pro2/diagnostic-evidence/symbols.txt` name exported functions and constants. The Rust bindings will declare the signatures they call. Declaring only what we call is the least we can do; whether that is acceptable is a question for VITURE. |
| Disclose the results of performance benchmarks of the SDK to a third party without VITURE's written consent. | The repo publishes measurements of Pro 2 runs (`docs/pro2/hil-evidence`, smoothness notes). Those measure our pipeline, but they include the SDK's pose path. Keep SDK-specific timing out of anything shared until VITURE agrees. |
| Use "VITURE" in the name of an application developed with the SDK, or VITURE trademarks in marketing. | The product is "XR Workspace". Some local paths still say `viture-workspace` (`~/.config/viture-workspace`). They are not a product name; avoid it in any new name. |

Other terms that matter:

- **Data collection (section 8):** the SDK may report usage data to VITURE unless we opt out.
  The bridge runs under `unshare -Urn` (no network), so it cannot report. Keep the Rust host
  network-denied too.
- **Termination (section 10):** VITURE may end the licence at any time. We must then destroy all
  copies of the SDK. Loading only the user's installed copy means we hold none.
- **No warranty, indemnity, California law (sections 11 to 14).**

## Rules for the Rust rebuild

These follow from the text and replace "until the licence text is found" in plan decision 3:

1. Load `libglasses.so` at run time from the user's installed package
   (`/usr/lib/xr-driver/libglasses.so`), through a pinned file descriptor after the SHA-256
   check, as today. No `build.rs` link to it: the crates build without the SDK present.
2. Never copy, commit, vendor or bundle any VITURE binary, header or data file.
3. Declare only the SDK functions and constants we call, in our own words. Do not copy VITURE
   header text.
4. No disassembly, decompilation or patching of the SDK.
5. Keep the SDK process without network access.
6. Do not publish benchmark results of the SDK itself without VITURE's written consent.

## Questions for VITURE

Draft for Cody to send to `bd@viture.com` if redistribution, the header clause or benchmarks
ever matter:

> We build an open-source Linux desktop for VITURE glasses. It loads `libglasses.so` from the
> user's XRLinuxDriver install at run time and does not ship it. (1) Is that use covered by the
> VITURE SDK License Agreement of September 2025? (2) May an open-source program declare the
> signatures of the exported C functions it calls? (3) May we publish latency and smoothness
> measurements of our pipeline that include the SDK's pose output? (4) On what terms could we
> bundle the SDK in a package?
