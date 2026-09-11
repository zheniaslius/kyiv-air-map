import { deflateSync } from 'node:zlib';
import { writeFileSync } from 'node:fs';

const width = 1200;
const height = 630;
const pixels = Buffer.alloc(width * height * 4);

function color(hex) {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16), 255];
}

function fill(hex) {
  const c = color(hex);
  for (let i = 0; i < pixels.length; i += 4) pixels.set(c, i);
}

function rect(x, y, w, h, hex) {
  const c = color(hex);
  for (let py = Math.max(0, y); py < Math.min(height, y + h); py++) {
    for (let px = Math.max(0, x); px < Math.min(width, x + w); px++) pixels.set(c, (py * width + px) * 4);
  }
}

function circle(cx, cy, radius, hex) {
  const c = color(hex);
  for (let y = Math.max(0, cy - radius); y <= Math.min(height - 1, cy + radius); y++) {
    for (let x = Math.max(0, cx - radius); x <= Math.min(width - 1, cx + radius); x++) {
      if ((x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2) pixels.set(c, (y * width + x) * 4);
    }
  }
}

function polygon(points, hex) {
  const c = color(hex);
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  for (let y = Math.max(0, Math.min(...ys)); y <= Math.min(height - 1, Math.max(...ys)); y++) {
    for (let x = Math.max(0, Math.min(...xs)); x <= Math.min(width - 1, Math.max(...xs)); x++) {
      let inside = false;
      for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
        const [xi, yi] = points[i];
        const [xj, yj] = points[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
      }
      if (inside) pixels.set(c, (y * width + x) * 4);
    }
  }
}

function crc32(data) {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const tag = Buffer.from(type);
  const size = Buffer.alloc(4);
  size.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([tag, data])));
  return Buffer.concat([size, tag, data, checksum]);
}

fill('#f4f5f7');
rect(0, 0, width, 18, '#1f2933');
rect(70, 65, 1060, 500, '#ffffff');
rect(70, 65, 1060, 76, '#1f2933');
rect(70, 141, 1060, 5, '#e5484d');
circle(111, 103, 14, '#e5484d');
rect(145, 94, 280, 18, '#ffffff');
rect(145, 121, 180, 7, '#d6dbe2');
rect(145, 178, 180, 14, '#1f2933');
rect(145, 204, 290, 8, '#6b7480');
rect(145, 246, 156, 34, '#1f2933');
rect(145, 300, 344, 8, '#6b7480');
rect(145, 319, 280, 8, '#6b7480');

polygon([[518, 204], [634, 172], [738, 228], [706, 326], [603, 356], [510, 292]], '#f0d6d7');
polygon([[738, 228], [845, 202], [943, 264], [912, 359], [807, 389], [706, 326]], '#e5484d');
polygon([[510, 292], [603, 356], [571, 456], [460, 478], [398, 391]], '#e9ecf0');
polygon([[603, 356], [706, 326], [807, 389], [775, 496], [668, 524], [571, 456]], '#f19b9e');
polygon([[807, 389], [912, 359], [998, 426], [960, 514], [856, 540], [775, 496]], '#e9ecf0');
circle(848, 264, 19, '#ffffff');
circle(848, 264, 8, '#e5484d');
rect(0, 602, width, 28, '#e5484d');

const rows = Buffer.alloc((width * 4 + 1) * height);
for (let y = 0; y < height; y++) {
  rows[y * (width * 4 + 1)] = 0;
  pixels.copy(rows, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
}
const header = Buffer.alloc(13);
header.writeUInt32BE(width, 0);
header.writeUInt32BE(height, 4);
header.set([8, 6, 0, 0, 0], 8);
const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
writeFileSync(new URL('../public/social-preview.png', import.meta.url), png);
