# Third-party source

This package includes an adaptation patch for [channprj/kmsg](https://github.com/channprj/kmsg), pinned to commit `54cbff790b409214735ef365a621d791034fe500` (VERSION `1.260921.0`).

The upstream project is MIT licensed, copyright 2026 Park Hee Chan. Its notice is retained in `patches/kmsg-LICENSE`. The patch narrows text sending to one exact-title open window, preserves nonempty drafts, removes forced typing and repeated commit, and avoids automatic permission prompts/authentication fallback.

Source is downloaded only when bootstrap is explicitly run. No upstream binary, private app artifact, chat database, account identifier, or local execution log is bundled.

The read-only probe and workflow documentation were derived from this project's AX experiments. The package's generic examples are not actual recipients or messages.
