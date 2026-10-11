/* 隐星控制中心 - frontend logic */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const MODE_NAMES = {
  auto: "自动 (EC)", silent: "静音", balanced: "均衡",
  performance: "性能", full: "全速", custom: "自定义", manual: "手动",
};
const MODE_HINTS = {
  auto: "风扇完全交给 EC 固件按出厂策略控制（推荐日常使用）。",
  silent: "低转速优先，适合办公、看视频等轻负载场景。",
  balanced: "兼顾散热与噪音，适合大多数场景（默认曲线）。",
  performance: "提前提速、更高上限，适合游戏等高负载场景。",
  full: "双风扇满转（约 100%），适合短时极限散热。",
  custom: "使用下方自定义温度-转速曲线，实时调速。",
  manual: "固定转速运行；不随温度自动变化。",
};
const EFFECTS = [
  ["static", "常亮", "常"], ["breathe", "呼吸", "呼"], ["cycle", "循环", "环"],
  ["dance", "舞动", "舞"], ["flash", "闪烁", "闪"], ["random", "随机", "随"],
  ["tempo", "节拍", "节"], ["wave", "波浪", "浪"],
];
const SWATCHES = [
  ["关闭", "#000000"], ["白色", "#ffffff"], ["红色", "#ff2d2d"],
  ["橙色", "#ff8c1a"], ["黄色", "#ffe14d"], ["绿色", "#35d94a"],
  ["青色", "#29d9d5"], ["蓝色", "#2f7bff"], ["紫色", "#a54dff"],
  ["粉色", "#ff5fb0"], ["暖白", "#ffd9b0"],
];

const state = {
  status: null,
  page: "dashboard",
  tempHist: [],
  colors: [[255, 255, 255], [255, 255, 255], [255, 255, 255]],
  linked: true,
  effectsBuilt: false,
  zonesBuilt: -1,
  curve: [[35, 25], [50, 32], [60, 45], [70, 60], [80, 80], [90, 100]],
  connected: false,
  demo: false,
  fails: 0,
  manualOpen: false,
  lastFanMode: null,
  manualVals: [50, 50],
  manualDirty: false,
  kbdOnLock: 0,
};

