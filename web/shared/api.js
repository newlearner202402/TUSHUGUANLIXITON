/* 各端共用的接口封装：token 存取、401 静默刷新、错误统一抛出 */

const API_BASE = '/api';
const K_ACCESS = 'lib_access_token';
const K_REFRESH = 'lib_refresh_token';
const K_USER = 'lib_user';

/* 登录状态存在 sessionStorage 而不是 localStorage：
   sessionStorage 的生命周期就是当前这个标签页，页面/浏览器一关就自动清空，
   下次打开必须重新登录（同一台的其它标签页也不会捡到已登录的会话）。 */
const store = window.sessionStorage;

function saveSession(data) {
  store.setItem(K_ACCESS, data.access_token);
  store.setItem(K_REFRESH, data.refresh_token);
  store.setItem(K_USER, JSON.stringify({
    username: data.username, real_name: data.real_name, role: data.role,
  }));
}

function getUser() {
  try { return JSON.parse(store.getItem(K_USER) || 'null'); } catch (e) { return null; }
}

function getToken() { return store.getItem(K_ACCESS); }

function clearSession() {
  stopHeartbeat();
  store.removeItem(K_ACCESS);
  store.removeItem(K_REFRESH);
  store.removeItem(K_USER);
}

/* ---------- 顶号心跳 ----------
   被顶下线的是「旧的」那个页面，它自己不主动问一声，就永远不知道已经被踢了，
   用户不点任何东西时页面看着还是登录状态。所以定期向服务端确认一次。 */
let hbTimer = null;

function stopHeartbeat() {
  if (hbTimer !== null) { clearInterval(hbTimer); hbTimer = null; }
}

async function checkAlive() {
  // 页面不在前台就不发：后台标签页的定时器本来就会被浏览器节流，
  // 而且用户看不到，等他切回来时下面的 visibilitychange 会补一次即时检查。
  if (document.hidden) return;
  try {
    await api('/auth/me');
  } catch (e) { /* 网络错误等交给 api() 处理，它已经决定要不要跳登录页 */ }
}

function startHeartbeat() {
  stopHeartbeat();
  // 5 秒一问：足够让人感觉是「立刻被顶掉」，又不至于给服务端添太多麻烦
  hbTimer = setInterval(checkAlive, 5000);
}

// 切回前台时立刻确认一次，不用干等下一个 5 秒
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && hbTimer !== null) checkAlive();
});

async function refreshSession() {
  const rt = store.getItem(K_REFRESH);
  if (!rt) return false;
  try {
    const res = await fetch(API_BASE + '/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) return false;
    saveSession(await res.json());
    return true;
  } catch (e) {
    return false;
  }
}

/** 统一请求。401 时自动用 refresh token 续期并重试一次。 */
async function api(path, options = {}) {
  const { method = 'GET', body, auth = true, retry = true } = options;
  const headers = { 'Content-Type': 'application/json' };
  if (auth && getToken()) headers.Authorization = 'Bearer ' + getToken();

  let res;
  try {
    res = await fetch(API_BASE + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new Error('无法连接服务端，请确认服务已启动');
  }

  if (res.status === 401 && auth && retry) {
    // 先把服务端给的原因留下来：被顶下线时它会说「已在其它地方登录」，
    // 而普通过期只是换不来新 token，两者的提示语不该混在一起。
    let reason = null;
    try {
      const d = await res.clone().json();
      if (d && typeof d.detail === 'string') reason = d.detail;
    } catch (e) { /* 响应体不是 JSON 就用默认文案 */ }

    if (await refreshSession()) return api(path, { ...options, retry: false });
    kickToLogin(reason || '登录已过期，请重新登录');
    throw new Error(reason || '登录已过期，请重新登录');
  }

  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }

  if (!res.ok) {
    let msg = '请求失败（HTTP ' + res.status + '）';
    if (data && data.detail) {
      msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    }
    throw new Error(msg);
  }
  return data;
}

/* 当前页面属于哪个端。不能只看 URL 上的 client 参数：
   /teacher/ 这类路径本来就不带参数，被顶下线时页面正处于这个路径下，
   只看参数会把人扔到学生端登录页去。所以先认路径，再认已存的角色。 */
function currentClient() {
  const m = location.pathname.match(/^\/(teacher|student)(\/|$)/);
  if (m) return m[1];
  const u = getUser();
  return u && u.role === 'teacher' ? 'teacher' : 'student';
}

/* 被顶下线 / 登录失效时的统一收尾：清掉本地会话，带着原因跳回登录页。
   加锁是为了只跳一次——401 之后 refresh 也会失败，两处都调这个函数，
   后者不能把前者的原因覆盖掉。 */
let kicking = false;
function kickToLogin(reason) {
  if (kicking) return;
  kicking = true;
  const c = currentClient(); // 必须在 clearSession 之前取（它要用到已保存的角色）
  clearSession();
  const suffix = reason ? '&kicked=' + encodeURIComponent(reason) : '';
  location.replace('/login.html?client=' + c + suffix);
}

/** 退出登录并跳回登录页 */
function logout() {
  const c = currentClient();
  clearSession();
  location.href = '/login.html?client=' + c;
}

/** 各端允许的角色：教师端含管理员，学生端只认学生 */
const CLIENT_ROLES = {
  teacher: ['teacher', 'admin'],
  student: ['student'],
};

/** 未登录或角色不匹配就跳转到登录页。返回当前用户信息。 */
async function requireLogin(client) {
  const user = getUser();
  if (!user || !getToken()) {
    location.href = '/login.html?client=' + client;
    return null;
  }
  let me;
  try {
    me = await api('/auth/me');
  } catch (e) {
    // api() 自己已经带原因跳转过了，这里只是兜底（比如网络不通时不会走 401 分支）
    kickToLogin('登录已失效，请重新登录');
    return null;
  }
  const allowed = CLIENT_ROLES[client] || [];
  if (allowed.length && !allowed.includes(me.role)) {
    clearSession();
    location.href = '/login.html?client=' + client;
    return null;
  }
  store.setItem(K_USER, JSON.stringify({
    username: me.username, real_name: me.real_name, role: me.role,
  }));
  startHeartbeat();
  return me;
}

/* ---------------- 通用小工具 ---------------- */

function el(id) { return document.getElementById(id); }

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtDate(s) {
  if (!s) return '—';
  const d = new Date(s);
  if (isNaN(d)) return String(s).slice(0, 10);
  const p = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function fmtDateTime(s) {
  if (!s) return '—';
  const d = new Date(s);
  if (isNaN(d)) return String(s);
  const p = n => String(n).padStart(2, '0');
  return fmtDate(s) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

/** 顶部临时提示条 */
function toast(message, type = 'info', containerId = 'toast') {
  const box = el(containerId);
  if (!box) { alert(message); return; }
  const div = document.createElement('div');
  div.className = 'alert ' + type;
  div.textContent = message;
  box.innerHTML = '';
  box.appendChild(div);
  if (type === 'ok') setTimeout(() => { if (div.parentNode) div.remove(); }, 5000);
}

/** 带页码的列表查询串 */
function qs(params) {
  const p = new URLSearchParams();
  Object.keys(params).forEach(k => {
    const v = params[k];
    if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, v);
  });
  const s = p.toString();
  return s ? '?' + s : '';
}

const ROLE_CN = { student: '学生', teacher: '教师', admin: '管理员' };
const COPY_STATUS_CN = {
  available: '在馆可借', borrowed: '已借出', lost: '遗失', repair: '修补中', discarded: '已注销',
};
