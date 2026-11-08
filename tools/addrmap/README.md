# addrmap: map stock addresses from one firmware build to the next

`match.py` maps addresses of one stock main image (`bento1.bin`, 0x08040000) to another by
aligning position-independent instruction tokens, then content search for data (method in its
docstring). `gen_table.py` uses it to draft the next base's symbol table from the current one:

```
python -I gen_table.py ~/bento/stock-1.6.1/bento1.bin ~/bento/stock-1.6.8/bento1.bin \
    --from stock_161 --to stock_168 --version 1.6.8          # writes mods/m1/bases/stock_168.py
python -I match.py OLD.bin NEW.bin 0x0805F8E0 ...           # ad-hoc lookups
```

Each drafted entry carries the mapped value, `was` (the old value) and `status` (exact / moved /
inferred / changed / unmatched / fact / reviewed / ours); anything that is not exact, not moved
with high confidence and not a verified fact gets `REVIEW="why"`. Every entry the matcher would
accept is then cross-checked between the two images (code: first instructions with branches,
PC-relative operands and literal-pool words masked; data: first 16 bytes with flash pointers
masked; RAM: literal counts; keys / offsets / events: `mods/m1/expected_values.py`), and a
disagreement becomes `REVIEW` too. A content match on a data window that starts with zeros is
treated as low confidence (that is how `HEADER_TEMPLATES` was first mapped wrongly).
`build.py` refuses to build a feature that uses an entry with `REVIEW` or no value (or that no
sprint has ported to the base yet: `BASE_SETTINGS` in build.py), and leaves a disabled one out of
the image, so no draft value reaches a build and no old value is used silently; other readers get an error on a `REVIEW` entry unless they ask
for the draft.

Reviewing an entry means checking it in the new image (for a hook: the assumptions the wrapper
makes there, as S5 does), then recording it in `facts_<old>_<new>/facts.py` `REVIEWED`
(value, evidence, and `orig` for a patch site) and, for a key / offset / event, in
`mods/m1/expected_values.py`, and rerunning the generator. Do not edit the generated table: a
rerun rewrites it. A `REVIEWED` entry may carry `note=` (replacing the old base's note, when it no
longer describes the new value) and, for an entry that does not exist on the new base,
`absent=True` with `value=None` (status `absent`: reviewed, no value, left out of the C header). The generator re-checks every `REVIEWED` entry against the image and makes it
`REVIEW` again if its bytes no longer match. `HINTS` in facts.py carries evidence for entries
that are still open (appended to their `REVIEW`).

`fingerprints.py TABLE IMAGE [--check]` writes (or compares) a table's `CHECKS`: a fingerprint of
the image at every code / data address and the literal count of every RAM address, which
`build.py` verifies. The generator writes it for the table it drafts; for the hand-maintained
`stock_161.py`, rerun it after deliberately changing an address.

`facts_161_168/`: the verified extras for 1.6.1 -> 1.6.8 (workflow map-161-to-168):
`facts.py` (hand checks, layout facts, our placement that has to move), `registry_keys.txt`
(measured key renumbering, numbers only), `ram_xlate.json` (per-core votes: RAM literal in 1.6.1
-> the literal at the same slot in 1.6.8; every RAM-range literal of both images, which the
generator also uses to infer fields of globals from their neighbours), `hand_checks.csv` (29
matcher results checked by hand). In `registry_keys.txt`, old keys 0x13A, 0x13B and 0x13C (In 2,
In 3, LED Brightness) all map to 0x13A: that part of the list is unreliable; the generator marks
any key in a many-to-one cluster `REVIEW`.

Neither image is stored here. The tables hold only what the build checks against: 4-byte
instruction encodings at patch sites, the 8-byte version string, SHA-256 fingerprints and
literal counts.
Runs in WSL with the venv's capstone (~20 s for both images).