/* ---------------- helpers ---------------- */
async function api(path, opts) {
  const init = { method: (opts && opts.method) || "GET", headers: {} };
  if (opts && opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  if (state.demo) {
    if (init.method === "POST") {
      return demoPost(path, opts && opts.body ? opts.body : {});
    }
    if (path === "/api/status") return demoStatus();
    if (path === "/api/diag") return { ok: true, report: demoReport() };
    if (path === "/api/ping") return { ok: true, app: "demo" };
    return { ok: true };
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("无法连接控制服务");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok) {
    throw new Error((data && data.error) || `请求失败 (${res.status})`);
  }
  return data;
}

/* ---------------- demo mode (离线预览) ---------------- */
const demo = {
  phase: 0,
  fan: { mode: "balanced", duty_pct: 45, duty_value: 115, source_temp: 55,
         error: null, active: true, safety_temp: 90, linked: true,
         manual: [50, 50, 50],
         curve: [[35, 25], [50, 32], [60, 45], [70, 60], [80, 80], [90, 100]] },
  kbd: { on: true, brightness_pct: 63, brightness: 160, white_level: 2,
         effect: "static", linked: true,
         colors: [[139, 92, 246], [34, 211, 238], [139, 92, 246]] },
  perf: 2,
};

function demoStatus() {
  demo.phase += 0.06;
  const cpu = Math.round(54 + 9 * Math.sin(demo.phase));
  const gpu = Math.round(46 + 7 * Math.sin(demo.phase * 0.8 + 1.3));
  const tmax = Math.max(cpu, gpu);
  demo.fan.source_temp = tmax;
  demo.fan.duty_pct = demo.fan.mode === "auto" ? null :
    Math.round(curveAt(demo.fan.curve, tmax) * 10) / 10;
  return {
    identity: {
      hostname: "P15-I5X1", kernel: "7.0.0-38-generic",
      product: "P15 I5X1", vendor: "COLORFUL", board: "P15 I5X1",
      bios: "1.07.03COLO", uptime_s: 16543,
    },
    temps: {
      cpu, gpu, gpu_util: Math.round(24 + 10 * Math.sin(demo.phase)),
      gpu_name: "NVIDIA GeForce RTX 4060 Laptop GPU",
      nvme: [41, 44],
      zones: { SEN1: 55, SEN2: 42, SEN3: 50, SEN4: 47, TCPU: 58 },
    },
    fans: {
      rpm: [Math.round(2450 + 120 * Math.sin(demo.phase * 1.2)),
            Math.round(2980 + 180 * Math.sin(demo.phase * 0.9 + 0.5))],
      state: demo.fan,
      presets: {
        silent: [[45, 25], [58, 28], [68, 36], [78, 52], [88, 78], [95, 100]],
        balanced: [[35, 25], [50, 32], [60, 45], [70, 60], [80, 80], [90, 100]],
        performance: [[30, 35], [45, 50], [58, 65], [68, 80], [78, 95], [88, 100]],
      },
    },
    load: { cpu: Math.round(25 + 12 * Math.sin(demo.phase * 1.4)),
            cpu_mhz: 3250,
            mem: { total_gb: 32.0, used_gb: 12.4, pct: 39 } },
    battery: { present: true, capacity: 92, status: "Discharging", power_w: 17.6,
               ac: false, threshold: null },
    keyboard: Object.assign({}, demo.kbd, {
      supported: true, type: 2, type_name: "三区 RGB", is_rgb: true, zones: 3,
      error: null,
      specs: { type: 2, features1: 4194304, white_max_5: false },
    }),
    bridge: { present: true, interface: 1, interface_name: "acpi", error: null },
    perf: { profile: demo.perf, profiles: { powersave: 0, quiet: 1, performance: 2, entertainment: 3 } },
    prefs: { kbd_apply_on_start: true, perf_apply_on_start: false, restore_auto_on_stop: true },
    meta: { app: "colorful-control-center", version: "1.0.0-demo", uptime: 120 },
  };
}

function demoPost(path, body) {
  if (path === "/api/fan") {
    if (body.mode) demo.fan.mode = body.mode;
    if (body.curve) demo.fan.curve = body.curve;
    if (body.manual) demo.fan.manual = body.manual;
  } else if (path === "/api/kbd") {
    if (body.on !== undefined) demo.kbd.on = body.on;
    if (body.brightness_pct !== undefined) {
      demo.kbd.brightness_pct = body.brightness_pct;
      demo.kbd.brightness = Math.round(body.brightness_pct * 2.55);
    }
    if (body.effect !== undefined) demo.kbd.effect = body.effect;
    if (body.colors !== undefined) demo.kbd.colors = body.colors;
    else if (body.color !== undefined) demo.kbd.colors = [body.color, body.color, body.color];
    if (body.linked !== undefined) demo.kbd.linked = body.linked;
    if (body.white_level !== undefined) demo.kbd.white_level = body.white_level;
  } else if (path === "/api/perf") {
    demo.perf = body.profile;
  } else if (path === "/api/raw") {
    const canned = {
      "0x0d": "0x00000000", "13": "0x00000000",
      "0x52": "0x00400000", "82": "0x00400000",
      "0x7a": "0x00000000", "122": "0x00000000",
      "0x63": "0x09950040", "99": "0x09950040",
      "0x64": "0x0ba60040", "100": "0x0ba60040",
      "0x6e": "0xffffffff", "110": "0xffffffff",
    };
    const key = String(body.cmd || "").toLowerCase();
    const hex = canned[key] || "0xfffffffe";
    return { ok: true, status: 0, result: parseInt(hex, 16),
             result_hex: hex,
             buf_hex: body.buf ? "00000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000" : undefined };
  } else if (path === "/api/config") {
    /* ignore */
  }
  return { ok: true };
}

function demoReport() {
  return [
    "== 系统 ==",
    "主机: P15-I5X1  型号: COLORFUL P15 I5X1",
    "主板: P15 I5X1  BIOS: 1.07.03COLO",
    "内核: 7.0.0-38-generic",
    "安全启动: 关闭",
    "",
    "== 内核桥接模块 ==",
    "clevo_cc 模块: 已加载",
    "/dev/clevo_cc: 存在",
    "活动接口: acpi",
    "",
    "== 键盘 ==",
    "类型: 三区 RGB (0x02)",
    "",
    "== 风扇 ==",
    "hwmon 风扇: ['/sys/class/hwmon/hwmon1/fan1_input', '/sys/class/hwmon/hwmon2/fan1_input']",
    "当前转速: [2450, 2980] RPM",
    "",
    "（演示文本：实机上将显示真实诊断数据）",
  ].join("\n");
}

function toast(msg, kind) {
  const wrap = $("#toast-wrap");
  const div = document.createElement("div");
  div.className = "toast" + (kind ? " " + kind : "");
  div.textContent = msg;
  wrap.appendChild(div);
  setTimeout(() => { div.style.opacity = "0"; div.style.transition = "opacity .3s"; }, 3200);
  setTimeout(() => div.remove(), 3600);
}

function rgbCss(c) { return `rgb(${c[0]},${c[1]},${c[2]})`; }
function fmtUptime(s) {
  if (s == null) return "--";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h} 小时 ${m} 分` : `${m} 分钟`;
}
function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/* ---------------- navigation ---------------- */
function switchPage(page) {
  state.page = page;
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("active", p.id === "page-" + page));
  if (page === "diag") refreshDiagReport();
}
$$("#nav .nav-item").forEach(btn => btn.addEventListener("click", () => switchPage(btn.dataset.page)));
$("#btn-open-diag").addEventListener("click", () => switchPage("diag"));

/* ---------------- polling ---------------- */
async function poll() {
  try {
    const st = await api("/api/status");
    state.status = st;
    state.connected = true;
    state.fails = 0;
    if (!state.demo) {
      $("#conn-dot").className = "dot ok";
      $("#conn-text").textContent = "服务运行中";
    }
    updateEverything(st);
  } catch (e) {
    state.connected = false;
    state.fails = (state.fails || 0) + 1;
    $("#conn-dot").className = "dot bad";
    $("#conn-text").textContent = "未连接";
    if (state.fails >= 3 && !state.demo) {
      state.demo = true;
      showBanner("演示模式：未连接到控制服务，界面展示模拟数据；安装到实机并启动服务后即为真实控制。", false);
    } else if (!state.demo) {
      showBanner("无法连接控制服务：" + e.message, true);
    }
  }
  setTimeout(poll, state.demo ? 1200 : 1000);
}

function showBanner(text, isErr) {
  const b = $("#banner");
  if (!text) { b.classList.add("hidden"); return; }
  b.textContent = text;
  b.classList.toggle("err", !!isErr);
  b.classList.remove("hidden");
}

function updateEverything(st) {
  $("#conn-dot").className = "dot ok";
  $("#conn-text").textContent = "服务运行中";

  // bridge banner
  if (state.demo) {
    showBanner("演示模式：未连接到控制服务，界面展示模拟数据；安装到实机并启动服务后即为真实控制。", false);
  } else if (!st.bridge.present) {
    if (st.bridge.secure_boot === "开启") {
      showBanner("安全启动已开启：内核模块需一次性登记 —— 重启后在蓝色 MOK 界面选择 Enroll MOK（密码 colorful）；或进入 BIOS 关闭 Secure Boot。详见安装说明。", true);
    } else {
      showBanner("内核桥接模块未加载，硬件控制不可用 —— 请前往「诊断」页尝试加载模块。", true);
    }
  } else if (st.bridge.error) {
    showBanner("硬件接口异常：" + st.bridge.error, true);
  } else {
    showBanner("");
  }

  updateDashboard(st);
  updateFans(st);
  updateKeyboard(st);
  updateSettings(st);
  updateDiagCards(st);
}

/* ---------------- dashboard ---------------- */
function updateDashboard(st) {
  const t = st.temps || {}, l = st.load || {};
  const cpu = t.cpu, gpu = t.gpu;
  $("#d-cpu").textContent = cpu != null ? cpu : "--";
  $("#d-cpu-sub").textContent = `负载 ${l.cpu ?? "--"}% · ${l.cpu_mhz ?? "--"} MHz`;
  $("#d-cpu-bar").style.width = Math.min(100, (cpu || 0)) + "%";
  $("#d-gpu").textContent = gpu != null ? gpu : "--";
  $("#d-gpu-sub").textContent = `使用率 ${t.gpu_util ?? "--"}%`;
  $("#d-gpu-bar").style.width = Math.min(100, (gpu || 0)) + "%";

  const rpm = (st.fans && st.fans.rpm) || [];
  $("#d-fans").textContent = rpm.length ? rpm.map(r => r + "").join(" / ") : "--";
  const fstate = (st.fans && st.fans.state) || {};
  $("#d-fans-sub").textContent = `RPM · 模式: ${MODE_NAMES[fstate.mode] || fstate.mode || "--"}`;

  const mem = l.mem || {};
  $("#d-mem").textContent = mem.pct != null ? mem.pct + "%" : "--";
  const bat = st.battery || {};
  if (bat.present) {
    $("#d-bat").textContent = `电池 ${bat.capacity}% ${bat.ac ? "(交流电)" : ""} ` +
      (bat.power_w ? `· ${bat.power_w}W` : "");
  } else {
    $("#d-bat").textContent = "无电池信息";
  }

  // identity
  const id = st.identity || {};
  $("#ident-line").textContent = `${id.vendor || ""} ${id.product || ""} · 内核 ${id.kernel || ""}`;
  $("#i-product").textContent = `${id.vendor || ""} ${id.product || ""}`;
  $("#i-bios").textContent = id.bios || "--";
  $("#i-kernel").textContent = id.kernel || "--";
  $("#i-uptime").textContent = fmtUptime(id.uptime_s);

  // zones
  const zones = t.zones || {};
  const zkeys = Object.keys(zones);
  $("#d-zones").innerHTML = zkeys.length
    ? zkeys.map(k => `<span class="chip">${k} <b>${zones[k]}°C</b></span>`).join("")
    : '<span class="muted">未检测到传感器</span>';

  // quick actions state
  $$("#quick-modes button").forEach(b => b.classList.toggle("active", b.dataset.mode === fstate.mode));
  $("#quick-fan-mode").textContent = MODE_NAMES[fstate.mode] || fstate.mode || "--";
  const kb = st.keyboard || {};
  if (!state.kbdOnLock || Date.now() > state.kbdOnLock) {
    $("#quick-kbd").checked = !!kb.on;
  }

  // chart history
  state.tempHist.push({ t: Date.now(), cpu, gpu });
  if (state.tempHist.length > 240) state.tempHist.shift();
  drawChart();
}

function drawChart() {
  const cv = $("#chart");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const hist = state.tempHist;
  if (hist.length < 2) return;
  const temps = [];
  hist.forEach(p => { [p.cpu, p.gpu].forEach(v => { if (v != null) temps.push(v); }); });
  if (!temps.length) return;
  let tmin = Math.min(...temps) - 4, tmax = Math.max(...temps) + 4;
  if (tmax - tmin < 16) { const c = (tmax + tmin) / 2; tmin = c - 8; tmax = c + 8; }
  const x = (i) => 8 + (W - 16) * (i / (hist.length - 1));
  const y = (v) => H - 12 - (H - 24) * ((v - tmin) / (tmax - tmin));

  // grid
  ctx.strokeStyle = "rgba(90,105,140,.15)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yy = 12 + (H - 24) * i / 4;
    ctx.beginPath(); ctx.moveTo(8, yy); ctx.lineTo(W - 8, yy); ctx.stroke();
  }
  const series = [["cpu", "rgba(34,211,238,.95)"], ["gpu", "rgba(139,92,246,.95)"]];
  for (const [key, color] of series) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    hist.forEach((p, i) => {
      const v = p[key];
      if (v == null) { started = false; return; }
      if (!started) { ctx.moveTo(x(i), y(v)); started = true; }
      else ctx.lineTo(x(i), y(v));
    });
    ctx.stroke();
    // last point dot
    for (let i = hist.length - 1; i >= 0; i--) {
      if (hist[i][key] != null) {
        ctx.fillStyle = color;
        ctx.beginPath(); ctx.arc(x(i), y(hist[i][key]), 3, 0, Math.PI * 2); ctx.fill();
        break;
      }
    }
  }
  // labels
  ctx.fillStyle = "#64708c";
  ctx.font = "11px system-ui";
  ctx.fillText(tmax.toFixed(0) + "°", 10, 22);
  ctx.fillText(tmin.toFixed(0) + "°", 10, H - 14);
}

/* ---------------- fans ---------------- */
function updateFans(st) {
  const f = (st.fans && st.fans.state) || {};
  const t = st.temps || {};
  $("#f-cpu").textContent = t.cpu != null ? t.cpu + " °C" : "--";
  $("#f-gpu").textContent = t.gpu != null ? t.gpu + " °C" : "--";
  const rpm = (st.fans && st.fans.rpm) || [];
  $("#f-rpm").textContent = rpm.length ? rpm.map((r, i) => `风扇${i + 1} ${r}`).join("  ") + " RPM" : "--";
  $("#f-duty").textContent = f.duty_pct != null ? f.duty_pct + "%" : (f.mode === "auto" ? "EC 自动" : "--");
  $("#f-error").textContent = f.error ? "硬件错误: " + f.error : "";

  $$("#fan-modes button").forEach(b => b.classList.toggle("active", b.dataset.mode === f.mode));
  $("#fan-mode-hint").textContent = MODE_HINTS[f.mode] || "";
  const curveCard = $("#curve-card");
  curveCard.style.display = (f.mode === "custom") ? "" : "none";
  if (f.mode === "manual" && state.lastFanMode !== "manual") state.manualOpen = true;
  state.lastFanMode = f.mode;
  $("#manual-card").classList.toggle("hidden", !state.manualOpen);

  // 手动滑杆：仅在没有未应用的本地改动时从服务端回读（修复滑动/联动被轮询回弹）
  if (f.manual && !state.manualDirty) {
    const m0 = f.manual[0] != null ? f.manual[0] : 50;
    const m1 = f.manual[1] != null ? f.manual[1] : 50;
    if (m0 !== state.manualVals[0] || m1 !== state.manualVals[1]) {
      state.manualVals = [m0, m1];
    }
  }
  renderManualVals();

  if (f.mode === "custom" && f.curve && !state.curveDirty) {
    state.curve = f.curve.map(([a, b]) => [Number(a), Number(b)]);
  }
  if (f.mode === "custom") drawCurve(f.source_temp);
  $("#curve-live").textContent = (f.source_temp != null && f.mode === "custom")
    ? `当前温度 ${f.source_temp}°C → 输出约 ${Math.round(curveAt(state.curve, f.source_temp))}%`
    : "—";
}

function bindFanControls() {
  $$("#fan-modes button").forEach(b => b.addEventListener("click", async () => {
    try {
      const body = { mode: b.dataset.mode };
      if (b.dataset.mode === "custom") body.curve = state.curve;
      state.manualOpen = false;
      state.manualDirty = false;
      await api("/api/fan", { method: "POST", body });
      toast(`风扇模式已切换：${MODE_NAMES[b.dataset.mode]}`, "ok");
    } catch (e) { toast("切换失败：" + e.message, "err"); }
  }));
  $$("#quick-modes button").forEach(b => b.addEventListener("click", async () => {
    try {
      state.manualOpen = false;
      state.manualDirty = false;
      await api("/api/fan", { method: "POST", body: { mode: b.dataset.mode } });
      toast(`风扇模式已切换：${MODE_NAMES[b.dataset.mode]}`, "ok");
    } catch (e) { toast("切换失败：" + e.message, "err"); }
  }));
  $("#quick-kbd").addEventListener("change", async (ev) => {
    const want = ev.target.checked;
    state.kbdOnLock = Date.now() + 2500;
    try { await api("/api/kbd", { method: "POST", body: { on: want } }); }
    catch (e) { state.kbdOnLock = 0; ev.target.checked = !want; toast("键盘操作失败：" + e.message, "err"); }
  });
  $("#btn-fan-apply-auto").addEventListener("click", async () => {
    try { await api("/api/fan", { method: "POST", body: { mode: "auto" } }); state.manualOpen = false; state.manualDirty = false; toast("已交还 EC 自动控制", "ok"); }
    catch (e) { toast("操作失败：" + e.message, "err"); }
  });
  $("#btn-fan-manual-toggle").addEventListener("click", () => {
    state.manualOpen = $("#manual-card").classList.contains("hidden");
    $("#manual-card").classList.toggle("hidden", !state.manualOpen);
    if (!state.manualOpen) state.manualDirty = false; // 收起面板 = 放弃未应用的改动
  });
  $("#btn-manual-auto").addEventListener("click", async () => {
    try { await api("/api/fan", { method: "POST", body: { mode: "auto" } }); state.manualOpen = false; state.manualDirty = false; toast("已恢复自动", "ok"); }
    catch (e) { toast("操作失败：" + e.message, "err"); }
  });
  $("#btn-manual-apply").addEventListener("click", async () => {
    const vals = state.manualVals.slice();
    try {
      await api("/api/fan", { method: "POST", body: { mode: "manual", manual: vals } });
      state.manualDirty = false;
      state.manualOpen = true;
      toast("已应用固定转速", "ok");
    } catch (e) { toast("应用失败：" + e.message, "err"); }
  });
  $("#manual-linked").addEventListener("change", (ev) => {
    state.linked = ev.target.checked;
    if (ev.target.checked) {
      const v = state.manualVals[0] != null ? state.manualVals[0] : 50;
      state.manualVals = [v, v];
      state.manualDirty = true;
      renderManualVals();
    }
  });
  buildManualSliders();
}

function buildManualSliders() {
  const wrap = $("#manual-sliders");
  wrap.innerHTML = "";
  for (let i = 0; i < 2; i++) {
    const row = document.createElement("div");
    row.className = "mt8";
    row.innerHTML = `
      <div class="row between"><span class="muted">风扇${i + 1} 转速</span>
        <span class="pill" id="man-val-${i}">50%</span></div>
      <input type="range" min="25" max="100" value="50" data-fan="${i}">`;
    wrap.appendChild(row);
  }
  wrap.querySelectorAll("input").forEach(inp => {
    inp.addEventListener("input", () => {
      const i = Number(inp.dataset.fan);
      const v = Number(inp.value);
      state.manualDirty = true;              // 暂存本地改动，等「应用」一次性下发
      if ($("#manual-linked").checked) {
        state.manualVals = [v, v];           // 联动：两个风扇一起走
      } else {
        state.manualVals[i] = v;
      }
      renderManualVals();
    });
  });
  renderManualVals();
}

function renderManualVals() {
  $$("#manual-sliders input").forEach(inp => {
    const i = Number(inp.dataset.fan);
    const v = state.manualVals[i] != null ? state.manualVals[i] : 50;
    if (document.activeElement !== inp) inp.value = v;
    const tag = document.getElementById("man-val-" + i);
    if (tag) tag.textContent = v + "%";
  });
}

/* ---------------- curve editor ---------------- */
const CV = { W: 660, H: 300, L: 48, R: 20, T: 20, B: 36, TMIN: 20, TMAX: 100 };
function cx(t) { return CV.L + (t - CV.TMIN) / (CV.TMAX - CV.TMIN) * (CV.W - CV.L - CV.R); }
function cy(d) { return CV.T + (1 - d / 100) * (CV.H - CV.T - CV.B); }
function invX(px) { return CV.TMIN + (px - CV.L) / (CV.W - CV.L - CV.R) * (CV.TMAX - CV.TMIN); }
function invY(py) { return (1 - (py - CV.T) / (CV.H - CV.T - CV.B)) * 100; }

function curveAt(points, temp) {
  const pts = points.slice().sort((a, b) => a[0] - b[0]);
  if (!pts.length) return 50;
  if (temp <= pts[0][0]) return pts[0][1];
  if (temp >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
  for (let i = 0; i < pts.length - 1; i++) {
    const [t0, d0] = pts[i], [t1, d1] = pts[i + 1];
    if (temp >= t0 && temp <= t1) {
      if (t1 === t0) return d1;
      const k = (temp - t0) / (t1 - t0);
      return d0 + k * (d1 - d0);
    }
  }
  return 50;
}

function buildCurveEditor() {
  const wrap = $("#curve-wrap");
  wrap.innerHTML = `
  <svg viewBox="0 0 ${CV.W} ${CV.H}" id="curve-svg">
    <defs>
      <linearGradient id="curveGrad" x1="0" y1="1" x2="1" y2="0">
        <stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#8b5cf6"/>
      </linearGradient>
      <linearGradient id="curveFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="rgba(139,92,246,.25)"/><stop offset="1" stop-color="rgba(139,92,246,0)"/>
      </linearGradient>
    </defs>
    <rect class="curve-bg" x="0" y="0" width="${CV.W}" height="${CV.H}" rx="10"/>
    <g id="curve-grid"></g>
    <path id="curve-fill" class="curve-fill"></path>
    <polyline id="curve-line" class="curve-line"></polyline>
    <g id="curve-pts"></g>
    <line id="curve-cursor" class="curve-cursor" x1="0" y1="${CV.T}" x2="0" y2="${CV.H - CV.B}" style="display:none"></line>
  </svg>`;
  const grid = $("#curve-grid");
  let g = "";
  for (let t = 20; t <= 100; t += 10) {
    const x = cx(t);
    g += `<line class="gridline ${t % 20 === 0 ? "major" : ""}" x1="${x}" y1="${CV.T}" x2="${x}" y2="${CV.H - CV.B}"/>`;
    g += `<text x="${x - 10}" y="${CV.H - CV.B + 16}">${t}°</text>`;
  }
  for (let d = 0; d <= 100; d += 20) {
    const y = cy(d);
    g += `<line class="gridline ${d % 40 === 0 ? "major" : ""}" x1="${CV.L}" y1="${y}" x2="${CV.W - CV.R}" y2="${y}"/>`;
    g += `<text x="8" y="${y + 4}">${d}%</text>`;
  }
  grid.innerHTML = g;

  const svg = $("#curve-svg");
  svg.addEventListener("dblclick", (ev) => {
    if (ev.target.classList.contains("curve-pt")) return;
    const p = toData(ev);
    if (state.curve.length >= 10) return;
    state.curve.push([p.t, p.d]);
    state.curve.sort((a, b) => a[0] - b[0]);
    state.curveDirty = true;
    drawCurve();
  });
  drawCurve();
}

function toData(ev) {
  const svg = $("#curve-svg");
  const rect = svg.getBoundingClientRect();
  const px = (ev.clientX - rect.left) * (CV.W / rect.width);
  const py = (ev.clientY - rect.top) * (CV.H / rect.height);
  let t = invX(px), d = invY(py);
  t = Math.max(CV.TMIN, Math.min(CV.TMAX, t));
  d = Math.max(25, Math.min(100, d));
  return { t: Math.round(t), d: Math.round(d) };
}

function drawCurve(cursorTemp) {
  const pts = state.curve.slice().sort((a, b) => a[0] - b[0]);
  const line = $("#curve-line");
  const fill = $("#curve-fill");
  const gpts = $("#curve-pts");
  if (!line) return;
  const coords = pts.map(([t, d]) => [cx(t), cy(d)]);
  line.setAttribute("points", coords.map(c => c.join(",")).join(" "));
  if (coords.length) {
    const first = coords[0], last = coords[coords.length - 1];
    fill.setAttribute("d",
      `M ${first[0]} ${CV.H - CV.B} L ` + coords.map(c => c.join(" ")).join(" L ") +
      ` L ${last[0]} ${CV.H - CV.B} Z`);
  }
  gpts.innerHTML = pts.map((p, i) =>
    `<circle class="curve-pt" data-i="${i}" cx="${cx(p[0])}" cy="${cy(p[1])}" r="7"/>`).join("");
  gpts.querySelectorAll(".curve-pt").forEach(c => {
    c.addEventListener("pointerdown", startDrag);
    c.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      const i = Number(c.dataset.i);
      if (state.curve.length > 2) {
        state.curve.splice(i, 1);
        state.curveDirty = true;
        drawCurve();
      }
    });
  });
  const cursor = $("#curve-cursor");
  if (cursorTemp != null && state.page === "fans") {
    cursor.style.display = "";
    cursor.setAttribute("x1", cx(cursorTemp));
    cursor.setAttribute("x2", cx(cursorTemp));
  } else if (cursor) {
    cursor.style.display = "none";
  }
}

let dragState = null;
function startDrag(ev) {
  ev.preventDefault();
  const svg = $("#curve-svg");
  const i = Number(ev.currentTarget.dataset.i);
  dragState = { i };
  ev.currentTarget.setPointerCapture(ev.pointerId);
  const move = (e) => {
    if (!dragState) return;
    const p = toData(e);
    const arr = state.curve.slice().sort((a, b) => a[0] - b[0]);
    const lo = i > 0 ? arr[i - 1][0] + 1 : CV.TMIN;
    const hi = i < arr.length - 1 ? arr[i + 1][0] - 1 : CV.TMAX;
    p.t = Math.max(lo, Math.min(hi, p.t));
    arr[i] = [p.t, p.d];
    state.curve = arr;
    state.curveDirty = true;
    drawCurve(state.status && state.status.fans && state.status.fans.state
      ? state.status.fans.state.source_temp : null);
  };
  const up = () => {
    dragState = null;
    svg.removeEventListener("pointermove", move);
    svg.removeEventListener("pointerup", up);
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerup", up);
}

function bindCurveButtons() {
  $("#btn-curve-apply").addEventListener("click", async () => {
    try {
      await api("/api/fan", { method: "POST", body: { mode: "custom", curve: state.curve } });
      state.curveDirty = false;
      toast("自定义曲线已应用", "ok");
    } catch (e) { toast("应用失败：" + e.message, "err"); }
  });
  $("#btn-curve-reset").addEventListener("click", () => {
    state.curve = [[35, 25], [50, 32], [60, 45], [70, 60], [80, 80], [90, 100]];
    state.curveDirty = true;
    drawCurve();
  });
}

/* ---------------- keyboard ---------------- */
function updateKeyboard(st) {
  const kb = st.keyboard || {};
  $("#kb-type-line").textContent = kb.supported
    ? `键盘类型：${kb.type_name} · ${kb.zones || 1} 个区域`
    : "未检测到可控键盘灯";
  $("#kb-unsupported").style.display = kb.supported ? "none" : "";
  $("#kb-effect-card").style.display = (kb.supported && kb.is_rgb) ? "" : "none";
  $("#kb-color-card").style.display = (kb.supported && kb.is_rgb) ? "" : "none";
  $("#kb-brightness-row").style.display = (kb.supported && kb.is_rgb) ? "" : "none";
  $("#kb-white-row").style.display = (kb.supported && !kb.is_rgb) ? "" : "none";

  if (!state.kbdOnLock || Date.now() > state.kbdOnLock) {
    $("#kb-on").checked = !!kb.on;
  }
  if (kb.is_rgb) {
    const br = $("#kb-brightness");
    if (document.activeElement !== br) {
      br.value = kb.brightness_pct ?? 60;
      $("#kb-bright-val").textContent = (kb.brightness_pct ?? 60) + "%";
    }
  } else {
    const max5 = kb.specs && kb.specs.white_max_5;
    const wl = $("#kb-white");
    wl.max = max5 ? 5 : 2;
    if (document.activeElement !== wl) {
      wl.value = kb.white_level ?? 2;
      $("#kb-white-val").textContent = kb.white_level ?? 2;
    }
  }
  $("#kb-error").textContent = kb.error ? "硬件错误: " + kb.error : "";
  $("#kb-color-hint").textContent = kb.effect !== "static"
    ? "（当前灯效模式下颜色可能被固件接管）" : "";

  if (!state.effectsBuilt && kb.is_rgb) {
    buildEffects(kb.effect);
  } else if (state.effectsBuilt) {
    $$("#kb-effects .effect").forEach(el => el.classList.toggle("active", el.dataset.fx === kb.effect));
  }

  const zones = kb.zones || 0;
  if (state.zonesBuilt !== zones && kb.is_rgb) {
    buildZones(zones);
    state.zonesBuilt = zones;
  }
  if (kb.colors) {
    state.colors = kb.colors.map(c => c.slice());
    $("#kb-linked").checked = !!kb.linked;
    state.linked = !!kb.linked;
    renderZoneColors();
  }
}

function buildEffects(active) {
  const wrap = $("#kb-effects");
  wrap.innerHTML = "";
  EFFECTS.forEach(([key, name, ic]) => {
    const d = document.createElement("div");
    d.className = "effect" + (key === active ? " active" : "");
    d.dataset.fx = key;
    d.innerHTML = `<div class="fx-ic">${ic}</div><div class="fx-name">${name}</div>`;
    d.addEventListener("click", async () => {
      try {
        const r = await api("/api/kbd", { method: "POST", body: { effect: key } });
        $$("#kb-effects .effect").forEach(e => e.classList.toggle("active", e === d));
        const off = r && r.keyboard && r.keyboard.on === false;
        toast(off ? `灯效已切换：${name}（背光当前关闭，打开总开关后生效）` : `灯效已切换：${name}`, "ok");
      } catch (e) { toast("切换失败：" + e.message, "err"); }
    });
    wrap.appendChild(d);
  });
  state.effectsBuilt = true;
}

function buildZones(count) {
  const wrap = $("#kb-zones");
  wrap.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const row = document.createElement("div");
    row.className = "zone-row";
    row.innerHTML = `
      <span class="zone-name">区域 ${i + 1}</span>
      <span class="zone-preview" id="zp-${i}"></span>
      <input type="color" id="zc-${i}" value="#ffffff">
      <div class="swatches" id="zs-${i}"></div>`;
    wrap.appendChild(row);
    const sw = row.querySelector(`#zs-${i}`);
    SWATCHES.forEach(([nm, hex]) => {
      const s = document.createElement("span");
      s.className = "swatch";
      s.style.background = hex;
      s.title = nm;
      s.addEventListener("click", () => setZoneColor(i, hexToRgb(hex)));
      sw.appendChild(s);
    });
    row.querySelector(`#zc-${i}`).addEventListener("input", (ev) => setZoneColor(i, hexToRgb(ev.target.value)));
  }
  renderZoneColors();
}

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return [255, 255, 255];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex(c) {
  return "#" + c.map(x => x.toString(16).padStart(2, "0")).join("");
}

