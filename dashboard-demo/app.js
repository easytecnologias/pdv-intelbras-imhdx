const DATA_URL = "data/alerts.json";
const DEFAULT_IMAGE = "assets/frame-register.svg";
const DEFAULT_VIDEO = "assets/frame-register.svg";

const demoAlerts = [
  {
    id: 1,
    severity: "critical",
    time: "14:32:08",
    pdv: "PDV 01",
    receipt: "221548",
    event: "Registro sem passagem",
    subtitle: "Movimento abaixo do limite",
    product: "Cafe Marata 250g",
    code: "7898286200060",
    confidence: 94,
    state: "review",
    stateText: "Revisar",
    qty: "1 unidade",
    value: "R$ 14,99",
    result: "Nao confere",
    analysis: "O PDV registrou um produto, mas os quadros nao mostram passagem fisica compativel na area do scanner.",
    note: "Movimento abaixo do limite local e ausencia visual confirmada pela analise de imagem.",
    image: DEFAULT_IMAGE,
    video: DEFAULT_VIDEO
  },
  {
    id: 2,
    severity: "warning",
    time: "14:18:41",
    pdv: "PDV 03",
    receipt: "221544",
    event: "Consulta divergente",
    subtitle: "Produto consultado difere da venda",
    product: "Carne bovina kg",
    code: "0000000000038",
    confidence: 82,
    state: "pending",
    stateText: "Em revisao",
    qty: "0,972 kg",
    value: "R$ 41,79",
    result: "Revisar",
    analysis: "Foi consultado arroz e, em seguida, registrado um produto de outra categoria. O contexto visual precisa de confirmacao humana.",
    note: "Consulta anterior vinculada ao evento de venda dentro da janela de 90 segundos.",
    image: DEFAULT_IMAGE,
    video: DEFAULT_VIDEO
  },
  {
    id: 3,
    severity: "warning",
    time: "13:42:50",
    pdv: "PDV 02",
    receipt: "221526",
    event: "Quantidade agrupada",
    subtitle: "12 unidades em uma leitura",
    product: "Cerv. Antarctica 350ml",
    code: "7891991016216",
    confidence: 76,
    state: "pending",
    stateText: "Em revisao",
    qty: "12 unidades",
    value: "R$ 58,68",
    result: "Revisar",
    analysis: "A embalagem e compativel com cerveja, mas a quantidade agrupada precisa ser confirmada no video.",
    note: "O valor total da linha foi considerado, mesmo com valor unitario abaixo do limite.",
    image: DEFAULT_IMAGE,
    video: DEFAULT_VIDEO
  }
];

const defaultHealth = Array.from({ length: 12 }, (_, index) => ({
  pdv: String(index + 1).padStart(2, "0"),
  bridge: "online",
  imhdx: "online",
  audit: index === 0 ? "warning" : "online"
}));

let alerts = [...demoAlerts];
let health = [...defaultHealth];
let metrics = {};
let activeFilter = "all";
let selectedAlert = alerts[0];
let videoTimer = null;
let videoSecond = 0;
let lastDataSignature = "";

const table = document.getElementById("alertsTable");
const drawer = document.getElementById("alertDrawer");
const backdrop = document.getElementById("drawerBackdrop");
const toast = document.getElementById("toast");

function normalizeAlert(raw, index) {
  const result = normalizeResult(raw.resultado || raw.result || raw.resultado_ia || raw.status);
  const state = raw.state || raw.estado || stateFromResult(result);
  const severity = raw.severity || raw.severidade || severityFromState(state, result);
  const pdvNumber = raw.pdv || raw.terminal || raw.caixa || "001";
  const quantity = raw.qty || raw.quantidade_formatada || raw.quantidade || raw.quantity || "1 unidade";
  const total = raw.value || raw.valor_total || raw.total || raw.valor || raw.valor_unitario || "";

  return {
    id: raw.id || raw.alert_id || `${raw.cupom || raw.receipt || "alert"}-${raw.horario || raw.time || index}`,
    severity,
    time: raw.time || raw.horario || raw.hora || "--:--:--",
    pdv: formatPdv(pdvNumber),
    receipt: String(raw.receipt || raw.cupom || raw.coupon || "-"),
    event: raw.event || raw.tipo || eventFromResult(result, raw),
    subtitle: raw.subtitle || raw.subtitulo || subtitleFromResult(result, raw),
    product: raw.product || raw.produto || raw.produto_pdv || "Produto nao informado",
    code: raw.code || raw.codigo || raw.ean || "",
    confidence: clamp(Number(raw.confidence ?? raw.confianca ?? 0), 0, 100),
    state,
    stateText: raw.stateText || raw.estado_texto || stateText(state),
    qty: formatQuantity(quantity),
    value: formatMoneyLike(total),
    unitValue: formatMoneyLike(raw.valor_unitario || raw.unit_value || ""),
    result,
    analysis: raw.analysis || raw.comparacao_pdv || raw.comparacao || "Analise visual aguardando revisao.",
    note: raw.note || raw.possivel_divergencia || raw.divergencia || raw.observacao || "Sem nota tecnica adicional.",
    source: raw.source || raw.fonte || "Gravacao PDV / iMHDX",
    image: raw.image || raw.imagem || raw.snapshot || DEFAULT_IMAGE,
    video: raw.video || raw.video_url || DEFAULT_VIDEO
  };
}

