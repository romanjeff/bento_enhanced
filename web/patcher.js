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
  // F [dst, len, byte]: a constant run. Any op out of bounds rejects the whole patch.
  function applyPatch(stock1, patch, payload) {
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
      } else if (kind === "C") {
        const [, dst, src, n] = op;
        if (!inOut(dst, n) || !Number.isInteger(src) || src < 0 || src + n > stock1.length)
          throw new Error("bad COPY op " + JSON.stringify(op));
        out.set(stock1.subarray(src, src + n), dst);
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

  // Stock in, verified image out, or an Error that says why. Nothing is produced unless every hash matches.
  async function patchImage(stock1, patch) {
    const want = patch.stock_sha256["bento1.bin"];
    const got = await sha256(stock1);
    if (got !== want) throw new Error("bento1.bin is not the official stock " + patch.base + " file (sha256 " + got + ")");
    const payload = b64decode(patch.payload_b64);
    if ((await sha256(payload)) !== patch.payload_sha256) throw new Error("the patch file is damaged (payload sha256)");
    const image = applyPatch(stock1, patch, payload);
    const h = await sha256(image);
    if (h !== patch.output_sha256)
      throw new Error("the result is not the published v" + patch.version + " image (sha256 " + h + "): nothing to download");
    return { image, sha256: h };
  }

  // ------------------------------------------------------------------ the kit

  function readme(patch, imageSha) {
    const v = patch.version, base = patch.base, kit = patch.kit.name;
    const tested = patch.device_tested === true
      ? "byte for byte the image the author tested on hardware."
      : "byte for byte the published image (NOT yet tested on hardware: DEVICE_CHECKLIST.txt is that test).";
    return [
      kit + ": flashing kit made in your browser",
      patch.label,
      "",
      "This kit was made on your own device from your own copy of 1010music's official Bento firmware " + base + ".",
      "It CONTAINS 1010music's firmware: it is for your own Bento only. Keep it private: never share, upload,",
      "post or attach it anywhere.",
      "",
      "Not affiliated with 1010music. Installing modified firmware is at your own risk. The mod never touches",
      "the Bento's bootloader, so going back to stock (below) always works. Please don't ask 1010music support",
      "about it.",
      "",
      "01_TEST_FIRMWARE/bento1.bin sha256 " + imageSha,
      "  " + tested,
      "",
      "WHAT'S IN IT",
      "  01_TEST_FIRMWARE/      bento1.bin (bento_enhanced v" + v + ") + bento2.bin (stock " + base + ", unchanged): what you install",
      "  03_RESTORE_STOCK/      your stock " + base + " bento1.bin + bento2.bin: the way back",
      "  DEVICE_CHECKLIST.txt   what's new in this release and what to try on the device",
      "  SHA256SUMS.txt         the sha256 of every file in the kit",
      "",
      "INSTALL",
      "  1. Back up your SD card (copy the whole card to a folder on your computer).",
      "  2. Copy BOTH files from 01_TEST_FIRMWARE (bento1.bin and bento2.bin) to the ROOT of the SD card",
      "     (not into a folder), replacing any there. Eject the card properly before you take it out.",
      "  3. Put the card in the Bento. Long-press power to start the bootloader, then press REC to install.",
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
    ].join("\n");
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
    if (patch.kit.checklist) files.push({ path: "DEVICE_CHECKLIST.txt", data: utf8(patch.kit.checklist) });
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
  async function buildKit(stock1, stock2, patch, date) {
    const want2 = patch.stock_sha256["bento2.bin"];
    const got2 = await sha256(stock2);
    if (got2 !== want2) throw new Error("bento2.bin is not the official stock " + patch.base + " file (sha256 " + got2 + ")");
    const { image, sha256: imageSha } = await patchImage(stock1, patch);
    const files = await kitFiles(patch, image, imageSha, stock1, stock2);
    const zip = makeZip(files.map((f) => ({ name: patch.kit.name + "/" + f.path, data: f.data })), date);
    return { image, imageSha, files, zip, zipName: patch.kit.name + "-kit.zip" };
  }

  // ------------------------------------------------------------------ reading the user's files

  // bento1.bin / bento2.bin out of the 1010music download's zip (stored or deflated entries; deflate needs
  // DecompressionStream, in every current browser). Returns {"bento1.bin": bytes, ...} for those found.
  async function filesFromZip(bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let eocd = -1;
    for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65557); i--)
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    if (eocd < 0) throw new Error("not a zip file");
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
      const raw = bytes.subarray(start, start + csize);
      if (method === 0) found[base] = raw.slice();
      else if (method === 8) {
        if (typeof root.DecompressionStream !== "function")
          throw new Error("this browser can't unzip: extract the zip and pick bento1.bin and bento2.bin");
        const s = new Blob([raw]).stream().pipeThrough(new root.DecompressionStream("deflate-raw"));
        found[base] = new Uint8Array(await new Response(s).arrayBuffer());
      } else throw new Error("unsupported zip compression: extract the zip and pick the files");
    }
    return found;
  }

  // What a file is, by its sha256 (names don't matter): {role: "bento1.bin" | "bento2.bin" | null, sha, note}.
  async function identify(bytes, index) {
    const sha = await sha256(bytes);
    for (const role of ["bento1.bin", "bento2.bin"])
      if (index.stock_sha256[role] === sha) return { role, sha, note: "stock " + index.base + " " + role };
    const known = index.known_sha256[sha];
    return { role: null, sha, note: known ? known + ", not stock " + index.base : "not a stock " + index.base + " Bento file" };
  }

  Object.assign(BEP, { FORMAT, FORMAT_VERSION, hex, sha256, b64decode, crc32, applyPatch, patchImage, readme,
    kitFiles, makeZip, buildKit, filesFromZip, identify });

  if (typeof module === "object" && module.exports) module.exports = BEP;
  else root.BEPatcher = BEP;

  // ------------------------------------------------------------------ the page
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const state = { index: null, patches: {}, stock: {}, result: null, urls: [] };

  function setStatus(el, text, kind) {
    el.textContent = text;
    el.className = "status" + (kind ? " " + kind : "");
  }

  function fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(2) + " MB" : (n / 1024).toFixed(1) + " KB"; }

  async function loadPatches() {
    const st = $("load-status");
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
    for (const rel of state.index.releases) {
      const o = document.createElement("option");
      o.value = rel.version;
      o.textContent = "v" + rel.version + (rel.version === state.index.default ? " (latest)" : "") +
        (rel.device_tested ? "" : " (not yet tested on hardware)");
      sel.appendChild(o);
    }
    sel.value = state.index.default;
    $("base-version").textContent = state.index.base;
    // Fetch every patch now, so the page keeps working offline once loaded.
    let ok = 0;
    await Promise.all(state.index.releases.map(async (rel) => {
      try {
        const r = await fetch("patches/" + rel.file, { cache: "no-cache" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const p = await r.json();
        if (p.version !== rel.version || p.output_sha256 !== rel.output_sha256) throw new Error("patch does not match the list");
        state.patches[rel.version] = p;
        ok++;
      } catch (e) {
        console.warn("patch v" + rel.version + ": " + e.message);
      }
    }));
    setStatus(st, ok === state.index.releases.length
      ? "Ready. Everything is loaded: this page works offline from here on."
      : "Loaded " + ok + " of " + state.index.releases.length + " releases (see the browser console).",
    ok === state.index.releases.length ? "ok" : "bad");
    update();
  }

  async function addFiles(fileList) {
    if (!state.index) return;
    const list = $("files");
    for (const f of fileList) {
      const li = document.createElement("li");
      li.textContent = f.name + ": checking...";
      list.appendChild(li);
      try {
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
    const v = $("release").value;
    $("make").disabled = !(have1 && have2 && state.patches[v]);
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
    clearResult();
    const st = $("make-status");
    const patch = state.patches[$("release").value];
    setStatus(st, "Patching and checking...", "");
    try {
      const k = await buildKit(state.stock["bento1.bin"], state.stock["bento2.bin"], patch);
      link("dl-zip", k.zip, k.zipName);
      $("dl-zip-name").textContent = k.zipName + " (" + fmtSize(k.zip.length) + ")";
      link("dl-bin", k.image, "bento1.bin");
      $("dl-bin-name").textContent = "bento1.bin (" + fmtSize(k.image.length) + ")";
      $("out-sha").textContent = k.imageSha;
      setStatus(st, "Done. The result is byte for byte the published " + patch.label + " image" +
        (patch.device_tested ? ", the one tested on hardware." : " (not yet tested on hardware)."), "ok");
      $("downloads").hidden = false;
    } catch (e) {
      setStatus(st, "Stopped: " + e.message, "bad");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const drop = $("drop"), input = $("pick");
    input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); addFiles(e.dataTransfer.files); });
    $("release").addEventListener("change", () => { clearResult(); setStatus($("make-status"), "", ""); update(); });
    $("make").addEventListener("click", make);
    $("reset").addEventListener("click", () => {
      state.stock = {};
      $("files").textContent = "";
      clearResult();
      setStatus($("make-status"), "", "");
      update();
    });
    loadPatches();
  });
})(typeof globalThis !== "undefined" ? globalThis : this);