function renderZoneColors() {
  for (let i = 0; i < 3; i++) {
    const prev = $(`#zp-${i}`), inp = $(`#zc-${i}`);
    if (prev) prev.style.background = rgbCss(state.colors[i] || [255, 255, 255]);
    if (inp && document.activeElement !== inp) inp.value = rgbToHex(state.colors[i] || [255, 255, 255]);
  }
}

const pushColors = debounce(async () => {
  try {
    await api("/api/kbd", { method: "POST", body: {
      effect: "static", linked: state.linked,
      colors: state.linked ? undefined : state.colors,
      color: state.linked ? state.colors[0] : undefined,
    }});
  } catch (e) { toast("颜色设置失败：" + e.message, "err"); }
}, 250);

function setZoneColor(i, rgb) {
  if ($("#kb-linked").checked) {
    state.colors = [rgb.slice(), rgb.slice(), rgb.slice()];
  } else {
    state.colors[i] = rgb.slice();
  }
  renderZoneColors();
  pushColors();
}

function bindKeyboardControls() {
  $("#kb-on").addEventListener("change", async (ev) => {
    const want = ev.target.checked;
    state.kbdOnLock = Date.now() + 2500; // 等服务确认期间，防止轮询把开关弹回
    try {
      const r = await api("/api/kbd", { method: "POST", body: { on: want } });
      if (r && r.keyboard) $("#kb-on").checked = !!r.keyboard.on;
    } catch (e) {
      state.kbdOnLock = 0;
      ev.target.checked = !want;
      toast("操作失败：" + e.message, "err");
    }
  });
  const pushBright = debounce(async () => {
    const v = Number($("#kb-brightness").value);
    try { await api("/api/kbd", { method: "POST", body: { brightness_pct: v } }); }
    catch (e) { toast("亮度设置失败：" + e.message, "err"); }
  }, 250);
  $("#kb-brightness").addEventListener("input", () => {
    $("#kb-bright-val").textContent = $("#kb-brightness").value + "%";
    pushBright();
  });
  const pushWhite = debounce(async () => {
    const v = Number($("#kb-white").value);
    try { await api("/api/kbd", { method: "POST", body: { white_level: v } }); }
    catch (e) { toast("设置失败：" + e.message, "err"); }
  }, 250);
  $("#kb-white").addEventListener("input", () => {
    $("#kb-white-val").textContent = $("#kb-white").value;
    pushWhite();
  });
  $("#kb-linked").addEventListener("change", (ev) => {
    state.linked = ev.target.checked;
    if (ev.target.checked) {
      state.colors = [state.colors[0].slice(), state.colors[0].slice(), state.colors[0].slice()];
      renderZoneColors();
    }
    pushColors();
  });
}

