// SPDX-License-Identifier: GPL-3.0-or-later
// bento_enhanced web patcher: applies a release's patch (web/patches/v<ver>.json) to the user's own stock
// Bento 1.6.8 bento1.bin, verifies the result, and packs a flashing kit as a zip. Everything runs in this page:
// no file is uploaded anywhere. Plain JS, no build step, no dependencies. The same file runs under node for the
// tests (tools/test_web_patch.py): the logic is in BEP, the page wiring at the bottom runs only in a browser.
// Format and policy: docs/web-patcher.md (the Python reference is tools/export_patch.py apply_patch).
(function (root) {
  "use strict";

  const FORMAT = "bento_enhanced-webpatch";
  const FORMAT_VERSION = 1;
  const BEP = {};

  // ------------------------------------------------------------------ bytes, hashes

  function hex(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i++) s += bytes[i].toString(16).padStart(2, "0");
    return s;
  }

  async function sha256(bytes) {
    const d = await root.crypto.subtle.digest("SHA-256", bytes);
    return hex(new Uint8Array(d));
  }

  function b64decode(s) {
    const bin = root.atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  const CRC_TABLE = (function () {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  const utf8 = (s) => new TextEncoder().encode(s);

  // ------------------------------------------------------------------ the patch

  // The output starts as stock bento1.bin truncated / zero-extended to output_size; the ops run in order.
  // W [dst, len]: the next len bytes of the payload. C [dst, src, len]: from the ORIGINAL stock bento1.bin.
  // D [dst, src, len]: from stock bento2.bin. F [dst, len, byte]: a constant run. Any op out of bounds rejects
  // the whole patch.
  function applyPatch(stock1, patch, payload, stock2) {
    if (patch.format !== FORMAT || patch.format_version !== FORMAT_VERSION)
      throw new Error("unknown patch format (this page is older or newer than the patch)");
    const size = patch.output_size;
    if (!Number.isInteger(size) || size <= 0 || size > 64 * 1024 * 1024) throw new Error("bad output_size");
    const out = new Uint8Array(size);              // zero-filled
    out.set(stock1.subarray(0, Math.min(size, stock1.length)));
    let p = 0;
    const inOut = (dst, n) => Number.isInteger(dst) && Number.isInteger(n) && dst >= 0 && n > 0 && dst + n <= size;
    for (const op of patch.ops) {
      const kind = op[0];
      if (kind === "W") {
        const [, dst, n] = op;
        if (!inOut(dst, n) || p + n > payload.length) throw new Error("bad WRITE op " + JSON.stringify(op));
        out.set(payload.subarray(p, p + n), dst);
        p += n;
      } else if (kind === "C" || kind === "D") {
        const [, dst, src, n] = op;
        const from = kind === "C" ? stock1 : stock2;
        if (!from || !inOut(dst, n) || !Number.isInteger(src) || src < 0 || src + n > from.length)
          throw new Error("bad COPY op " + JSON.stringify(op));
        out.set(from.subarray(src, src + n), dst);
      } else if (kind === "F") {
        const [, dst, n, v] = op;
        if (!inOut(dst, n) || !Number.isInteger(v) || v < 0 || v > 255) throw new Error("bad FILL op " + JSON.stringify(op));
        out.fill(v, dst, dst + n);
      } else {
        throw new Error("unknown op " + JSON.stringify(op));
      }
    }
    if (p !== payload.length) throw new Error("patch payload not fully used");
    return out;
  }

  // A test build: a release published before it was tested on hardware, device_tested exactly false (anything
  // else that is not true is refused outright). It is built only on an explicit opt-in.
  const isTestBuild = (patch) => patch.device_tested === false;
  const UNTESTED_TAG = "-UNTESTED";

  // The kit's folder name: a test build's always says UNTESTED (the exporter names it so; this makes sure).
  function kitName(patch) {
    const n = patch.kit.name;
    return isTestBuild(patch) && n.indexOf(UNTESTED_TAG) < 0 ? n + UNTESTED_TAG : n;
  }

  // Stock in, verified image out, or an Error that says why. Nothing is produced unless every hash matches,
  // and only for a release marked tested on hardware, or a test build with opts.allowUntested === true.
  async function patchImage(stock1, patch, stock2, opts) {
    if (patch.device_tested !== true) {
      if (!isTestBuild(patch)) throw new Error("v" + patch.version + " is not marked tested on hardware: refused");
      if (!(opts && opts.allowUntested === true))
        throw new Error("v" + patch.version + " is not marked tested on hardware (a test build): refused unless " +
          "you tick the box that you know it is untested");
    }
    const want = patch.stock_sha256["bento1.bin"];
    const got = await sha256(stock1);
    if (got !== want) throw new Error("bento1.bin is not the official stock " + patch.base + " file (sha256 " + got + ")");
    const payload = b64decode(patch.payload_b64);
    if ((await sha256(payload)) !== patch.payload_sha256) throw new Error("the patch file is damaged (payload sha256)");
    const image = applyPatch(stock1, patch, payload, stock2);
    const h = await sha256(image);
    if (h !== patch.output_sha256)
      throw new Error("the result is not the published v" + patch.version + " image (sha256 " + h + "): nothing to download");
    return { image, sha256: h };
  }

  // ------------------------------------------------------------------ the kit

  function readme(patch, imageSha) {
    const v = patch.version, base = patch.base, kit = kitName(patch), test = isTestBuild(patch);
    const warn = !test ? [] : [
      "**********************************************************************************************",
      "  TEST BUILD: NOT YET TESTED ON HARDWARE.",
      "  v" + v + " was published before the author ran its device checklist on a real Bento. It may not",
      "  work, may crash or may misbehave. Use it only if you want to help test it, keep your SD card",
      "  backed up, and keep 03_RESTORE_STOCK (or a tested release's kit) at hand to go back.",
      "  If the Bento keeps resetting before the boot screen: unplug USB, hold power past the bootloader",
      "  screen until it switches off, leave it off a while, then power on and put 03_RESTORE_STOCK (stock) back.",
      "**********************************************************************************************",
      "",
    ];
    return warn.concat([
      kit + ": flashing kit made in your browser",
      patch.label + (test ? " - TEST BUILD, not yet tested on hardware" : ""),
      "",
      "This kit was made on your own device from your own copy of 1010music's official Bento firmware " + base + ".",
      "It CONTAINS 1010music's firmware: it is for your own Bento only. Keep it private: never share, upload,",
      "post or attach it anywhere.",
      "",
      "Not affiliated with 1010music. Installing modified firmware is at your own risk and may affect your",
      "warranty. bento_enhanced is free software (GPL-3.0-or-later) and comes with NO WARRANTY. The mod never",
      "touches the Bento's bootloader, which is what keeps the official way back to stock (below) working.",
      "Please don't ask 1010music support about it.",
      "",
      "01_TEST_FIRMWARE/bento1.bin sha256 " + imageSha,
      test ? "  byte for byte the published v" + v + " test build image. It has NOT been tested on hardware yet."
        : "  byte for byte the published v" + v + " image, the one the author tested on hardware.",
      "",
      "WHAT'S IN IT",
      "  01_TEST_FIRMWARE/      bento1.bin (bento_enhanced v" + v + ") + bento2.bin (stock " + base + ", unchanged): what you install",
      "  03_RESTORE_STOCK/      your stock " + base + " bento1.bin + bento2.bin: the way back",
      "  DEVICE_CHECKLIST.txt   the author's hardware test list for this release (what changed, how to try it)",
      "  SHA256SUMS.txt         the sha256 of every file in the kit",
      "",
      "INSTALL",
      "  1. Back up your SD card (copy the whole card to a folder on your computer).",
      "  2. Copy BOTH files from 01_TEST_FIRMWARE (bento1.bin and bento2.bin) to the ROOT of the SD card",
      "     (not into a folder), replacing any there. Eject the card properly before you take it out.",
      "  3. Put the card in the Bento. Long-press power to start the bootloader, then press REC to install.",
      "     Don't switch the Bento off while it installs.",
      "  4. The boot screen shows BE" + v + ".",
      "",
      "BACK TO STOCK",
      "  Do the same with the two files from 03_RESTORE_STOCK: that is the official firmware, installed the",
      "  official way. You can switch back and forth.",
      "  Projects that use the mod's features are not supported on stock firmware. They usually open, but they",
      "  may not play correctly. Keep a backup of any project you care about from before you used the mod.",
      "",
      "More: https://github.com/romanjeff/bento_enhanced (BUILDING.md builds the same kit from source).",
      "",
    ]).join("\n");
  }

  // The checklist as the repository keeps it, with a preface: it was written for the developer's kit.
  function checklistText(patch) {
    return [
      "This is the author's device checklist for bento_enhanced v" + patch.version + ", as kept in the source",
      "repository (docs/checklist-v" + patch.version + ".txt). " + (isTestBuild(patch)
        ? "It has NOT been run on hardware yet: this is a TEST BUILD, published to be tested."
        : "It passed on hardware before the release was published."),
      "It was written for the developer kit that tools/make_kit.py builds: the folder names, Windows paths,",
      "manifest.json and docs\\... files it mentions are that kit's and the repository's, not this kit's",
      "(https://github.com/romanjeff/bento_enhanced). \"Measured in the emulator\" notes date from before the",
      "hardware test. Its \"What changed\" section is the useful part: what is new and how to try it.",
      "",
      "",
    ].join("\n") + patch.kit.checklist;
  }

  // The kit's files, laid out as tools/make_kit.py lays them out (manifest.json is left out: the build's
  // manifest lists stock bytes at the patch sites, so it is never published; the README is added).
  async function kitFiles(patch, image, imageSha, stock1, stock2) {
    const files = [
      { path: "01_TEST_FIRMWARE/bento1.bin", data: image },
      { path: "01_TEST_FIRMWARE/bento2.bin", data: stock2 },
      { path: "03_RESTORE_STOCK/bento1.bin", data: stock1 },
      { path: "03_RESTORE_STOCK/bento2.bin", data: stock2 },
      { path: "README.txt", data: utf8(readme(patch, imageSha)) },
    ];
    if (patch.kit.checklist) files.push({ path: "DEVICE_CHECKLIST.txt", data: utf8(checklistText(patch)) });
    files.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));   // make_kit's order ("./" + path)
    let sums = "";
    for (const f of files) sums += (await sha256(f.data)) + "  ./" + f.path + "\n";
    files.push({ path: "SHA256SUMS.txt", data: utf8(sums) });
    return files;
  }

  // A zip with no compression (method 0, "stored"): firmware hardly compresses, and it keeps this small.
  function makeZip(entries, date) {
    const d = date || new Date();
    const dosTime = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
    const dosDate = ((Math.max(1980, d.getFullYear()) - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
    const parts = [], central = [];
    let offset = 0;
    for (const e of entries) {
      const name = utf8(e.name), data = e.data, crc = crc32(data);
      const lh = new DataView(new ArrayBuffer(30));
      lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true);  // UTF-8 names
      lh.setUint16(8, 0, true); lh.setUint16(10, dosTime, true); lh.setUint16(12, dosDate, true);
      lh.setUint32(14, crc, true); lh.setUint32(18, data.length, true); lh.setUint32(22, data.length, true);
      lh.setUint16(26, name.length, true); lh.setUint16(28, 0, true);
      parts.push(new Uint8Array(lh.buffer), name, data);
      const ch = new DataView(new ArrayBuffer(46));
      ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true);
      ch.setUint16(8, 0x0800, true); ch.setUint16(10, 0, true); ch.setUint16(12, dosTime, true);
      ch.setUint16(14, dosDate, true); ch.setUint32(16, crc, true); ch.setUint32(20, data.length, true);
      ch.setUint32(24, data.length, true); ch.setUint16(28, name.length, true);
      ch.setUint32(42, offset, true);
      central.push(new Uint8Array(ch.buffer), name);
      offset += 30 + name.length + data.length;
    }
    const cdSize = central.reduce((n, a) => n + a.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true);
    end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
    const all = parts.concat(central, [new Uint8Array(end.buffer)]);
    const out = new Uint8Array(all.reduce((n, a) => n + a.length, 0));
    let p = 0;
    for (const a of all) { out.set(a, p); p += a.length; }
    return out;
  }

  // Stock pair + patch in; verified image, kit files and the kit zip out.
  // opts.allowUntested === true: the user opted in to a test build (refused without it).
  async function buildKit(stock1, stock2, patch, date, opts) {
    const want2 = patch.stock_sha256["bento2.bin"];
    const got2 = await sha256(stock2);
    if (got2 !== want2) throw new Error("bento2.bin is not the official stock " + patch.base + " file (sha256 " + got2 + ")");
    const { image, sha256: imageSha } = await patchImage(stock1, patch, stock2, opts);
    const files = await kitFiles(patch, image, imageSha, stock1, stock2);
    const name = kitName(patch);
    const zip = makeZip(files.map((f) => ({ name: name + "/" + f.path, data: f.data })), date);
    return { image, imageSha, files, zip, zipName: name + "-kit.zip" };
  }

  // ------------------------------------------------------------------ reading the user's files

  // bento1.bin / bento2.bin out of the 1010music download's zip (stored or deflated entries; deflate needs
  // DecompressionStream, in every current browser). Returns {"bento1.bin": bytes, ...} for those found.
  async function filesFromZip(bytes) {
    try {
      return await readZip(bytes);
    } catch (e) {
      if (e instanceof RangeError) throw new Error("the zip is damaged or incomplete: download it again, or extract it and pick the files");
      throw e;
    }
  }

  async function readZip(bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let eocd = -1;
    // The end record is the last 22 bytes plus its comment: a "PK\5\6" inside the comment is not it.
    for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--)
      if (dv.getUint32(i, true) === 0x06054b50 && i + 22 + dv.getUint16(i + 20, true) === bytes.length) { eocd = i; break; }
    if (eocd < 0) throw new Error("not a zip file, or a damaged one");
    const count = dv.getUint16(eocd + 10, true);
    let p = dv.getUint32(eocd + 16, true);
    const found = {}, dec = new TextDecoder();
    for (let k = 0; k < count; k++) {
      if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("damaged zip");
      const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true);
      const nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
      const lho = dv.getUint32(p + 42, true);
      const name = dec.decode(bytes.subarray(p + 46, p + 46 + nlen));
      p += 46 + nlen + xlen + clen;
      const base = name.replace(/\\/g, "/").split("/").pop().toLowerCase();
      if (base !== "bento1.bin" && base !== "bento2.bin") continue;
      if (found[base]) throw new Error("the zip holds more than one " + base + ": extract it and pick the files");
      const start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true);
      if (start + csize > bytes.length) throw new RangeError("entry past the end");
      const raw = bytes.subarray(start, start + csize);
      if (method === 0) found[base] = raw.slice();
      else if (method === 8) {
        let ds = null;
        try { ds = new root.DecompressionStream("deflate-raw"); } catch (e) { ds = null; }   // absent, or no deflate-raw
        if (!ds) throw new Error("this browser can't unzip: extract the zip and pick bento1.bin and bento2.bin");
        const s = new Blob([raw]).stream().pipeThrough(ds);
        found[base] = new Uint8Array(await new Response(s).arrayBuffer());
      } else throw new Error("unsupported zip compression: extract the zip and pick the files");
    }
    return found;
  }

  // What a file is, by its sha256 (names don't matter): {role: "bento1.bin" | "bento2.bin" | null, sha, note}.
  // For a refused file the note says what it is, when the hash is known, and what to do.
  async function identify(bytes, index) {
    const sha = await sha256(bytes);
    for (const role of ["bento1.bin", "bento2.bin"])
      if (index.stock_sha256[role] === sha) return { role, sha, note: "stock " + index.base + " " + role };
    const known = index.known_sha256[sha], base = index.base;
    let note;
    if (known && /already modded/.test(known)) note = known;
    else if (known) note = known + ": this page needs firmware " + base + " exactly. Download " + base + " from 1010music";
    else note = "not an official " + base + " Bento file. This page needs firmware " + base + " exactly, unmodified " +
      "(newer versions are refused until a release supports them)";
    return { role: null, sha, note };
  }

  // The index's releases, split: tested (offered as before, the default among them) and test builds (device_tested
  // exactly false and test_build true; never the default). Anything else is not offered at all.
  function splitReleases(index) {
    const tested = index.releases.filter((r) => r.device_tested === true);
    const testBuilds = index.releases.filter((r) => r.device_tested === false && r.test_build === true);
    const def = tested.some((r) => r.version === index.default) ? index.default : (tested[0] ? tested[0].version : null);
    return { tested, testBuilds, default: def };
  }

  // A fetched patch must be the one its index entry names: version, image hash and device_tested alike.
  const patchMatchesRelease = (rel, p) =>
    p.version === rel.version && p.output_sha256 === rel.output_sha256 && p.device_tested === rel.device_tested;

  Object.assign(BEP, { FORMAT, FORMAT_VERSION, hex, sha256, b64decode, crc32, applyPatch, patchImage, readme,
    checklistText, kitFiles, makeZip, buildKit, filesFromZip, identify, isTestBuild, kitName, splitReleases, patchMatchesRelease });

  if (typeof module === "object" && module.exports) module.exports = BEP;
  else root.BEPatcher = BEP;

  // ------------------------------------------------------------------ the page
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const state = { index: null, patches: {}, stock: {}, result: null, urls: [], busy: false, ready: false };
  const MAX_BIN = 4 * 1024 * 1024, MAX_ZIP = 64 * 1024 * 1024;   // a stock file is ~1.5 MB, the download ~2 MB

  function setStatus(el, text, kind) {
    el.textContent = text;
    el.className = "status" + (kind ? " " + kind : "");
  }

  function fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(2) + " MB" : (n / 1024).toFixed(1) + " KB"; }

  async function loadPatches() {
    const st = $("load-status");
    if (!root.isSecureContext || !root.crypto || !root.crypto.subtle) {
      setStatus(st, "This page must be opened over https (or http://localhost): only there can the browser check " +
        "files (SHA-256). Nothing can be made here.", "bad");
      return;
    }
    try {
      const r = await fetch("patches/index.json", { cache: "no-cache" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      state.index = await r.json();
      if (state.index.format !== FORMAT || state.index.format_version !== FORMAT_VERSION) throw new Error("unknown index format");
    } catch (e) {
      setStatus(st, "Could not load the release list (" + e.message + "). If you opened this file from disk, serve " +
        "the folder instead: python3 -m http.server, in web/, then open http://localhost:8000/.", "bad");
      return;
    }
    const sel = $("release");
    sel.textContent = "";
    const split = splitReleases(state.index);
    const offered = split.tested.concat(split.testBuilds);
    // Tested releases first, as before; test builds in their own group, labelled as such, never the default.
    const group = (label, rels, text) => {
      if (!rels.length) return;
      const g = document.createElement("optgroup");
      g.label = label;
      for (const rel of rels) {
        const o = document.createElement("option");
        o.value = rel.version;
        o.textContent = text(rel);
        g.appendChild(o);
      }
      sel.appendChild(g);
    };
    const latest = split.testBuilds.length ? " (latest tested)" : " (latest)";
    group("Tested on hardware", split.tested, (rel) => "v" + rel.version + (rel === split.tested[0] ? latest : ""));
    group("Test builds: NOT yet tested on hardware", split.testBuilds,
      (rel) => "BE" + rel.version + " - TEST BUILD, not yet tested on hardware");
    if (split.default) sel.value = split.default;
    const tb = $("test-builds");
    tb.hidden = !split.testBuilds.length;
    $("test-builds-list").textContent = split.testBuilds.map((r) => "BE" + r.version).join(", ");
    $("base-version").textContent = state.index.base;
    // Fetch every patch now, so the page keeps working offline once loaded.
    let ok = 0;
    await Promise.all(offered.map(async (rel) => {
      try {
        const r = await fetch("patches/" + rel.file, { cache: "no-cache" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const p = await r.json();
        if (!patchMatchesRelease(rel, p)) throw new Error("patch does not match the list");
        state.patches[rel.version] = p;
        ok++;
      } catch (e) {
        console.warn("patch v" + rel.version + ": " + e.message);
      }
    }));
    setStatus(st, ok && ok === offered.length
      ? "Ready. Everything is loaded: you can go offline now, as long as you don't reload this page."
      : "Loaded " + ok + " of " + offered.length + " releases (see the browser console).",
    ok && ok === offered.length ? "ok" : "bad");
    state.ready = true;
    update();
  }

  async function addFiles(fileList) {
    const list = $("files");
    if (!state.ready) {
      const li = document.createElement("li");
      li.className = "bad";
      li.textContent = state.index ? "Still loading: try again in a second." : $("load-status").textContent;
      list.appendChild(li);
      return;
    }
    for (const f of fileList) {
      const li = document.createElement("li");
      li.textContent = f.name + ": checking...";
      list.appendChild(li);
      try {
        if (f.size > MAX_ZIP || (f.size > MAX_BIN && !/\.zip$/i.test(f.name)))
          throw new Error("too big for a Bento firmware file (" + fmtSize(f.size) + "): pick bento1.bin and bento2.bin, or the 1010music zip");
        let bytes = new Uint8Array(await f.arrayBuffer());
        let parts = [[f.name, bytes]];
        if (bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 3 && bytes[3] === 4) {
          const inside = await filesFromZip(bytes);
          parts = Object.entries(inside).map(([n, b]) => [f.name + " > " + n, b]);
          if (!parts.length) throw new Error("no bento1.bin or bento2.bin in this zip");
        }
        li.textContent = "";
        for (const [label, b] of parts) {
          const id = await identify(b, state.index);
          const row = document.createElement("div");
          if (id.role) {
            state.stock[id.role] = b;
            row.className = "ok";
            row.textContent = label + ": " + id.note + " (OK)";
          } else {
            row.className = "bad";
            row.textContent = label + ": " + id.note + ". Refused.";
          }
          li.appendChild(row);
        }
      } catch (e) {
        li.className = "bad";
        li.textContent = f.name + ": " + e.message;
      }
    }
    update();
  }

  function update() {
    const have1 = !!state.stock["bento1.bin"], have2 = !!state.stock["bento2.bin"];
    $("have-1").className = have1 ? "ok" : "";
    $("have-1").textContent = have1 ? "bento1.bin: stock, OK" : "bento1.bin: not yet";
    $("have-2").className = have2 ? "ok" : "";
    $("have-2").textContent = have2 ? "bento2.bin: stock, OK" : "bento2.bin: not yet";
    const v = $("release").value, p = state.patches[v];
    const test = !!p && isTestBuild(p);
    $("test-warn").hidden = !test;
    if (test) $("test-warn-version").textContent = "BE" + p.version;
    $("test-ok").disabled = state.busy;
    $("make").textContent = test ? "Make the TEST BUILD kit" : "Make the kit";
    $("make").disabled = state.busy || !(have1 && have2 && p) || (test && !$("test-ok").checked);
    $("release").disabled = state.busy;
    $("reset").disabled = state.busy;
  }

  function clearResult() {
    for (const u of state.urls) URL.revokeObjectURL(u);
    state.urls = [];
    $("downloads").hidden = true;
  }

  function link(id, bytes, name) {
    const u = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
    state.urls.push(u);
    const a = $(id);
    a.href = u;
    a.download = name;
  }

  async function make() {
    if (state.busy) return;
    clearResult();
    const st = $("make-status");
    const version = $("release").value, patch = state.patches[version];
    const test = isTestBuild(patch), optIn = test && $("test-ok").checked === true;
    state.busy = true;                                 // the release and the files can't change mid-build
    update();
    setStatus(st, "Patching and checking...", "");
    try {
      const k = await buildKit(state.stock["bento1.bin"], state.stock["bento2.bin"], patch, undefined,
        { allowUntested: optIn });
      if ($("release").value !== version) return;      // (defensive: the select is disabled while building)
      link("dl-zip", k.zip, k.zipName);
      $("dl-zip-name").textContent = k.zipName + " (" + fmtSize(k.zip.length) + ")";
      $("out-sha").textContent = k.imageSha;
      setStatus(st, test
        ? "Done. The result is byte for byte the published " + patch.label + " TEST BUILD image. It is NOT yet " +
          "tested on hardware: see the warning at the top of the kit's README.txt."
        : "Done. The result is byte for byte the published " + patch.label + " image, the one tested on hardware.",
      test ? "" : "ok");
      $("downloads").hidden = false;
    } catch (e) {
      setStatus(st, "Stopped: " + e.message, "bad");
    } finally {
      state.busy = false;
      update();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const drop = $("drop"), input = $("pick");
    input.addEventListener("change", () => { addFiles(Array.from(input.files)); input.value = ""; });
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault(); e.stopPropagation(); drop.classList.remove("over"); addFiles(Array.from(e.dataTransfer.files));
    });
    // A file dropped next to the drop zone would make the browser open (or download) it and leave the page.
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files.length) addFiles(Array.from(e.dataTransfer.files));
    });
    $("release").addEventListener("change", () => {
      $("test-ok").checked = false;                    // the opt-in is per choice: picking again asks again
      clearResult(); setStatus($("make-status"), "", ""); update();
    });
    $("test-ok").addEventListener("change", () => { clearResult(); setStatus($("make-status"), "", ""); update(); });
    $("make").addEventListener("click", make);
    $("reset").addEventListener("click", () => {
      if (state.busy) return;
      state.stock = {};
      $("test-ok").checked = false;
      $("files").textContent = "";
      clearResult();
      setStatus($("make-status"), "", "");
      update();
    });
    loadPatches();
  });
})(typeof globalThis !== "undefined" ? globalThis : this);