function normalizeResult(value) {
  const text = String(value || "").toUpperCase();
  if (text.includes("CONFERE_POR_REGRA")) return "Confere por regra";
  if (text.includes("NAO_CONFERE") || text.includes("NAO CONFERE") || text.includes("NÃO CONFERE")) return "Nao confere";
  if (text.includes("CONFERE")) return "Confere";
  if (text.includes("INCONCLUSIVO")) return "Inconclusivo";
  if (text.includes("CANCEL")) return "Cancelado";
  return "Revisar";
}

function stateFromResult(result) {
  if (result === "Confere" || result === "Confere por regra" || result === "Cancelado") return "resolved";
  if (result === "Nao confere") return "review";
  return "pending";
}

function severityFromState(state, result) {
  if (state === "resolved" || result === "Confere" || result === "Cancelado") return "ok";
  if (result === "Nao confere") return "critical";
  return "warning";
}

function stateText(state) {
  if (state === "resolved") return "Resolvido";
  if (state === "review") return "Revisar";
  return "Em revisao";
}

function eventFromResult(result, raw) {
  const product = String(raw.product || raw.produto || "").toUpperCase();
  const quantity = Number(String(raw.quantidade || raw.qty || "1").replace(",", "."));
  if (result === "Cancelado") return "Cancelamento detectado";
  if (quantity > 1) return "Quantidade agrupada";
  if (product.includes("CARNE") || product.includes("BOV") || product.includes("KG")) return "Item de pesagem";
  if (result === "Nao confere") return "Produto incompatível";
  if (result === "Inconclusivo") return "Imagem inconclusiva";
  return "Conferencia visual";
}

function subtitleFromResult(result, raw) {
  if (result === "Cancelado") return "Linha cancelada no cupom";
  if (result === "Confere por regra") return "Liberado pela regra local";
  if (raw.consulta) return "Consulta vinculada ao evento";
  if (result === "Nao confere") return "Revisao humana recomendada";
  if (result === "Inconclusivo") return "Imagem ou contexto insuficiente";
  return "Produto e registro compativeis";
}

function formatPdv(value) {
  const text = String(value).replace(/PDV/i, "").trim();
  const number = text.padStart(2, "0");
  return `PDV ${number}`;
}

function formatQuantity(value) {
  if (typeof value === "string" && /unidade|kg|x/i.test(value)) return value;
  const text = String(value);
  if (text.includes(",")) return `${text} kg`;
  if (text.includes(".") && Number(text) < 5) return `${text.replace(".", ",")} kg`;
  return `${text} unidade${Number(text) === 1 ? "" : "s"}`;
}

