// Builds the logo files the web app uses from the source logo (public/images/carekin.jpeg).
// The off-white background is made transparent by filling in from the edges, so the white
// heart inside the mark (which the background can't reach) stays white.
//
//   node scripts/make-brand-assets.mjs
//
// Writes public/brand/{mark,wordmark,logo}.png and app/{icon,apple-icon}.png.
import sharp from "sharp";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const SOURCE = `${root}/public/images/carekin.jpeg`;

const { data, info } = await sharp(SOURCE).removeAlpha().raw().toBuffer({ resolveWithObject: true });
const { width: W, height: H } = info;

// Background colour: the average of the four corners.
const corner = (x, y) => [0, 1, 2].map((c) => data[(y * W + x) * 3 + c]);
const corners = [corner(2, 2), corner(W - 3, 2), corner(2, H - 3), corner(W - 3, H - 3)];
const bg = [0, 1, 2].map((c) => corners.reduce((s, p) => s + p[c], 0) / 4);
const dist = (i) => Math.hypot(data[i * 3] - bg[0], data[i * 3 + 1] - bg[1], data[i * 3 + 2] - bg[2]);

// Flood fill from every edge pixel through pixels close to the background colour.
const REACH = 42; // how far from the background a pixel may be and still count as background
const outside = new Uint8Array(W * H);
const stack = [];
for (let x = 0; x < W; x++) stack.push(x, (H - 1) * W + x);
for (let y = 0; y < H; y++) stack.push(y * W, y * W + W - 1);
while (stack.length) {
  const i = stack.pop();
  if (outside[i] || dist(i) > REACH) continue;
  outside[i] = 1;
  const x = i % W;
  if (x > 0) stack.push(i - 1);
  if (x < W - 1) stack.push(i + 1);
  if (i >= W) stack.push(i - W);
  if (i < W * (H - 1)) stack.push(i + W);
}

// RGBA: background fully clear, its soft edge (anti-aliasing) partly clear.
const rgba = Buffer.alloc(W * H * 4);
for (let i = 0; i < W * H; i++) {
  rgba[i * 4] = data[i * 3];
  rgba[i * 4 + 1] = data[i * 3 + 1];
  rgba[i * 4 + 2] = data[i * 3 + 2];
  rgba[i * 4 + 3] = outside[i] ? Math.round(Math.min(1, Math.max(0, (dist(i) - 10) / (REACH - 10))) * 255) : 255;
}
const clear = () => sharp(rgba, { raw: { width: W, height: H, channels: 4 } });

mkdirSync(`${root}/public/brand`, { recursive: true });

// Keep only the solid shapes of a cut-out: drop specks left over from neighbouring parts.
// `keep(area, largest)` decides per connected shape (8-connected, by alpha).
function cleanCutout(buf, w, h, keep) {
  const seen = new Int32Array(w * h).fill(-1);
  const areas = [];
  for (let start = 0; start < w * h; start++) {
    if (seen[start] !== -1 || buf[start * 4 + 3] < 24) continue;
    const id = areas.length;
    let area = 0;
    const stack = [start];
    seen[start] = id;
    while (stack.length) {
      const i = stack.pop();
      area++;
      const x = i % w;
      const y = (i - x) / w;
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx;
          const ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          const j = ny * w + nx;
          if (seen[j] === -1 && buf[j * 4 + 3] >= 24) {
            seen[j] = id;
            stack.push(j);
          }
        }
    }
    areas.push(area);
  }
  const largest = Math.max(0, ...areas);
  for (let i = 0; i < w * h; i++) {
    const id = seen[i];
    if (id === -1 ? buf[i * 4 + 3] > 0 : !keep(areas[id], largest)) buf[i * 4 + 3] = 0;
  }
  return buf;
}

// Regions of the 1254 x 1254 source, trimmed to their content afterwards.
const parts = {
  // The heart and the two figures: one shape, so keep only the largest.
  mark: { region: { left: 360, top: 160, width: 575, height: 592 }, keep: (a, max) => a === max, width: 256 },
  // "CareKin": each letter is its own shape, plus the small heart over the i.
  wordmark: { region: { left: 200, top: 752, width: 875, height: 205 }, keep: (a) => a >= 400, width: 520 },
  // The whole logo, with the tagline.
  logo: { region: { left: 170, top: 150, width: 915, height: 900 }, keep: (a) => a >= 60, width: 640 },
};
for (const [name, { region, keep, width }] of Object.entries(parts)) {
  const { data: cut, info: ci } = await clear().extract(region).raw().toBuffer({ resolveWithObject: true });
  cleanCutout(cut, ci.width, ci.height, keep);
  const trimmed = await sharp(cut, { raw: { width: ci.width, height: ci.height, channels: 4 } }).png().toBuffer();
  const out = await sharp(trimmed)
    .trim({ threshold: 1 })
    .resize({ width, withoutEnlargement: true })
    .png({ compressionLevel: 9, palette: true, quality: 90 })
    .toFile(`${root}/public/brand/${name}.png`);
  console.log(`public/brand/${name}.png`, `${out.width}x${out.height}`, `${Math.round(out.size / 1024)}KB`);
}

// Favicons from the mark: transparent for browsers, on white for Apple home screens.
const markBuf = await sharp(`${root}/public/brand/mark.png`).toBuffer();
await sharp(markBuf).resize(256, 256, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toFile(`${root}/app/icon.png`);
await sharp(markBuf)
  .resize(150, 150, { fit: "contain", background: { r: 255, g: 255, b: 255, alpha: 1 } })
  .extend({ top: 15, bottom: 15, left: 15, right: 15, background: { r: 255, g: 255, b: 255, alpha: 1 } })
  .flatten({ background: "#ffffff" })
  .png()
  .toFile(`${root}/app/apple-icon.png`);
console.log("app/icon.png 256x256, app/apple-icon.png 180x180");