/* ---------------- settings ---------------- */
function updateSettings(st) {
  const asEl = $("#s-autostart");
  if (asEl && document.activeElement !== asEl) {
    asEl.checked = !!(st.autostart && st.autostart.enabled);
  }
  const perf = st.perf || {};
  $$("#perf-modes button").forEach(b => b.classList.toggle("active", String(perf.profile) === b.dataset.p));
  $("#perf-status").textContent = perf.profile == null
    ? "当前: 未设置（不修改固件预设）"
    : "当前: " + (["省电", "安静", "性能", "影音"][perf.profile] || perf.profile);

  const bat = st.battery || {};
  $("#s-bat-cap").textContent = bat.present ? bat.capacity + "%" : "--";
  $("#s-bat-ac").textContent = bat.present ? (bat.ac ? "已接电源" : "电池供电") : "--";
  $("#s-bat-power").textContent = bat.power_w ? bat.power_w + " W" : "--";
  $("#s-bat-threshold").textContent = st.battery && st.battery.threshold
    ? `${st.battery.threshold.start}% ~ ${st.battery.threshold.end}%` : "不支持";
}

function bindSettings() {
  $$("#perf-modes button").forEach(b => b.addEventListener("click", async () => {
    try {
      await api("/api/perf", { method: "POST", body: { profile: Number(b.dataset.p) } });
      toast("性能预设已应用", "ok");
    } catch (e) { toast("设置失败：" + e.message, "err"); }
  }));
  $("#btn-fan-release").addEventListener("click", async () => {
    try { await api("/api/fan", { method: "POST", body: { mode: "auto" } }); toast("风扇已交还 EC", "ok"); }
    catch (e) { toast("操作失败：" + e.message, "err"); }
  });
  $("#s-kbd-autostart").addEventListener("change", (ev) =>
    savePref("kbd", "apply_on_start", ev.target.checked));
  $("#s-restore-auto").addEventListener("change", (ev) =>
    savePref("daemon", "restore_auto_on_stop", ev.target.checked));
  $("#s-autostart").addEventListener("change", async (ev) => {
    const want = ev.target.checked;
    try {
      const r = await api("/api/autostart", { method: "POST", body: { enabled: want } });
      toast(r.enabled ? "已开启开机自启动（托盘常驻）" : "已关闭开机自启动", "ok");
    } catch (e) {
      toast("设置失败：" + e.message, "err");
      ev.target.checked = !want;
    }
  });
}

