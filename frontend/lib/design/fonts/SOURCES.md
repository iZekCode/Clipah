# Vendored fonts

Signal's two interface faces are served from this directory through `next/font/local`, so
builds are reproducible offline and never fetch fonts at build time.

Both files come from the Google Fonts repository at commit
`a54f7446f84a1125ef6bf08baa46f3639e8905e0`, downloaded on 2026-09-18.

| File | Source | SHA-256 | Axes |
| --- | --- | --- | --- |
| `Archivo-Variable.ttf` | https://raw.githubusercontent.com/google/fonts/a54f7446f84a1125ef6bf08baa46f3639e8905e0/ofl/archivo/Archivo%5Bwdth,wght%5D.ttf | `0e094a7d3c7c4c25cf1310c4b30014f1dae9332220b1c2c88f4fa996f0b05053` | `wght` 100–900, `wdth` 62–125 |
| `JetBrainsMono-Variable.ttf` | https://raw.githubusercontent.com/google/fonts/a54f7446f84a1125ef6bf08baa46f3639e8905e0/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf | `48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda` | `wght` 100–800 |

## Licence

Both families are licensed under the SIL Open Font License, version 1.1. Each licence is
kept beside its font: `OFL-Archivo.txt` (from `ofl/archivo/OFL.txt`) and
`OFL-JetBrainsMono.txt` (from `ofl/jetbrainsmono/OFL.txt`), at the same commit.

To update a font, download it from a newer commit, replace the file, and update the commit,
URL, and digest here (`shasum -a 256 <file>`).
