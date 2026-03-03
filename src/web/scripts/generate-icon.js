const path = require('path');
const fs = require('fs');
const sharp = require('sharp');
const pngToIco = require('png-to-ico');
const toIco = pngToIco.default || pngToIco;

const svgPath = path.join(__dirname, '..', 'assets', 'logo.svg');
const icoPath = path.join(__dirname, '..', 'assets', 'logo.ico');

const sizes = [16, 24, 32, 48, 64, 128, 256];

async function main() {
  if (!fs.existsSync(svgPath)) {
    throw new Error(`SVG 不存在: ${svgPath}`);
  }

  const pngBuffers = [];
  for (const size of sizes) {
    const buffer = await sharp(svgPath)
      .resize(size, size, { fit: 'contain' })
      .png()
      .toBuffer();
    pngBuffers.push(buffer);
  }

  const icoBuffer = await toIco(pngBuffers);
  fs.writeFileSync(icoPath, icoBuffer);
  console.log(`已生成: ${icoPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});