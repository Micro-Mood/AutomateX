/**
 * 嵌入式 Python 环境准备脚本
 * 1. 下载 Python 3.10 Windows embeddable package
 * 2. 安装 pip
 * 3. 安装 requirements.txt 中的依赖
 * 
 * 产出: build/python-embed/ 目录（可直接打包进安装包）
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

const ROOT = path.resolve(__dirname, '..');
const PROJECT_ROOT = path.resolve(ROOT, '..', '..');
const EMBED_DIR = path.join(ROOT, 'build', 'python-embed');
const PYTHON_VERSION = '3.10.11';
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`;
const GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py';
const ZIP_FILE = path.join(ROOT, 'build', `python-${PYTHON_VERSION}-embed.zip`);

// ============ 工具函数 ============

function downloadFile(url, dest) {
    return new Promise((resolve, reject) => {
        console.log(`  ↓ 下载: ${url}`);
        const file = fs.createWriteStream(dest);
        const get = url.startsWith('https') ? https.get : http.get;

        get(url, (response) => {
            // 处理重定向
            if (response.statusCode === 301 || response.statusCode === 302) {
                file.close();
                fs.unlinkSync(dest);
                return downloadFile(response.headers.location, dest).then(resolve).catch(reject);
            }
            if (response.statusCode !== 200) {
                file.close();
                fs.unlinkSync(dest);
                return reject(new Error(`下载失败 HTTP ${response.statusCode}`));
            }

            const totalSize = parseInt(response.headers['content-length'] || '0', 10);
            let downloaded = 0;

            response.on('data', (chunk) => {
                downloaded += chunk.length;
                if (totalSize > 0) {
                    const pct = ((downloaded / totalSize) * 100).toFixed(0);
                    process.stdout.write(`\r    进度: ${pct}% (${(downloaded / 1024 / 1024).toFixed(1)} MB)`);
                }
            });

            response.pipe(file);
            file.on('finish', () => {
                file.close();
                console.log('');
                resolve();
            });
        }).on('error', (err) => {
            fs.unlinkSync(dest);
            reject(err);
        });
    });
}

function unzip(zipPath, destDir) {
    console.log(`  解压到: ${destDir}`);
    // 使用 PowerShell 解压
    execSync(`powershell -NoProfile -Command "Expand-Archive -Path '${zipPath}' -DestinationPath '${destDir}' -Force"`, {
        stdio: 'pipe'
    });
}

// ============ 主流程 ============

async function setup() {
    console.log('\n=== 准备嵌入式 Python 环境 ===\n');

    // 如果已经构建过就跳过
    const pythonExe = path.join(EMBED_DIR, 'python.exe');
    if (fs.existsSync(pythonExe)) {
        // 检查依赖是否已安装
        const sitePackages = path.join(EMBED_DIR, 'Lib', 'site-packages');
        if (fs.existsSync(path.join(sitePackages, 'fastapi'))) {
            console.log('  ✓ 嵌入式 Python 环境已就绪（跳过）');
            return;
        }
    }

    // 创建 build 目录
    fs.mkdirSync(path.join(ROOT, 'build'), { recursive: true });

    // 1. 下载嵌入式 Python
    if (!fs.existsSync(ZIP_FILE)) {
        console.log('  [1/4] 下载 Python 嵌入式包...');
        await downloadFile(PYTHON_URL, ZIP_FILE);
        console.log('  ✓ 下载完成');
    } else {
        console.log('  [1/4] Python 嵌入式包已缓存');
    }

    // 2. 解压
    console.log('  [2/4] 解压 Python...');
    if (fs.existsSync(EMBED_DIR)) {
        fs.rmSync(EMBED_DIR, { recursive: true, force: true });
    }
    unzip(ZIP_FILE, EMBED_DIR);
    console.log('  ✓ 解压完成');

    // 3. 启用 import site（修改 ._pth 文件，去掉 #import site 的注释）
    const pthFiles = fs.readdirSync(EMBED_DIR).filter(f => f.endsWith('._pth'));
    for (const pthFile of pthFiles) {
        const pthPath = path.join(EMBED_DIR, pthFile);
        let content = fs.readFileSync(pthPath, 'utf8');
        content = content.replace('#import site', 'import site');
        // 添加 Lib/site-packages 路径
        if (!content.includes('Lib/site-packages')) {
            content += '\nLib/site-packages\n';
        }
        fs.writeFileSync(pthPath, content, 'utf8');
    }
    console.log('  ✓ 已启用 site-packages');

    // 4. 安装 pip
    console.log('  [3/4] 安装 pip...');
    const getPipPath = path.join(ROOT, 'build', 'get-pip.py');
    if (!fs.existsSync(getPipPath)) {
        await downloadFile(GET_PIP_URL, getPipPath);
    }
    execSync(`"${pythonExe}" "${getPipPath}" --no-warn-script-location`, {
        cwd: EMBED_DIR,
        stdio: 'pipe',
        env: { ...process.env, PYTHONUTF8: '1' }
    });
    console.log('  ✓ pip 安装完成');

    // 5. 安装依赖
    console.log('  [4/4] 安装项目依赖...');
    const reqFile = path.join(PROJECT_ROOT, 'requirements.txt');
    execSync(`"${pythonExe}" -m pip install -r "${reqFile}" --no-warn-script-location --disable-pip-version-check`, {
        cwd: EMBED_DIR,
        stdio: 'inherit',
        env: { ...process.env, PYTHONUTF8: '1' }
    });
    console.log('  ✓ 依赖安装完成');

    // 6. 清理不需要的文件减小体积
    console.log('  清理缓存...');
    const cacheDir = path.join(EMBED_DIR, 'Lib', 'site-packages', 'pip');
    if (fs.existsSync(cacheDir)) {
        fs.rmSync(cacheDir, { recursive: true, force: true });
    }
    // 清理 __pycache__
    cleanDir(EMBED_DIR, '__pycache__');
    // 清理 .dist-info 中不需要的大文件
    cleanDistInfo(path.join(EMBED_DIR, 'Lib', 'site-packages'));
    // 删除 pip、setuptools 等不需要的包
    for (const pkg of ['pip', 'setuptools', 'wheel', 'pkg_resources']) {
        const pkgDir = path.join(EMBED_DIR, 'Lib', 'site-packages', pkg);
        if (fs.existsSync(pkgDir)) {
            fs.rmSync(pkgDir, { recursive: true, force: true });
        }
    }
    // 删除 Scripts 目录（不需要 pip.exe 等）
    const scriptsDir = path.join(EMBED_DIR, 'Scripts');
    if (fs.existsSync(scriptsDir)) {
        fs.rmSync(scriptsDir, { recursive: true, force: true });
    }

    console.log('  ✓ 清理完成');

    // 打印最终大小
    const totalSize = getDirSize(EMBED_DIR);
    console.log(`\n  ✅ 嵌入式 Python 环境就绪: ${(totalSize / 1024 / 1024).toFixed(1)} MB\n`);
}

function cleanDir(dir, targetName) {
    if (!fs.existsSync(dir)) return;
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (entry.name === targetName) {
                fs.rmSync(fullPath, { recursive: true, force: true });
            } else {
                cleanDir(fullPath, targetName);
            }
        }
    }
}

function cleanDistInfo(sitePackages) {
    if (!fs.existsSync(sitePackages)) return;
    const entries = fs.readdirSync(sitePackages, { withFileTypes: true });
    for (const entry of entries) {
        if (entry.isDirectory() && entry.name.endsWith('.dist-info')) {
            const distDir = path.join(sitePackages, entry.name);
            const files = fs.readdirSync(distDir);
            for (const f of files) {
                // 只保留 METADATA 和 RECORD
                if (f !== 'METADATA' && f !== 'RECORD' && f !== 'INSTALLER') {
                    const fp = path.join(distDir, f);
                    if (fs.statSync(fp).isFile()) {
                        fs.unlinkSync(fp);
                    }
                }
            }
        }
    }
}

function getDirSize(dir) {
    let size = 0;
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            size += getDirSize(fullPath);
        } else {
            size += fs.statSync(fullPath).size;
        }
    }
    return size;
}

module.exports = { setup };

// 支持直接运行
if (require.main === module) {
    setup().catch(err => {
        console.error('❌ 准备失败:', err);
        process.exit(1);
    });
}