async function savePref(section, key, value) {
  try {
    await api("/api/config", { method: "POST", body: { [section]: { [key]: value } } });
  } catch (e) { toast("保存设置失败：" + e.message, "err"); }
}

/* ---------------- diagnostics ---------------- */
function updateDiagCards(st) {
  const b = st.bridge || {};
  $("#dg-module").textContent = b.present ? "已加载" : "未加载";
  $("#dg-iface").textContent = b.present
    ? `接口: ${b.interface_name || "未知"}` : (b.error || "请尝试加载模块");
  $("#dg-sb").textContent = b.secure_boot || "未知";
  const kb = st.keyboard || {};
  $("#dg-kb").textContent = kb.type_name || "--";
  $("#dg-kb-sub").textContent = kb.supported ? `区域数: ${kb.zones}` : "未识别";
  const f = (st.fans && st.fans.state) || {};
  const rpm = (st.fans && st.fans.rpm) || [];
  $("#dg-fan").textContent = rpm.length ? rpm.join(" / ") + " RPM" : "--";
  $("#dg-fan-sub").textContent = `模式: ${MODE_NAMES[f.mode] || f.mode} · 输出: ${f.duty_pct ?? "--"}%`;

  if (document.activeElement !== $("#s-kbd-autostart")) {
    $("#s-kbd-autostart").checked = !!(st.prefs && st.prefs.kbd_apply_on_start);
  }
  if (document.activeElement !== $("#s-restore-auto")) {
    $("#s-restore-auto").checked = !(st.prefs && st.prefs.restore_auto_on_stop === false);
  }
}

