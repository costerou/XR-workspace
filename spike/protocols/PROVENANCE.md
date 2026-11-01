# Protocol provenance and notices

## wlr-layer-shell

Vendored byte-for-byte from backend-decision source [20]:

https://raw.githubusercontent.com/swaywm/wlr-protocols/b010a03648b88d143236de193bddbfea0c08bc84/unstable/wlr-layer-shell-unstable-v1.xml

SHA-256: `996b8c66e1c2276c443336d4cb174634e8e5948823779042f244930525b79cc2`.
Retrieved by explicit source-only HTTPS request during offline Task 3; CMake never
fetches anything. **This pinned XML is version 4**, even though the inspected
compositor advertises version 5. Bind `min(advertised, 4)`, not version 5.
`wayland-scanner` generates actual client headers and C protocol descriptors in
the build directory. Its C parameter `namespace` is adapted only in the C++
include scope; the upstream XML is unchanged.

Copyright © 2017 Drew DeVault

Permission to use, copy, modify, distribute, and sell this software and its
documentation for any purpose is hereby granted without fee, provided that the
above copyright notice appear in all copies and that both that copyright notice
and this permission notice appear in supporting documentation, and that the name
of the copyright holders not be used in advertising or publicity pertaining to
distribution of the software without specific, written prior permission. The
copyright holders make no representations about the suitability of this software
for any purpose. It is provided "as is" without express or implied warranty.

THE COPYRIGHT HOLDERS DISCLAIM ALL WARRANTIES WITH REGARD TO THIS SOFTWARE,
INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS, IN NO EVENT
SHALL THE COPYRIGHT HOLDERS BE LIABLE FOR ANY SPECIAL, INDIRECT OR CONSEQUENTIAL
DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING
OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

## xdg-shell reference symbols

Layer-shell's protocol descriptor references `xdg_popup_interface`. Build its
supporting descriptor from the **installed** wayland-protocols
`stable/xdg-shell/xdg-shell.xml` (pkg-config package version 1.49 for this build).
The renderer does not bind xdg-shell or create popups. Upstream copyright notices
are carried by scanner-generated sources. No installed XML is copied into Git.

No screencopy, SDK or vendor headers are vendored or generated in this task.
