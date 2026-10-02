'use strict';
/**
 * H3 Studio 桌面壳。
 *
 * 它只做四件事：拉起本地后端、等后端报出真端口、把窗口指过去、退出时让后端自己停干净。
 * 界面由后端自己托管（app/main.py 的 mount_web_client），所以这里不需要打包前端资源，
 * 也不需要一套 IPC —— 壳与后端之间只有一个文件：H3_HOME/desktop.json。
 *
 * 为什么端口不能写死 8788：开发机上 8788 常常已经被 uvicorn 占着，后端会自动换随机口，
 * 写死的壳就会白屏（第一版就是这么设计的，改成握手文件是因为猜端口会静默连到别人的实例上）。
 */
const { app, BrowserWindow, Menu, Tray, shell, dialog, nativeImage } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const APP_NAME = 'H3 Studio';
const APP_ID = 'com.h3studio.desktop';
const IS_WIN = process.platform === 'win32';

/** 首次装机的第一启要 initdb + 建表，给足时间；超时就报清楚，不要转圈装没事 */
const BOOT_TIMEOUT_MS = 180_000;

let win = null;
let tray = null;
let child = null;
let handshake = null;
let adoptedForeignBackend = false;
let quitting = false;
let bootFailure = null;

// ---------------------------------------------------------------- 位置与日志

function defaultDataRoot() {
  // Windows 走 LOCALAPPDATA：媒体产物动辄几十 GB，不能落在随域账户漫游的 Roaming 里
  if (IS_WIN && process.env.LOCALAPPDATA) return path.join(process.env.LOCALAPPDATA, APP_NAME);
  return app.getPath('userData');
}

const dataRoot = process.env.H3_HOME || defaultDataRoot();
const logsDir = path.join(dataRoot, 'logs');
const handshakeFile = path.join(dataRoot, 'desktop.json');
const shellLog = path.join(logsDir, 'desktop-shell.log');

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try {
    fs.mkdirSync(logsDir, { recursive: true });
    fs.appendFileSync(shellLog, line, 'utf8');
  } catch {
    /* 日志写不进去不能拦住启动 */
  }
  console.log(`[desktop] ${msg}`);
}

// 后端读的是 H3_HOME，所以必须在拉起之前就把它定下来
process.env.H3_HOME = dataRoot;
process.env.H3_DESKTOP = '1';

function backendCommand() {
  // 1) 显式指定（排查问题用的后门，也是 CI 的入口）
  if (process.env.H3_BACKEND) {
    return { cmd: process.env.H3_BACKEND, args: [], cwd: dataRoot, label: process.env.H3_BACKEND };
  }
  // 2) 随包的后端（electron-builder 的 extraResources: backend/）
  const bundled = path.join(process.resourcesPath || '', 'backend', IS_WIN ? 'h3-backend.exe' : 'h3-backend');
  if (fs.existsSync(bundled)) {
    return { cmd: bundled, args: [], cwd: dataRoot, label: bundled };
  }
  // 3) 源码模式：没有冻结产物时用指定解释器跑 app.desktop（开发机上验壳用）
  if (process.env.H3_PYTHON && process.env.H3_API_ROOT) {
    return {
      cmd: process.env.H3_PYTHON,
      args: ['-X', 'utf8', '-m', 'app.desktop'],
      cwd: process.env.H3_API_ROOT,
      label: `源码模式 ${process.env.H3_PYTHON}`,
    };
  }
  return null;
}

// ---------------------------------------------------------------- 握手

function readHandshake() {
  try {
    const raw = JSON.parse(fs.readFileSync(handshakeFile, 'utf8'));
    if (!raw.port || !raw.pid) return null;
    return raw;
  } catch {
    return null;
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === 'EPERM';
  }
}

function httpGet(url, { token } = {}, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const req = http.get(url, { headers: token ? { 'x-h3-desktop-token': token } : {}, timeout: timeoutMs }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('timeout', () => {
      req.destroy();
      resolve({ status: 0, body: 'timeout' });
    });
    req.on('error', (err) => resolve({ status: 0, body: String(err.message || err) }));
  });
}

