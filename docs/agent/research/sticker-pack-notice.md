# Default sticker pack notice

The default local QQ sticker pack is installed at:

```text
D:\AstrBot\data\sticker_packs\aiji
```

It contains eight images copied from `cheems135/dsh-qq-bridge`
(`assets/preset/`), licensed under the MIT License:

https://github.com/cheems135/dsh-qq-bridge

Copyright (c) 2026 cheems135

The images are used as local chat stickers. They are not claimed as original
AIJiYi artwork. Each image is selected by mood, then sent as its own QQ image
message; text replies are sanitized so `[表情]`, emoji and CQ face markup never
appear in the text bubble.

AIJiYi may also merge newer images from this public MIT-licensed feed:

```text
https://raw.githubusercontent.com/cheems135/dsh-qq-bridge/main/assets/preset/manifest.json
```

Remote updates are opt-in configurable, restricted to public HTTP(S), validated
by image signature and size, written atomically, and merged with local custom
stickers rather than deleting them.

Replace the pack by editing `manifest.json` or deleting the directory.