function formatMoneyLike(value) {
  if (value === undefined || value === null || value === "") return "-";
  if (typeof value === "string" && value.includes("R$")) return value;
  const number = Number(String(value).replace("R$", "").replace(/\./g, "").replace(",", "."));
  if (Number.isNaN(number)) return String(value);
  return number.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function clamp(value, min, max) {
  if (Number.isNaN(value)) return min;
  return Math.max(min, Math.min(max, value));
}

async function loadDashboardData() {
  try {
    const response = await fetch(`${DATA_URL}?ts=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return;

    const payload = await response.json();
    const rawAlerts = Array.isArray(payload) ? payload : payload.alerts;
    if (!Array.isArray(rawAlerts)) return;

    const signature = JSON.stringify(payload);
    if (signature === lastDataSignature) return;
    lastDataSignature = signature;

    alerts = rawAlerts.map(normalizeAlert);
    health = Array.isArray(payload.health) ? payload.health : health;
    metrics = payload.metrics || {};

    if (!alerts.find(alert => alert.id === selectedAlert?.id)) {
      selectedAlert = alerts[0] || demoAlerts[0];
    }

    renderAll();
  } catch (error) {
    // Fallback silencioso: se o JSON ainda nao existir, a demonstracao continua.
  }
}

function filteredAlerts() {
  const query = document.getElementById("searchInput").value.toLowerCase();
  return alerts.filter(alert => {
    const filterMatch = activeFilter === "all"
      || (activeFilter === "critical" && alert.severity === "critical")
      || (activeFilter === "review" && alert.state !== "resolved")
      || (activeFilter === "resolved" && alert.state === "resolved");
    const text = `${alert.pdv} ${alert.receipt} ${alert.product} ${alert.event} ${alert.code}`.toLowerCase();
    return filterMatch && text.includes(query);
  });
}

function renderAlerts() {
  const rows = filteredAlerts();

  table.innerHTML = rows.map(alert => `
    <tr data-id="${alert.id}">
      <td><span class="severity ${alert.severity}"><i></i>${severityLabel(alert.severity)}</span></td>
      <td>${alert.time}</td>
      <td class="receipt-cell"><strong>${alert.pdv}</strong><span>Cupom ${alert.receipt}</span></td>
      <td><div class="event-cell"><img class="mini-cctv" src="${alert.image || DEFAULT_IMAGE}" alt=""><div><strong>${alert.event}</strong><span>${alert.subtitle}</span></div></div></td>
      <td class="product-cell"><strong>${alert.product}</strong><span>${alert.qty} · ${alert.value}</span></td>
      <td><div class="confidence"><span>${alert.confidence}%</span><i class="confidence-meter"><i style="width:${alert.confidence}%"></i></i></div></td>
      <td><span class="state-badge ${alert.state}">${alert.stateText}</span></td>
      <td><div class="row-actions"><button data-action="open" title="Revisar alerta"><i data-lucide="scan-search"></i></button><button data-action="video" title="Ver video"><i data-lucide="play"></i></button></div></td>
    </tr>
  `).join("");

  table.querySelectorAll("tr").forEach(row => {
    row.addEventListener("click", event => {
      const alert = alerts.find(item => String(item.id) === String(row.dataset.id));
      if (event.target.closest("[data-action='video']")) {
        selectedAlert = alert;
        openVideo();
      } else {
        openDrawer(alert);
      }
    });
  });
  refreshIcons();
}

function severityLabel(severity) {
  if (severity === "critical") return "Critico";
  if (severity === "warning") return "Atencao";
  return "Normal";
}

function renderHealth() {
  document.getElementById("healthGrid").innerHTML = health.map(item => `
    <div class="health-row">
      <strong>PDV ${String(item.pdv).replace(/PDV/i, "").trim().padStart(2, "0")}</strong>
      ${serviceState(item.bridge)}
      ${serviceState(item.imhdx)}
      ${serviceState(item.audit)}
    </div>
  `).join("");
}

function serviceState(state) {
  const label = state === "online" ? "Online" : state === "warning" ? "Atencao" : "Parada";
  return `<span class="service-state ${state}"><i></i>${label}</span>`;
}

function refreshCounters() {
  const pending = alerts.filter(alert => alert.state !== "resolved").length;
  const critical = alerts.filter(alert => alert.severity === "critical").length;
  const resolved = alerts.filter(alert => alert.state === "resolved").length;
  const online = health.filter(item => item.bridge !== "offline" && item.imhdx !== "offline").length;

  const navBadge = document.querySelector('.nav-item[data-view="alerts"] b');
  if (navBadge) navBadge.textContent = pending;

  const notificationBadge = document.querySelector(".notification-button span");
  if (notificationBadge) notificationBadge.textContent = pending;

  const metricCards = document.querySelectorAll(".metrics article strong");
  if (metricCards[0]) metricCards[0].innerHTML = `${metrics.pdvs_monitorados || health.length} <small>/ ${online} online</small>`;
  if (metricCards[1]) metricCards[1].textContent = metrics.vendido_hoje || "R$ 0,00";
  if (metricCards[2]) metricCards[2].textContent = metrics.cupons_fechados || "-";
  if (metricCards[3]) metricCards[3].innerHTML = `${pending} <small>${critical} criticos</small>`;
  if (metricCards[4]) metricCards[4].textContent = metrics.analise_media || "8,4s";

  const tabs = document.querySelectorAll(".alert-tabs button span");
  if (tabs[0]) tabs[0].textContent = alerts.length;
  if (tabs[1]) tabs[1].textContent = critical;
  if (tabs[2]) tabs[2].textContent = pending;
  if (tabs[3]) tabs[3].textContent = resolved;

  const footer = document.querySelector(".table-footer span");
  if (footer) footer.textContent = `Mostrando ${filteredAlerts().length} alertas carregados`;
}

function openDrawer(alert) {
  selectedAlert = alert;
  document.getElementById("drawerTitle").textContent = alert.event;
  document.getElementById("cameraTime").textContent = `${metrics.data || "10/06/2026"} ${alert.time}`;
  document.getElementById("detailPdv").textContent = alert.pdv;
  document.getElementById("detailReceipt").textContent = alert.receipt;
  document.getElementById("detailTime").textContent = alert.time;
  document.getElementById("detailProduct").textContent = alert.product;
  document.getElementById("detailQuantity").textContent = alert.qty;
  document.getElementById("detailValue").textContent = alert.value;
  document.getElementById("confidenceValue").textContent = `${alert.confidence}% confianca`;
  document.getElementById("analysisText").textContent = alert.analysis;
  document.getElementById("technicalNote").textContent = alert.note;
  document.getElementById("mainEvidence").src = alert.image || DEFAULT_IMAGE;
  document.querySelectorAll(".frame-strip button").forEach(button => {
    if (button.dataset.frame === DEFAULT_IMAGE) button.dataset.frame = alert.image || DEFAULT_IMAGE;
    button.querySelector("img").src = button.dataset.frame;
  });

  const badge = document.getElementById("resultBadge");
  badge.textContent = alert.result;
  badge.className = `result-badge ${resultClass(alert.result)}`;
  drawer.classList.add("open");
  backdrop.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function resultClass(result) {
  if (result === "Confere" || result === "Confere por regra" || result === "Cancelado") return "success";
  if (result === "Inconclusivo" || result === "Revisar") return "warning";
  return "danger";
}

function closeDrawer() {
  drawer.classList.remove("open");
  backdrop.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
}

function showToast(message) {
  toast.querySelector("span").textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2500);
}

function openVideo() {
  document.getElementById("videoModal").classList.add("open");
  document.querySelector(".video-meta span").textContent = `${selectedAlert.pdv} · Cupom ${selectedAlert.receipt}`;
  const videoImage = document.querySelector(".video-simulation img");
  videoImage.src = selectedAlert.video || selectedAlert.image || DEFAULT_VIDEO;
  resetVideo();
}

function resetVideo() {
  clearInterval(videoTimer);
  videoTimer = null;
  videoSecond = 0;
  document.getElementById("videoProgress").style.width = "0%";
  document.getElementById("videoClock").textContent = "00:00 / 00:20";
  document.getElementById("playToggle").innerHTML = '<i data-lucide="play"></i>';
  refreshIcons();
}

function toggleVideo() {
  if (videoTimer) {
    clearInterval(videoTimer);
    videoTimer = null;
    document.getElementById("playToggle").innerHTML = '<i data-lucide="play"></i>';
  } else {
    document.getElementById("playToggle").innerHTML = '<i data-lucide="pause"></i>';
    videoTimer = setInterval(() => {
      videoSecond += 1;
      document.getElementById("videoProgress").style.width = `${videoSecond * 5}%`;
      document.getElementById("videoClock").textContent = `00:${String(videoSecond).padStart(2, "0")} / 00:20`;
      if (videoSecond >= 20) resetVideo();
    }, 1000);
  }
  refreshIcons();
}

function renderAll() {
  refreshCounters();
  renderAlerts();
  renderHealth();
  refreshIcons();
}

function refreshIcons() {
  if (window.lucide) lucide.createIcons();
}

document.querySelectorAll(".alert-tabs button").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".alert-tabs button").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    activeFilter = button.dataset.filter;
    renderAll();
  });
});

document.getElementById("searchInput").addEventListener("input", renderAll);
document.getElementById("closeDrawer").addEventListener("click", closeDrawer);
backdrop.addEventListener("click", closeDrawer);
document.querySelectorAll(".frame-strip button").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".frame-strip button").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    document.getElementById("mainEvidence").src = button.dataset.frame;
  });
});
document.getElementById("saveButton").addEventListener("click", () => {
  selectedAlert.state = "resolved";
  selectedAlert.stateText = "Salvo";
  selectedAlert.severity = "ok";
  showToast(`Ocorrencia do cupom ${selectedAlert.receipt} salva.`);
  renderAll();
  closeDrawer();
});
document.getElementById("ignoreButton").addEventListener("click", () => {
  selectedAlert.state = "resolved";
  selectedAlert.stateText = "Ignorado";
  selectedAlert.severity = "ok";
  showToast(`Alerta do cupom ${selectedAlert.receipt} ignorado.`);
  renderAll();
  closeDrawer();
});
document.getElementById("videoButton").addEventListener("click", openVideo);
document.getElementById("closeVideo").addEventListener("click", () => {
  document.getElementById("videoModal").classList.remove("open");
  resetVideo();
});
document.getElementById("playToggle").addEventListener("click", toggleVideo);
document.querySelector(".mobile-menu").addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open"));
document.querySelectorAll(".nav-item[data-view]").forEach(item => {
  item.addEventListener("click", () => {
    document.querySelectorAll(".nav-item[data-view]").forEach(nav => nav.classList.remove("active"));
    item.classList.add("active");
    if (item.dataset.view !== "overview") showToast("Tela incluida na proxima etapa do prototipo.");
  });
});

renderAll();
loadDashboardData();
setInterval(loadDashboardData, 8000);
