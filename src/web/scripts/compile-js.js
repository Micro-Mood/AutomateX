/**
 * 使用 Electron 的 V8 引擎编译 JS 为字节码
 * 必须通过 `npx electron scripts/compile-js.js` 运行
 * 这样生成的 .jsc 才能在打包后的 Electron 中正常加载
 */

const bytenode = require('bytenode');
const fs = require('fs');
const path = require('path');
const { app } = require('electron');

const ROOT = path.resolve(__dirname, '..');

async function compile() {
    const filesToCompile = ['main.js', 'preload.js'];

    for (const file of filesToCompile) {
        const srcFile = path.join(ROOT, file);
        const jscFile = path.join(ROOT, file.replace('.js', '.jsc'));

        if (!fs.existsSync(srcFile)) {
            console.warn(`  ⚠ 跳过 ${file}（文件不存在）`);
            continue;
        }

        // 备份源文件
        const backupFile = srcFile + '.bak';
        fs.copyFileSync(srcFile, backupFile);

        // 使用 Electron 的 V8 编译字节码
        console.log(`  ✓ 编译 ${file} → ${file.replace('.js', '.jsc')}`);
        await bytenode.compileFile(srcFile, jscFile);
        console.log(`    字节码大小: ${(fs.statSync(jscFile).size / 1024).toFixed(1)} KB`);
    }

    // 创建 loader 文件
    fs.writeFileSync(path.join(ROOT, 'main-loader.js'),
        `'use strict';\nrequire('bytenode');\nrequire('./main.jsc');\n`, 'utf8');
    console.log('  ✓ 创建 main-loader.js');

    fs.writeFileSync(path.join(ROOT, 'preload-loader.js'),
        `'use strict';\nrequire('bytenode');\nrequire('./preload.jsc');\n`, 'utf8');
    console.log('  ✓ 创建 preload-loader.js');

    // 完成后退出 Electron
    app.quit();
}

app.whenReady().then(compile).catch(err => {
    console.error('编译失败:', err);
    app.exit(1);
});