function httpPost(url, token, timeoutMs = 10_000) {
  return new Promise((resolve) => {
    const u = new URL(url);
    const req = http.request(
      { method: 'POST', hostname: u.hostname, port: u.port, path: u.pathname, headers: { 'x-h3-desktop-token': token, 'content-length': 0 }, timeout: timeoutMs },
      (res) => {
        res.resume();
        res.on('end', () => resolve(res.statusCode));
      }
    );
    req.on('error', () => resolve(0));
    req.on('timeout', () => {
      req.destroy();
      resolve(0);
    });
    req.end();
  });
}

async function waitForBackend(deadlineMs) {
  const started = Date.now();
  let sawHandshake = false;
  while (Date.now() - started < deadlineMs) {
    if (child && child.exitCode !== null) {
      return { ok: false, why: `后端进程提前退出（退出码 ${child.exitCode}）` };
    }
    const hs = readHandshake();
    if (hs) {
      if (!sawHandshake) log(`握手文件就绪：端口 ${hs.port}，pid ${hs.pid}`);
      sawHandshake = true;
      handshake = hs;
      const health = await httpGet(`http://127.0.0.1:${hs.port}/healthz`);
      if (health.status === 200) return { ok: true, port: hs.port };
    }
    await sleep(400);
  }
  return { ok: false, why: sawHandshake ? '后端写了握手文件但健康检查一直不通' : `${deadlineMs / 1000} 秒内没有出现握手文件 ${handshakeFile}` };
}

function spawnBackend(spec) {
  const logFile = path.join(logsDir, 'desktop-backend.log');
  fs.mkdirSync(logsDir, { recursive: true });
  const out = fs.openSync(logFile, 'a');
  log(`拉起后端：${spec.label}（cwd=${spec.cwd}，日志=${logFile}）`);
  child = spawn(spec.cmd, spec.args, { cwd: spec.cwd, env: process.env, stdio: ['ignore', out, out], detached: false, windowsHide: true });
  child.on('error', (err) => {
    bootFailure = `启动后端进程失败：${err.message}`;
    log(bootFailure);
  });
  child.on('exit', (code, signal) => log(`后端进程退出 code=${code} signal=${signal}`));
  return child;
}

// ---------------------------------------------------------------- 停机

async function stopBackend() {
  if (!handshake) return;
  const url = `http://127.0.0.1:${handshake.port}/api/desktop/shutdown`;
  log('请求后端优雅退出（它会顺手停掉内嵌 PostgreSQL）');
  await httpPost(url, handshake.token);
  const deadline = Date.now() + 12_000;
  while (Date.now() < deadline) {
    const alive = child ? child.exitCode === null : pidAlive(handshake.pid);
    if (!alive) return;
    await sleep(300);
  }
  log('后端 12 秒没退，强制结束');
  if (child && child.exitCode === null) child.kill();
}

// ---------------------------------------------------------------- 界面

function iconPath() {
  const p = path.join(__dirname, 'build', IS_WIN ? 'icon.ico' : 'icon.png');
  return fs.existsSync(p) ? p : null;
}

function createWindow(port) {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    title: APP_NAME,
    backgroundColor: '#0d1014',
    show: false,
    autoHideMenuBar: true,
    icon: iconPath() || undefined,
    webPreferences: {
      // 界面是本地后端发的可信内容，但它仍是「网页」：不给 node 集成，不开新窗口
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });
  win.once('ready-to-show', () => win.show());
  win.on('close', (e) => {
    // 关掉窗口不等于退出：任务在服务端排队，退出会把后端（含正在跑的任务）一起停掉
    if (!quitting) {
      e.preventDefault();
      win.hide();
      if (tray && IS_WIN) tray.displayBalloon({ title: APP_NAME, content: '仍在后台运行，任务继续排。要彻底退出请从托盘选「退出 H3 Studio」。' });
    }
  });
  win.on('closed', () => {
    win = null;
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${port}`) && !url.startsWith('file://')) {
      e.preventDefault();
      if (/^https?:/i.test(url)) shell.openExternal(url);
    }
  });
  win.loadURL(`http://127.0.0.1:${port}/`);
}

function showFailure(reason) {
  const choice = dialog.showMessageBoxSync({
    type: 'error',
    title: `${APP_NAME} 起不来`,
    message: reason,
    detail: `数据目录：${dataRoot}\n后端日志：${path.join(logsDir, 'desktop-backend.log')}\n壳日志：${shellLog}`,
    buttons: ['打开日志目录', '重试', '退出'],
    defaultId: 1,
    cancelId: 2,
    noLink: true,
  });
  if (choice === 0) shell.openPath(logsDir);
  if (choice === 1) {
    bootFailure = null;
    start().then(() => {
      if (!bootFailure && handshake) createWindow(handshake.port);
      else if (bootFailure) showFailure(bootFailure);
    });
    return;
  }
  app.quit();
}

function createTray(port) {
  const img = iconPath();
  if (!img) return;
  tray = new Tray(nativeImage.createFromPath(img));
  tray.setToolTip(`${APP_NAME} · 本地后端 :${port}`);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '打开工作台', click: () => (win ? win.show() : createWindow(port)) },
      { type: 'separator' },
      { label: '打开数据目录', click: () => shell.openPath(dataRoot) },
      { label: '打开日志目录', click: () => shell.openPath(logsDir) },
      { type: 'separator' },
      {
        label: `退出 ${APP_NAME}（会停后台服务）`,
        click: async () => {
          quitting = true;
          await stopBackend();
          app.quit();
        },
      },
    ])
  );
  tray.on('double-click', () => (win ? win.show() : createWindow(port)));
}

