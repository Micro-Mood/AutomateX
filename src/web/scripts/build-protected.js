/**
 * 源码保护构建脚本
 * 1. 准备嵌入式 Python 环境（含依赖）
 * 2. 用 Electron 的 V8 引擎编译 JS → .jsc 字节码
 * 3. 将 Python .py 文件编译为 .pyc 字节码
 * 4. 调用 electron-builder 打包
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC_ROOT = path.resolve(ROOT, '..');
const { setup: setupPython } = require('./setup-python');

// ============ 0. 准备嵌入式 Python ============

async function preparePython() {
    console.log('\n=== [1/4] 准备嵌入式 Python 环境 ===\n');
    await setupPython();
}

// ============ 1. 用 Electron 的 V8 编译 JS 字节码 ============

function compileJS() {
    console.log('\n=== [2/4] 编译 JS 为 V8 字节码（使用 Electron V8） ===\n');

    try {
        // 关键：用 npx electron 运行编译脚本，确保字节码和打包后的 V8 版本一致
        execSync('npx electron scripts/compile-js.js', {
            cwd: ROOT,
            stdio: 'inherit',
            env: { ...process.env }
        });
        console.log('  ✓ JS 字节码编译完成');
    } catch (err) {
        console.error('  ✗ JS 编译失败:', err.message);
        process.exit(1);
    }
}

// ============ 2. 编译 Python → .pyc ============

function compilePython() {
    console.log('\n=== [3/4] 编译 Python 为 .pyc 字节码 ===\n');

    const pythonDirs = [
        path.join(SRC_ROOT, 'config'),
        path.join(SRC_ROOT, 'mcp'),
        path.join(SRC_ROOT, 'tasks'),
    ];

    // 也编译顶层 .py 文件
    const topLevelPy = [
        path.join(ROOT, 'server.py'),
        path.join(ROOT, 'ws_manager.py'),
    ];

    try {
        // 使用 python -m compileall 编译所有 Python 目录
        for (const dir of pythonDirs) {
            if (fs.existsSync(dir)) {
                console.log(`  编译目录: ${path.relative(SRC_ROOT, dir)}`);
                execSync(`python -m compileall -b -f -q "${dir}"`, {
                    stdio: 'pipe',
                    env: { ...process.env, PYTHONUTF8: '1' }
                });
                console.log(`  ✓ ${path.relative(SRC_ROOT, dir)} 编译完成`);
            }
        }

        // 编译顶层文件
        for (const pyFile of topLevelPy) {
            if (fs.existsSync(pyFile)) {
                console.log(`  编译文件: ${path.basename(pyFile)}`);
                execSync(`python -m compileall -b -f -q "${pyFile}"`, {
                    stdio: 'pipe',
                    env: { ...process.env, PYTHONUTF8: '1' }
                });
                console.log(`  ✓ ${path.basename(pyFile)} 编译完成`);
            }
        }

        console.log('  ✓ Python 字节码编译完成');
    } catch (err) {
        console.error('  ✗ Python 编译失败:', err.message);
        process.exit(1);
    }
}

// ============ 辅助：还原备份 ============

function restoreBackups() {
    console.log('\n=== 还原源文件 ===\n');

    // 还原 JS 备份
    for (const file of ['main.js', 'preload.js']) {
        const srcFile = path.join(ROOT, file);
        const backupFile = srcFile + '.bak';
        if (fs.existsSync(backupFile)) {
            fs.copyFileSync(backupFile, srcFile);
            fs.unlinkSync(backupFile);
            console.log(`  ✓ 还原 ${file}`);
        }
    }

    // 清理临时文件
    const tempFiles = ['main-loader.js', 'preload-loader.js', 'main.jsc', 'preload.jsc'];
    for (const file of tempFiles) {
        const filePath = path.join(ROOT, file);
        if (fs.existsSync(filePath)) {
            fs.unlinkSync(filePath);
            console.log(`  ✓ 清理 ${file}`);
        }
    }

    // 清理源码目录中生成的 .pyc（已被 extraResources 复制走）
    function cleanPyc(dir) {
        if (!fs.existsSync(dir)) return;
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
            const fullPath = path.join(dir, entry.name);
            if (entry.isDirectory() && entry.name !== '__pycache__' && entry.name !== 'node_modules') {
                cleanPyc(fullPath);
            } else if (entry.isFile() && entry.name.endsWith('.pyc')) {
                fs.unlinkSync(fullPath);
            }
        }
    }
    cleanPyc(path.join(SRC_ROOT, 'config'));
    cleanPyc(path.join(SRC_ROOT, 'mcp'));
    cleanPyc(path.join(SRC_ROOT, 'tasks'));

    for (const f of ['server.pyc', 'ws_manager.pyc']) {
        const p = path.join(ROOT, f);
        if (fs.existsSync(p)) { fs.unlinkSync(p); }
    }
    console.log('  ✓ .pyc 文件已清理');
}

// ============ 主流程 ============

async function main() {
    console.log('╔══════════════════════════════════════════╗');
    console.log('║     AutomateX 源码保护构建              ║');
    console.log('╚══════════════════════════════════════════╝');

    // 备份 package.json 并将 main 字段改为 main-loader.js
    const pkgPath = path.join(ROOT, 'package.json');
    const pkgBackup = pkgPath + '.bak';
    fs.copyFileSync(pkgPath, pkgBackup);

    try {
        // 0. 准备嵌入式 Python 环境
        await preparePython();

        // 1. 用 Electron V8 编译 JS 字节码
        compileJS();

        // 2. 编译 Python 字节码
        compilePython();

        // 3. 修改 package.json main 指向字节码 loader，并临时切换输出目录
        const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
        const tempOutput = `release-${Date.now()}`;
        pkg.main = 'main-loader.js';
        pkg.build = pkg.build || {};
        pkg.build.directories = pkg.build.directories || {};
        pkg.build.directories.output = tempOutput;
        fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2), 'utf8');
        console.log(`  ✓ package.json main → main-loader.js, output → ${tempOutput}`);

        // 4. 执行 electron-builder 打包
        console.log('\n=== [4/4] 执行 electron-builder 打包 ===\n');
        execSync('npx electron-builder --win', {
            cwd: ROOT,
            stdio: 'inherit',
            env: { ...process.env }
        });

        console.log('\n✅ 保护构建完成！');
    } catch (err) {
        console.error('\n❌ 构建失败:', err.message);
    } finally {
        // 还原 package.json
        fs.copyFileSync(pkgBackup, pkgPath);
        fs.unlinkSync(pkgBackup);
        console.log('  ✓ 还原 package.json');

        // 还原其他临时文件
        restoreBackups();
    }
}

main().catch(err => {
    console.error('\n❌ 构建异常:', err);
    process.exit(1);
});
