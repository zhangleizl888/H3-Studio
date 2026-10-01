/**
 * IndexedDB 底座（A 方案）。
 *
 * 项目 / 角色 / 场景 / 镜头 / 渲染日志 / 上传的参考图都留在浏览器本地：
 * 工作台离线可用、没有后端也能写完剧本和分镜，只有「生成」这一步需要 FastAPI。
 * 代价是换浏览器或换机器要导出再导入项目（exportProject / importProject）。
 *
 * 这里只做最小的一层 Promise 包装，不引第三方库：整个前端只有一个地方碰 IDB，
 * 出问题时（配额满、别的标签页占着升级）必须能一眼看到是谁。
 */

const DB_NAME = "h3-studio";
/** v2：加 assets 表（跨项目复用的角色/场景资产库） */
const DB_VERSION = 2;

export const STORE_PROJECTS = "projects";
export const STORE_MEDIA = "media";
export const STORE_BLOBS = "blobs";
export const STORE_META = "meta";
export const STORE_ASSETS = "assets";

let dbPromise: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  if (!dbPromise) dbPromise = doOpen();
  return dbPromise;
}

function doOpen(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("这个浏览器没有 IndexedDB，本地项目存不下"));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      // 版本升级时老 store 已经在了，重复 createObjectStore 会直接抛错
      if (!db.objectStoreNames.contains(STORE_PROJECTS)) {
        const s = db.createObjectStore(STORE_PROJECTS, { keyPath: "id" });
        s.createIndex("updatedAt", "updatedAt");
      }
      if (!db.objectStoreNames.contains(STORE_MEDIA)) {
        const s = db.createObjectStore(STORE_MEDIA, { keyPath: "id" });
        s.createIndex("projectId", "projectId");
      }
      if (!db.objectStoreNames.contains(STORE_BLOBS)) db.createObjectStore(STORE_BLOBS, { keyPath: "id" });
      if (!db.objectStoreNames.contains(STORE_META)) db.createObjectStore(STORE_META, { keyPath: "key" });
      if (!db.objectStoreNames.contains(STORE_ASSETS)) {
        const s = db.createObjectStore(STORE_ASSETS, { keyPath: "id" });
        s.createIndex("type", "type");
        s.createIndex("updatedAt", "updatedAt");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB 打开失败"));
    req.onblocked = () => reject(new Error("另一个标签页占住了数据库升级，请先关掉它再刷新"));
  });
}

function tx<T>(store: string, mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return open().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(store, mode);
        const req = run(t.objectStore(store));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
        t.onabort = () => reject(t.error ?? new Error("写入被拒绝"));
      }),
  );
}

export function putRecord(store: string, value: unknown): Promise<void> {
  return tx(store, "readwrite", (s) => s.put(value) as IDBRequest<IDBValidKey>).then(() => undefined);
}

export function getRecord<T>(store: string, key: IDBValidKey): Promise<T | undefined> {
  return tx<T | undefined>(store, "readonly", (s) => s.get(key) as IDBRequest<T | undefined>);
}

export function allRecords<T>(store: string): Promise<T[]> {
  return tx<T[]>(store, "readonly", (s) => s.getAll() as IDBRequest<T[]>);
}

export function deleteRecord(store: string, key: IDBValidKey): Promise<void> {
  return tx(store, "readwrite", (s) => s.delete(key) as IDBRequest<undefined>).then(() => undefined);
}

/** 按索引取某个项目下的全部媒体 */
export function recordsByIndex<T>(store: string, index: string, key: IDBValidKey): Promise<T[]> {
  return open().then(
    (db) =>
      new Promise<T[]>((resolve, reject) => {
        const req = db.transaction(store, "readonly").objectStore(store).index(index).getAll(key);
        req.onsuccess = () => resolve(req.result as T[]);
        req.onerror = () => reject(req.error);
      }),
  );
}

export async function setMeta<T>(key: string, value: T): Promise<void> {
  await putRecord(STORE_META, { key, value });
}

export async function getMeta<T>(key: string): Promise<T | undefined> {
  const row = await getRecord<{ key: string; value: T }>(STORE_META, key);
  return row?.value;
}
