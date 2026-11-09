# Vendored DaisySP subset (MIT)

These files are DaisySP by Electrosmith, Corp. (with code by Emilie Gillet from Plaits), published under the MIT
license in `LICENSE` (kept with them, unchanged). They are **unmodified** copies from
https://github.com/electro-smith/DaisySP at commit `2c72eaf9eac5fc0dca1919d65d606da907832618`, the commit
bento-playground 9fc7787 names in its own `eu4_work/drums/vendor/daisysp/UPSTREAM.json`. The SHA-256 of the
LICENSE and the four Drums / Filters files match the `original_sha256` values listed there; `Utility/dsp.h` has no
entry there and was checked against the file at that commit (both S6c reviews fetched the commit and compared every
file byte for byte). bento-playground's own copies are modified; ours are not:

| File | SHA-256 |
|---|---|
| LICENSE | ae36576d7394109775a8cc86c31c94f016de256526e86d11ee554ef25d429570 |
| Source/Drums/synthbassdrum.cpp | 151345fed1088f8a6e5379522a62a6dfc9a43e29bb014c590bc8dea8f6a34a7d |
| Source/Drums/synthbassdrum.h | d0dcf35534bc0e7e132c6ac5c89a4c23a5cb4017952e1f1c4f3a528adc98d953 |
| Source/Filters/svf.cpp | 27ff7d834b9c6d705570bec43079c752028c15d4bfb00c97140a42508ec87ce0 |
| Source/Filters/svf.h | 97356a57d038421c5df461d22de2c2ad02b27e92067c65275791da4831620bca |
| Source/Utility/dsp.h | 534f0f76b7794939a5fff9b128b8fddb23ae0e63ad3354f663445d417924099a |

`test_drums.py` (class `Vendored`) re-checks these hashes, so an edit here fails a suite.

How they are built (S6c, `docs/plan-synths-168.md`): inside the hard-float DSP ELF, with the freestanding headers in
`../shim/` standing in for the C/C++ library headers they include (`<cmath>`, `<math.h>`, `<stdlib.h>`, `<cassert>`,
`<cstdint>`, `<random>`). The library functions they call come from our own code, not from a libc or libm:

- `sinf`, `powf`, `log10f`: `../dsp_lib.cpp` (single precision polynomials; no double, no table of stock's;
  `powf(2, b)` remembers its last two results in the drum run state, because the Kick's two per-sample envelope
  coefficients are constant within a block);
- `rand()`: `../drums.cpp`, a xorshift32 whose state is the drum cell being rendered (per voice, in the synth area),
  so voices never share a noise sequence and the ELF keeps no RAM data of its own (upstream's global `rand()` state
  would need `.data`, which the build refuses).

Only the Kick (S6c) uses them: `SyntheticBassDrum` and the `Svf` its click filter needs.
