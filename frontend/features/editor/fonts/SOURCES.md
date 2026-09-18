# Caption font sources

These are the eight caption families a composition may name (`FontFamily` in
`backend/src/clipah/editor/models.py`). The editor preview loads them with `next/font/local`,
and the media image copies the same files into `/usr/local/share/fonts/clipah/`, so a caption
is previewed and rendered from identical font files.

- Source: [google/fonts](https://github.com/google/fonts) at commit `1edf95b4328bc5997ca93d2c0c7205272ec7347f`.
- Licence: every family here is under the SIL Open Font License 1.1; each family's licence text
  is kept beside its fonts as `<Family>-OFL.txt`.
- Variable fonts are used where the family ships one (Inter, Montserrat, Roboto, Open Sans,
  Nunito); Poppins, Bebas Neue, and Anton are static files. Italic files are not vendored.
- Every file name starts with the family name without spaces; a deployment contract test checks
  this.

| File | Source path | SHA-256 |
| --- | --- | --- |
| `Anton-OFL.txt` | `ofl/anton/OFL.txt` | `ee67e6ee22790b7929f1a3769ca2801d565c64b5a9096942c1adf5596de9c9e4` |
| `Anton-Regular.ttf` | `ofl/anton/Anton-Regular.ttf` | `a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab` |
| `BebasNeue-OFL.txt` | `ofl/bebasneue/OFL.txt` | `72082f6cb4d04be2ecf7cc7d9e1e7d73787f0af8a5a278a47cade70c16b78341` |
| `BebasNeue-Regular.ttf` | `ofl/bebasneue/BebasNeue-Regular.ttf` | `08e4623805102d819f58601e46e345648846075e363b2ceb23313c2d1c83ec73` |
| `Inter-OFL.txt` | `ofl/inter/OFL.txt` | `5b9321a4298cfeb6b34354164a1c3afc3db114569984c502b9b35d988fd58c57` |
| `Inter-Variable.ttf` | `ofl/inter/Inter[opsz,wght].ttf` | `29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031` |
| `Montserrat-OFL.txt` | `ofl/montserrat/OFL.txt` | `8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e` |
| `Montserrat-Variable.ttf` | `ofl/montserrat/Montserrat[wght].ttf` | `0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7` |
| `Nunito-OFL.txt` | `ofl/nunito/OFL.txt` | `580df76c95a1ec5ab878ceb25bb3d85c6a076804e9c970c8c6972aea775fdf65` |
| `Nunito-Variable.ttf` | `ofl/nunito/Nunito[wght].ttf` | `bb55a5ca5c2042335b3991af27c4d0705d0ef41cac6164ac737fd8f2a1e85207` |
| `OpenSans-OFL.txt` | `ofl/opensans/OFL.txt` | `fbbbcfef55318de350562559b671360de6d597112ecc5c73881b05092db89602` |
| `OpenSans-Variable.ttf` | `ofl/opensans/OpenSans[wdth,wght].ttf` | `36643644f318a812aab2d2ed3bb98f8cf0872527f835fe9398d95fe6b9adb878` |
| `Poppins-Black.ttf` | `ofl/poppins/Poppins-Black.ttf` | `d82aaaf98a9283f9a8edd24e51173337d8eaf09e25cd3d98831f8ec8461748a1` |
| `Poppins-Bold.ttf` | `ofl/poppins/Poppins-Bold.ttf` | `983676516167748b74de6f4771fb384c664fd913acb8b471122ecacf5da5ea6c` |
| `Poppins-ExtraBold.ttf` | `ofl/poppins/Poppins-ExtraBold.ttf` | `f2ab17c1a63a0ecc12c2461848fc8a469395e3cd2d641803e889c643d9f958e1` |
| `Poppins-Light.ttf` | `ofl/poppins/Poppins-Light.ttf` | `650ba57fa99d12ec40c31ccfb680be656be4497fbe14164617d67e32ffe9cd46` |
| `Poppins-Medium.ttf` | `ofl/poppins/Poppins-Medium.ttf` | `90373e7d838d32468438fc3e152dca0bdb12edcab99ea639f158790b1ba1fd05` |
| `Poppins-OFL.txt` | `ofl/poppins/OFL.txt` | `6be04893d770899a015649c7aa3b582f871b272f8747a92b78b17c3e5c8b2573` |
| `Poppins-Regular.ttf` | `ofl/poppins/Poppins-Regular.ttf` | `7e65201e9b79159e2300267cc885e16c8dcef2424cdfa09a29bfb0980a94a7ba` |
| `Poppins-SemiBold.ttf` | `ofl/poppins/Poppins-SemiBold.ttf` | `d3bf1bdaf0550e83da9ac0b1d1d9fe6db086835a83aa28578e609a394b9a0286` |
| `Roboto-OFL.txt` | `ofl/roboto/OFL.txt` | `061402327a96aadb0bfb694a960ed289ecd38d383e396243831ab81feb109c41` |
| `Roboto-Variable.ttf` | `ofl/roboto/Roboto[wdth,wght].ttf` | `d7598e12c5dbef095ff8272cfc55da0250bd07fbdecbac8a530b9b277872a134` |