function buildMenu(port) {
  const template = [
    ...(IS_WIN ? [] : [{ label: app.name, submenu: [{ role: 'about' }, { role: 'quit' }] }]),
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { label: '窗口', submenu: [{ role: 'minimize' }, { role: 'close' }] },
    {
      label: '帮助',
      submenu: [
        { label: '打开数据目录', click: () => shell.openPath(dataRoot) },
        { label: '打开日志目录', click: () => shell.openPath(logsDir) },
        {
          label: '关于本机后端',
          click: () =>
            dialog.showMessageBoxSync({
              type: 'info',
              title: '关于本机后端',
              message: `端口 ${port}\n数据目录 ${dataRoot}\n平台 ${process.platform}\n壳版本 ${app.getVersion()}`,
            }),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function start() {
  fs.mkdirSync(dataRoot, { recursive: true });
  fs.mkdirSync(logsDir, { recursive: true });

  // 顺序不能反：先认「有没有活着的后端」，再决定要不要清那份可能陈旧的握手文件。
  // 反了就会「明明有活着的后端，却又起一个」—— 两个实例共用一个数据目录时，
  // 第二个只是接上了别人起的 postmaster，退出时还不停它，等于留下一个孤儿数据库。
  const existing = readHandshake();
  if (existing && pidAlive(existing.pid)) {
    const health = await httpGet(`http://127.0.0.1:${existing.port}/healthz`);
    if (health.status === 200) {
      log(`接到已有后端 :${existing.port}（pid ${existing.pid}），不再新起进程`);
      handshake = existing;
      adoptedForeignBackend = true;
      return;
    }
    log(`握手文件指向 :${existing.port}（pid ${existing.pid}）但健康检查不通，按陈旧处理`);
  }
  fs.rmSync(handshakeFile, { force: true });

  const spec = backendCommand();
  if (!spec) {
    bootFailure = '找不到后端。装机包应带 resources/backend/h3-backend；从源码跑请设 H3_PYTHON 与 H3_API_ROOT。';
    return;
  }
  spawnBackend(spec);
  const res = await waitForBackend(BOOT_TIMEOUT_MS);
  if (!res.ok) {
    bootFailure = res.why;
    return;
  }
  log(`后端在线：http://127.0.0.1:${res.port}`);
}

// ---------------------------------------------------------------- 生命周期

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.show();
      win.focus();
    }
  });

  if (IS_WIN) app.setAppUserModelId(APP_ID);

  app.whenReady().then(async () => {
    log(`${APP_NAME} 启动，数据根 ${dataRoot}`);
    await start();
    if (bootFailure) {
      log(`启动失败：${bootFailure}`);
      showFailure(bootFailure);
      return;
    }
    createWindow(handshake.port);
    createTray(handshake.port);
    buildMenu(handshake.port);
  });

  app.on('before-quit', async (e) => {
    if (quitting || adoptedForeignBackend) return;
    e.preventDefault();
    quitting = true;
    await stopBackend();
    app.exit(0);
  });

  app.on('window-all-closed', () => {
    // 关窗口不退出（托盘还在）；只有托盘/菜单里的「退出」会走 stopBackend
    if (process.platform === 'darwin') return;
  });

  app.on('activate', () => {
    if (win === null && handshake) createWindow(handshake.port);
  });
}