async function refreshDiagReport() {
  try {
    const d = await api("/api/diag");
    $("#diag-out").textContent = d.report || "（空）";
    const m = /安全启动: (开启|关闭|未知)/.exec(d.report || "");
    $("#dg-sb").textContent = m ? m[1] : "未知";
  } catch (e) {
    $("#diag-out").textContent = "获取失败: " + e.message;
  }
}

function bindDiag() {
  $("#btn-diag-refresh").addEventListener("click", refreshDiagReport);
  $("#btn-diag-copy").addEventListener("click", async () => {
    const text = $("#diag-out").textContent;
    try {
      await navigator.clipboard.writeText(text);
      toast("报告已复制到剪贴板", "ok");
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      toast("报告已复制（兼容模式）", "ok");
    }
  });
  $("#btn-diag-download").addEventListener("click", () => {
    const blob = new Blob([$("#diag-out").textContent], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "control-center-report.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  });
  $("#btn-mod-load").addEventListener("click", async () => {
    try {
      const r = await api("/api/module", { method: "POST", body: { action: "load" } });
      toast(r.present ? "模块已加载" : "模块未能加载：" + (r.message || ""), r.present ? "ok" : "err");
      refreshDiagReport();
    } catch (e) { toast("操作失败：" + e.message, "err"); }
  });
  $("#btn-mod-reprobe").addEventListener("click", async () => {
    try {
      const r = await api("/api/module", { method: "POST", body: { action: "reprobe" } });
      toast("接口: " + (r.interface_name || "无"), "ok");
    } catch (e) { toast("操作失败：" + e.message, "err"); }
  });
  $("#btn-kb-redetect").addEventListener("click", async () => {
    try { await api("/api/kbd/redetect", { method: "POST", body: {} }); toast("已重新检测", "ok"); }
    catch (e) { toast("操作失败：" + e.message, "err"); }
  });
  $("#btn-raw-send").addEventListener("click", sendRaw);
  $$(".qc").forEach(b => b.addEventListener("click", () => {
    $("#raw-cmd").value = b.dataset.cmd;
    $("#raw-arg").value = b.dataset.arg;
    $("#raw-buf").checked = b.dataset.buf === "1";
    sendRaw();
  }));
}

async function sendRaw() {
  const cmd = $("#raw-cmd").value.trim();
  const arg = $("#raw-arg").value.trim() || "0";
  const buf = $("#raw-buf").checked;
  try {
    const r = await api("/api/raw", { method: "POST", body: { cmd, arg, buf } });
    let out = `cmd=${cmd} arg=${arg}\nstatus=${r.status}  result=${r.result_hex} (${r.result})`;
    if (r.buf_hex) {
      out += "\n\n缓冲区:\n" + hexdump(r.buf_hex);
    }
    $("#raw-out").textContent = out;
  } catch (e) {
    $("#raw-out").textContent = "错误: " + e.message;
  }
}

function hexdump(hex) {
  const bytes = [];
  for (let i = 0; i + 1 < hex.length; i += 2) bytes.push(parseInt(hex.substr(i, 2), 16));
  let out = "";
  for (let i = 0; i < bytes.length; i += 16) {
    const row = bytes.slice(i, i + 16);
    const addr = i.toString(16).padStart(4, "0");
    const hexs = row.map(b => b.toString(16).padStart(2, "0")).join(" ");
    out += `${addr}: ${hexs}\n`;
  }
  return out;
}

/* ---------------- boot ---------------- */
function boot() {
  bindFanControls();
  bindCurveButtons();
  bindKeyboardControls();
  bindSettings();
  bindDiag();
  buildCurveEditor();
  poll();
}

document.addEventListener("DOMContentLoaded", boot);
if (document.readyState !== "loading") boot();
