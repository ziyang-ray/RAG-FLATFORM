const state = {
  token: sessionStorage.getItem("portal_token") || "",
  me: null,
  departments: [],
  currentDeptId: "",
  resources: [],
  agents: [],
  kbs: [],
  sessionId: "",
  sessions: [],
  shareRequests: [],
  shareTargets: [],
};

/* ============================
   DOM 引用
   ============================ */
const loginCard = document.getElementById("loginCard");
const appWrapper = document.getElementById("appWrapper");
const loginMsg = document.getElementById("loginMsg");
const chatMsg = document.getElementById("chatMsg");
const deptSelect = document.getElementById("deptSelect");
const deptSelectWrap = document.getElementById("deptSelectWrap");
const agentRadioPanel = document.getElementById("agentRadioPanel");
const kbPanelMain = document.getElementById("kbCheckboxPanelMain");
const configModal = document.getElementById("configModal");
const closeConfigBtn = document.getElementById("closeConfigBtn");
const createSessionModalBtn = document.getElementById("createSessionModalBtn");
const selectAllKbMainBtn = document.getElementById("selectAllKbMain");
const deselectAllKbMainBtn = document.getElementById("deselectAllKbMain");
const sessionHistoryEl = document.getElementById("sessionHistory");
const chatBox = document.getElementById("chatBox");
const sessionInfo = document.getElementById("sessionInfo");
const question = document.getElementById("question");
const sessionPrivateToggle = document.getElementById("sessionPrivateToggle");

// KB 上传弹窗
const kbUploadModal = document.getElementById("kbUploadModal");
const openKbUploadBtn = document.getElementById("openKbUploadBtn");
const closeKbUploadBtn = document.getElementById("closeKbUploadBtn");
const kbNameInput = document.getElementById("kbNameInput");
const kbDescInput = document.getElementById("kbDescInput");
const kbFileInput = document.getElementById("kbFileInput");
const kbUploadMsg = document.getElementById("kbUploadMsg");
const kbPrivateToggle = document.getElementById("kbPrivateToggle");
const kbUploadBtn = document.getElementById("kbUploadBtn");

// KB 分享弹窗
const kbShareModal = document.getElementById("kbShareModal");
const openKbShareBtn = document.getElementById("openKbShareBtn");
const openKbChainBtn = document.getElementById("openKbChainBtn");
const kbChainModal = document.getElementById("kbChainModal");
const closeKbChainBtn = document.getElementById("closeKbChainBtn");
const chainKbSelect = document.getElementById("chainKbSelect");
const chainView = document.getElementById("chainView");
const chainMsg = document.getElementById("chainMsg");
const closeKbShareBtn = document.getElementById("closeKbShareBtn");
const shareKbSelect = document.getElementById("shareKbSelect");
const shareScopeSelect = document.getElementById("shareScopeSelect");
const shareTargetWrap = document.getElementById("shareTargetWrap");
const shareTargetPanel = document.getElementById("shareTargetPanel");
const shareUserWrap = document.getElementById("shareUserWrap");
const shareUserInput = document.getElementById("shareUserInput");
const sharePermSelect = document.getElementById("sharePermSelect");
const shareReasonInput = document.getElementById("shareReasonInput");
const shareReqBtn = document.getElementById("shareReqBtn");
const shareReqMsg = document.getElementById("shareReqMsg");

// KB 撤销分享弹窗
const kbUnshareModal = document.getElementById("kbUnshareModal");
const openKbUnshareBtn = document.getElementById("openKbUnshareBtn");
const closeKbUnshareBtn = document.getElementById("closeKbUnshareBtn");
const unshareKbList = document.getElementById("unshareKbList");
const unshareKbBtn = document.getElementById("unshareKbBtn");
const unshareMsg = document.getElementById("unshareMsg");

// KB 删除弹窗
const deleteKbModal = document.getElementById("deleteKbModal");
const openDeleteKbBtn = document.getElementById("openDeleteKbBtn");
const closeDeleteKbBtn = document.getElementById("closeDeleteKbBtn");
const deleteKbList = document.getElementById("deleteKbList");
const confirmDeleteKbBtn = document.getElementById("confirmDeleteKbBtn");
const deleteKbMsg = document.getElementById("deleteKbMsg");

// 分享申请弹窗
const shareRequestsModal = document.getElementById("shareRequestsModal");
const openShareRequestsBtn = document.getElementById("openShareRequestsBtn");
const closeShareRequestsBtn = document.getElementById("closeShareRequestsBtn");
const shareRequestsList = document.getElementById("shareRequestsList");
const shareListMsg = document.getElementById("shareListMsg");
const refreshShareBtn = document.getElementById("refreshShareBtn");
const shareRequestsBtnWrap = document.getElementById("shareRequestsBtnWrap");
const shareReqBadge = document.getElementById("shareReqBadge");

const toggleSessionHistoryBtn = document.getElementById("toggleSessionHistory");
const toggleWorkbenchBtn = document.getElementById("toggleWorkbench");
const toggleKbManagementBtn = document.getElementById("toggleKbManagement");

// 侧边栏 & 切换
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebarToggle");
const sidebarBackdrop = document.getElementById("sidebarBackdrop");
const mobileMenuBtn = document.getElementById("mobileMenuBtn");

// 账户区域
const accountBtn = document.getElementById("accountBtn");
const accountDropdown = document.getElementById("accountDropdown");
const accountName = document.getElementById("accountName");
const logoutBtn = document.getElementById("logoutBtn");

/* ============================
   权限判断
   ============================ */
function isAdminUser() {
  return !!(state.me?.is_admin || state.me?.is_dept_admin);
}
function isSuperAdmin() {
  return !!state.me?.is_admin;
}

function updateSidebarVisibility() {
  const isAdmin = isAdminUser();
  if (shareRequestsBtnWrap) shareRequestsBtnWrap.classList.toggle("hidden", !isAdmin);
  // Share scope selector visible for all users
  shareScopeSelect?.classList.remove("hidden");
  shareUserWrap?.classList.add("hidden");
  shareTargetWrap?.classList.add("hidden");
}

function setLoggedIn(v) {
  loginCard.classList.toggle("hidden", v);
  appWrapper.classList.toggle("hidden", !v);
}

function updateAccountName() {
  if (accountName && state.me) {
    accountName.textContent = state.me.login_id || "用户";
  }
}

/* ============================
   部门标签映射
   ============================ */
const DEPT_TAG_MAP = {
  dept_mp:      { text: "MP",    cls: "tag-mp" },
  dept_mp_q:    { text: "Q",     cls: "tag-q" },
  dept_mp_plm:  { text: "PLM",   cls: "tag-plm" },
  dept_mp_ap:   { text: "AP",    cls: "tag-ai" },
  dept_mp_mc:   { text: "MC",    cls: "tag-mc" },
  dept_mp_usdx: { text: "US&DX", cls: "tag-at" },
  dept_mp_at:   { text: "AT",    cls: "tag-at" },
};

function getDeptTag(deptId) {
  return DEPT_TAG_MAP[deptId] || { text: (deptId || "").replace("dept_", "").toUpperCase(), cls: "tag-mp" };
}

function getResourceTag(resource) {
  if ((resource.title || "").includes("不选择")) {
    return { text: "直接聊天", cls: "tag-direct" };
  }
  const ownerDept = getDeptTag(resource.owner_dept_id);
  const allowDepts = resource.allow_dept_ids || [];
  const myUserId = state.me?.user_id || "";
  const myDeptId = state.me?.default_dept_id || "";
  const isOwner = resource.owner_user_id === myUserId;
  const inOwnerDept = myDeptId === resource.owner_dept_id;
  const myDeptAllowed = allowDepts.includes(myDeptId);
  const myPerm = resource.my_permission || 0;

  // Build permission suffix for shared KBs (bitmask: 4=read, 2=write, 1=share)
  let permSuffix = "";
  if (!isOwner && myPerm > 0) {
    const parts = [];
    if (myPerm & 2) parts.push("可改写");
    if (myPerm & 1) parts.push("可分享");
    if (parts.length) permSuffix = " · " + parts.join(" · ");
    else permSuffix = " · 只读";
  }
  const isSharedToMe = myPerm > 0 && !isOwner;

  // Public: everyone sees it
  if (resource.visibility === "public" || ownerDept.text === "MP") {
    return { deptTags: [], visText: "公开", visCls: "tag-public" };
  }

  // I am the owner
  if (isOwner) {
    if (resource.visibility === "private") {
      return { deptTags: [], visText: "私密", visCls: "tag-private" };
    }
    // dept: show department code
    return { deptTags: [ownerDept], visText: "", visCls: ownerDept.cls };
  }

  // I am NOT the owner
  if (resource.visibility === "private") {
    return { deptTags: [ownerDept], visText: `分享给我${permSuffix}`, visCls: "tag-private" };
  }
  // dept visibility
  if (inOwnerDept) {
    // I see it because I'm in the owner's department
    return { deptTags: [ownerDept], visText: "部门", visCls: ownerDept.cls };
  }
  if (isSharedToMe) {
    // I was specifically shared to (via allow_user_ids)
    return { deptTags: [ownerDept], visText: `分享给我${permSuffix}`, visCls: "tag-private" };
  }
  if (myDeptAllowed) {
    // My department was added to allow_dept_ids
    const myDept = getDeptTag(myDeptId);
    return { deptTags: [ownerDept, myDept], visText: "部门", visCls: ownerDept.cls };
  }
  // Fallback: shouldn't normally reach here if access control is correct
  return { deptTags: [ownerDept], visText: "分享", visCls: "tag-private" };
}

function getResourceTagHtml(resource) {
  const tag = getResourceTag(resource);
  if (tag.deptTags) {
    const deptHtml = tag.deptTags.map(t => `<span class="tag ${t.cls}">${t.text}</span>`).join(",");
    return `${deptHtml}<span class="tag ${tag.visCls}">${tag.visText}</span>`;
  }
  return `<span class="tag ${tag.cls}">${tag.text}</span>`;
}

function getResourceTagText(resource) {
  const tag = getResourceTag(resource);
  if (tag.deptTags) {
    const deptText = tag.deptTags.map(t => t.text).join(",");
    return `[${deptText}, ${tag.visText}]`;
  }
  return `[${tag.text}]`;
}

/* ============================
   API 工具
   ============================ */
async function apiJson(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!options.noJsonContentType) headers["Content-Type"] = "application/json";
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...options, headers });
  return res.json();
}

function appendBubble(role, text, references) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  const content = document.createElement("div");
  content.className = "bubble-content";
  content.textContent = text || "";
  div.appendChild(content);

  const raw = text || "";
  const lineCount = raw.split(/\n/).length;
  if (raw.length > 500 || lineCount > 8) {
    div.classList.add("collapsed");
    const toggle = document.createElement("div");
    toggle.className = "bubble-toggle";
    toggle.textContent = "展开";
    toggle.onclick = () => {
      const collapsed = div.classList.toggle("collapsed");
      toggle.textContent = collapsed ? "展开" : "收起";
    };
    div.appendChild(toggle);
  }

  // 引用依据（仅 assistant 气泡）
  // references 可能是 dict（agent session，key 为 hash ID）或 list（chat session）
  let refList = [];
  if (references && Array.isArray(references)) {
    refList = references;
  } else if (references && typeof references === "object") {
    refList = Object.values(references);
  }
  if (role === "assistant" && refList.length > 0) {
    const refWrap = document.createElement("div");
    refWrap.className = "ref-section";
    const refHeader = document.createElement("div");
    refHeader.className = "ref-header";
    refHeader.textContent = `引用来源（${refList.length} 条）`;
    refHeader.onclick = () => refWrap.classList.toggle("ref-expanded");
    refWrap.appendChild(refHeader);

    const refBody = document.createElement("div");
    refBody.className = "ref-body";
    for (let i = 0; i < refList.length; i++) {
      const chunk = refList[i];
      // agent session 格式化后: content / document_name
      // chat session 原始: content_with_weight / docnm_kwd
      const docName = chunk.document_name || chunk.docnm_kwd || chunk.doc_name || "未知文件";
      const chunkText = chunk.content || chunk.content_with_weight || "";
      if (!chunkText) continue;
      const item = document.createElement("div");
      item.className = "ref-item";
      item.innerHTML = `<div class="ref-doc-name">[${i + 1}] ${escapeHtml(docName)}</div><div class="ref-doc-text">${escapeHtml(chunkText)}</div>`;
      refBody.appendChild(item);
    }
    if (refBody.children.length > 0) {
      refWrap.appendChild(refBody);
      div.appendChild(refWrap);
    }
  }

  chatBox.appendChild(div);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function escapeHtml(str) {
  const el = document.createElement("div");
  el.textContent = str;
  return el.innerHTML;
}

function clearChatView() {
  chatBox.innerHTML = "";
  sessionInfo.textContent = "未选择会话";
  state.sessionId = "";
}

function formatDeptName(d) {
  const code = (d.dept_code || "").toUpperCase();
  const name = d.dept_name || code;
  return `${code} · ${name}`;
}

function agentName(agentId) {
  if (agentId === "direct") return "GPT-5.4";
  const agent = state.agents.find((a) => a.resource_id === agentId);
  return agent ? agent.title : agentId;
}

function selectedKbIds() {
  const source = kbPanelMain;
  return [...source.querySelectorAll("input[type='checkbox']:checked")].map((x) => x.value);
}

function selectedAgentId() {
  const checked = agentRadioPanel?.querySelector("input[type='radio']:checked");
  return checked ? checked.value : "";
}

function kbNameById(kbId) {
  const kb = state.kbs.find(k => k.resource_id === kbId);
  return kb ? kb.title : kbId.slice(0, 8) + "...";
}

/* ============================
   弹窗工具
   ============================ */
function openModal(modal) {
  if (modal) modal.classList.remove("hidden");
}

function closeModal(modal) {
  if (modal) modal.classList.add("hidden");
}

function closeAllModals() {
  [configModal, kbUploadModal, kbShareModal, kbUnshareModal, deleteKbModal, shareRequestsModal].forEach(closeModal);
}

/* ============================
   侧边栏滑动切换
   ============================ */
const workspace = document.querySelector(".workspace");

function toggleSidebar() {
  const isCollapsed = sidebar.classList.toggle("collapsed");
  sidebarToggle.classList.toggle("collapsed", isCollapsed);
  sidebarToggle.textContent = isCollapsed ? "▶" : "◀";
  if (workspace) workspace.classList.toggle("sidebar-collapsed", isCollapsed);
}

function openSidebar() {
  sidebar.classList.add("open");
  if (sidebarBackdrop) sidebarBackdrop.classList.add("open");
}

function closeSidebar() {
  sidebar.classList.remove("open");
  if (sidebarBackdrop) sidebarBackdrop.classList.remove("open");
}

if (sidebarToggle) sidebarToggle.onclick = toggleSidebar;
if (mobileMenuBtn) mobileMenuBtn.onclick = openSidebar;
if (sidebarBackdrop) sidebarBackdrop.onclick = closeSidebar;

/* ============================
   账户下拉
   ============================ */
if (accountBtn) {
  accountBtn.onclick = (e) => {
    e.stopPropagation();
    accountDropdown?.classList.toggle("hidden");
  };
}

document.addEventListener("click", (e) => {
  if (accountDropdown && !accountDropdown.classList.contains("hidden")) {
    if (!accountDropdown.contains(e.target) && e.target !== accountBtn) {
      accountDropdown.classList.add("hidden");
    }
  }
});

if (logoutBtn) logoutBtn.onclick = logout;

/* ============================
   渲染：部门选择
   ============================ */
function renderDepartments() {
  deptSelect.innerHTML = "";
  for (const d of state.departments) {
    const opt = document.createElement("option");
    opt.value = d.dept_id;
    opt.textContent = formatDeptName(d);
    deptSelect.appendChild(opt);
  }
  if (!state.currentDeptId && state.departments.length > 0) {
    state.currentDeptId = state.departments[0].dept_id;
  }
  deptSelect.value = state.currentDeptId;
  updateDeptSelectVisibility();
  updateAccountName();
  updateSidebarVisibility();
}

function updateDeptSelectVisibility() {
  if (!deptSelectWrap) return;
  if ((state.me?.login_id || "").toUpperCase() === "MP") {
    deptSelectWrap.style.display = "";
  } else {
    deptSelectWrap.style.display = "none";
    state.currentDeptId = state.me?.default_dept_id || state.currentDeptId;
  }
}

/* ============================
   渲染：资源列表（智能体 + 知识库）
   ============================ */
function renderResources() {
  state.agents = state.resources.filter((x) => x.resource_type === "agent");
  state.kbs = state.resources.filter((x) => x.resource_type === "kb");

  state.agents.sort((a, b) => {
    const aDirect = (a.title || "").includes("不选择");
    const bDirect = (b.title || "").includes("不选择");
    if (aDirect !== bDirect) return aDirect ? -1 : 1;
    return (a.title || "").localeCompare(b.title || "");
  });

  // 智能体单选面板
  if (agentRadioPanel) {
    agentRadioPanel.innerHTML = "";
    const noneItem = document.createElement("label");
    noneItem.className = "config-item";
    noneItem.innerHTML = `<input type="radio" name="agentChoice" value="" checked /> <span>不使用智能体（直连 GPT-5.4）</span>`;
    agentRadioPanel.appendChild(noneItem);
    for (const a of state.agents) {
      const item = document.createElement("label");
      item.className = "config-item";
      item.innerHTML = `<input type="radio" name="agentChoice" value="${a.resource_id}" /> <span>${getResourceTagHtml(a)} ${a.title}</span>`;
      agentRadioPanel.appendChild(item);
    }
  }

  // 知识库多选面板
  if (kbPanelMain) {
    kbPanelMain.innerHTML = "";
    if (state.kbs.length === 0) {
      kbPanelMain.innerHTML = `<div class="title">当前部门无可用知识库</div>`;
    } else {
      for (const kb of state.kbs) {
        const item = document.createElement("label");
        item.className = "config-item";
        item.innerHTML = `<input type="checkbox" value="${kb.resource_id}" /> <span>${getResourceTagHtml(kb)} ${kb.title}</span>`;
        kbPanelMain.appendChild(item);
      }
    }
  }

  renderShareKbOptions();
  renderUnshareKbOptions();
  renderDeleteKbOptions();
}

/* ============================
   渲染：分享 / 撤销 / 删除选项
   ============================ */
function renderShareKbOptions() {
  // Show KBs that the user owns OR has share permission on
  const owned = state.kbs.filter(
    (k) =>
      k.owner_user_id === state.me?.user_id ||
      (k.my_permission && (k.my_permission & 1))  // has share bit
  );
  shareKbSelect.innerHTML = "";
  if (!owned.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "当前部门无可分享知识库";
    shareKbSelect.appendChild(opt);
    shareKbSelect.disabled = true;
    shareReqBtn.disabled = true;
  } else {
    shareKbSelect.disabled = false;
    shareReqBtn.disabled = false;
    for (const kb of owned) {
      const opt = document.createElement("option");
      opt.value = kb.resource_id;
      opt.textContent = `${getResourceTagText(kb)} ${kb.title}`;
      shareKbSelect.appendChild(opt);
    }
  }
  renderShareTargetPanel();
}

function renderUnshareKbOptions() {
  if (!unshareKbList) return;
  // Show KBs that the user owns AND have been shared (not private)
  const visibleShared = state.kbs.filter((k) =>
    k.owner_user_id === state.me?.user_id &&
    (k.visibility !== "private" || (k.shares && k.shares.length > 0))
  );
  unshareKbList.innerHTML = "";
  if (!visibleShared.length) {
    unshareKbList.innerHTML = `<div class="title">当前无可撤销共享的知识库</div>`;
    unshareKbBtn.disabled = true;
    return;
  }
  unshareKbBtn.disabled = false;
  for (const kb of visibleShared) {
    const item = document.createElement("label");
    item.className = "config-item";
    item.innerHTML = `<input type="checkbox" value="${kb.resource_id}" /> <span>${getResourceTagHtml(kb)} ${kb.title}</span>`;
    unshareKbList.appendChild(item);
  }
}

function renderDeleteKbOptions() {
  if (!deleteKbList) return;
  deleteKbList.innerHTML = "";
  const isMp = (state.me?.login_id || "").toUpperCase() === "MP";
  const canDelete = state.kbs.filter((k) => {
    if (isMp) return true;
    if (k.owner_user_id !== state.me?.user_id) return false;
    if (isAdminUser()) return k.visibility !== "public";
    return k.visibility === "private";
  });
  if (!canDelete.length) {
    deleteKbList.innerHTML = `<div class="title">当前无可删除的知识库</div>`;
    return;
  }
  for (const kb of canDelete) {
    const item = document.createElement("label");
    item.className = "config-item";
    item.innerHTML = `<input type="checkbox" value="${kb.resource_id}" /> <span>${getResourceTagHtml(kb)} ${kb.title}</span>`;
    deleteKbList.appendChild(item);
  }
}

/* ============================
   渲染：分享目标部门
   ============================ */
const sharePermWrap = document.getElementById("sharePermWrap");
function renderShareTargetPanel() {
  const scope = shareScopeSelect.value;
  shareUserWrap?.classList.toggle("hidden", scope !== "user");
  if (sharePermWrap) sharePermWrap.classList.toggle("hidden", scope !== "user");
  shareTargetWrap?.classList.toggle("hidden", scope !== "dept");
  shareTargetPanel.innerHTML = "";

  if (scope === "dept" && shareTargetPanel) {
    // Load all departments for sharing
    shareTargetPanel.innerHTML = '<div style="font-size:12px;color:var(--muted);">加载中...</div>';
    apiJson("/portal/v1/departments?all=true").then(res => {
      shareTargetPanel.innerHTML = "";
      const depts = (res.ok && res.data?.items) ? res.data.items : [];
      for (const d of depts) {
        if (d.dept_id === state.me?.default_dept_id) continue; // skip own dept
        const label = document.createElement("label");
        label.className = "checkline";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = d.dept_id;
        label.appendChild(cb);
        label.appendChild(document.createTextNode(` ${d.dept_code || d.dept_id}`));
        shareTargetPanel.appendChild(label);
      }
      if (!shareTargetPanel.children.length) {
        shareTargetPanel.innerHTML = '<div class="title" style="font-size:12px;color:var(--muted);">无可选部门</div>';
      }
    });
  }
}

/* ============================
   渲染：会话历史
   ============================ */
function renderSessions() {
  sessionHistoryEl.innerHTML = "";
  if (!state.sessions.length) {
    sessionHistoryEl.innerHTML = `<div class="title">暂无会话</div>`;
    return;
  }
  for (const s of state.sessions) {
    const item = document.createElement("div");
    item.className = `session-item ${s.session_id === state.sessionId ? "active" : ""}`;
    const txt = document.createElement("div");
    txt.className = "txt";
    const privacyTag = s.is_private ? "[私密] " : "";
    txt.textContent = `${privacyTag}${agentName(s.agent_id)} · ${(s.updated_at || "").slice(0, 16).replace("T", " ")}`;
    if (s.user_id && s.user_id === state.me?.user_id) {
      const wrap = document.createElement("label");
      wrap.className = "session-privacy-check";
      wrap.onclick = (e) => e.stopPropagation();
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!s.is_private;
      cb.onchange = async () => {
        await onToggleSessionPrivacy(s.session_id, cb.checked);
      };
      const lbl = document.createElement("span");
      lbl.textContent = "私密";
      wrap.appendChild(cb);
      wrap.appendChild(lbl);
      item.appendChild(wrap);
    }
    item.appendChild(txt);
    if (s.user_id && s.user_id === state.me?.user_id) {
      const del = document.createElement("button");
      del.className = "del";
      del.textContent = "🗑";
      del.onclick = async (e) => {
        e.stopPropagation();
        await onDeleteSession(s.session_id);
      };
      item.appendChild(del);
    }
    item.onclick = () => {
      openSession(s.session_id, s.agent_id, s.kb_ids || [], !!s.is_private);
      closeSidebar();
    };
    sessionHistoryEl.appendChild(item);
  }
}

/* ============================
   渲染：分享申请列表
   ============================ */
function renderShareRequests() {
  shareRequestsList.innerHTML = "";
  if (!state.shareRequests.length) {
    shareRequestsList.innerHTML = `<div class="title">暂无分享申请</div>`;
    return;
  }
  const canReview = isAdminUser();
  for (const r of state.shareRequests) {
    const item = document.createElement("div");
    item.className = "share-request-item";
    const targets = (r.target_dept_ids || []).join(", ") || "MP";
    const targetUser = r.target_user_login ? ` | 账户：${r.target_user_login}` : "";
    const statusCls = r.status === "pending" ? "tag-warning" : r.status === "approved" ? "tag-public" : "tag-private";
    item.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <strong>${kbNameById(r.kb_id)}</strong>
        <span class="tag ${statusCls}">${r.status}</span>
      </div>
      <div class="share-request-meta">归属：${r.owner_dept_id} | 范围：${r.target_scope} | 目标：${targets}${targetUser}</div>
      <div class="share-request-meta">申请人：${r.requester_user_id} | 备注：${r.reason || "-"}</div>
    `;
    if (canReview && r.status === "pending") {
      const actions = document.createElement("div");
      actions.className = "share-request-actions";
      const approveBtn = document.createElement("button");
      approveBtn.className = "btn btn-success small";
      approveBtn.textContent = "✓ 批准";
      approveBtn.onclick = (e) => { e.stopPropagation(); onReviewShareRequest(r.request_id, true); };
      const rejectBtn = document.createElement("button");
      rejectBtn.className = "btn btn-danger small";
      rejectBtn.textContent = "✗ 驳回";
      rejectBtn.onclick = (e) => { e.stopPropagation(); onReviewShareRequest(r.request_id, false); };
      actions.appendChild(approveBtn);
      actions.appendChild(rejectBtn);
      item.appendChild(actions);
    }
    if (canReview) {
      const closeBtn = document.createElement("button");
      closeBtn.className = "btn btn-ghost small";
      closeBtn.style.cssText = "position:absolute;top:8px;right:8px;";
      closeBtn.textContent = "×";
      closeBtn.onclick = (e) => { e.stopPropagation(); onDismissShareRequest(r.request_id); };
      item.style.position = "relative";
      item.appendChild(closeBtn);
    }
    shareRequestsList.appendChild(item);
  }
}

function updateShareBadge() {
  if (!shareReqBadge) return;
  const pending = state.shareRequests.filter(r => r.status === "pending").length;
  if (pending > 0) {
    shareReqBadge.textContent = pending;
    shareReqBadge.classList.remove("hidden");
  } else {
    shareReqBadge.classList.add("hidden");
  }
}

/* ============================
   数据加载
   ============================ */
async function loadMeAndDepartments() {
  const meRes = await apiJson("/portal/v1/me");
  if (!meRes.ok) throw new Error(meRes.message || "鉴权失败");
  state.me = meRes.data;
  const depRes = await apiJson("/portal/v1/departments");
  if (depRes.ok && depRes.data && Array.isArray(depRes.data.items)) {
    state.departments = depRes.data.items;
  } else {
    state.departments = (meRes.data.dept_ids || []).map((id) => ({
      dept_id: id,
      dept_code: id.replace("dept_", "").toUpperCase(),
      dept_name: id,
    }));
  }
  state.currentDeptId = meRes.data.default_dept_id || (state.departments[0] || {}).dept_id || "";
  renderDepartments();
}

async function loadResources() {
  chatMsg.textContent = "";
  const res = await apiJson(
    `/portal/v1/resources?resource_type=all&dept_id=${encodeURIComponent(state.currentDeptId)}`
  );
  if (!res.ok) {
    chatMsg.textContent = res.message || "加载资源失败";
    state.resources = [];
  } else {
    state.resources = (res.data && res.data.items) || [];
  }
  renderResources();
}

async function loadSessions() {
  const res = await apiJson(`/portal/v1/sessions?dept_id=${encodeURIComponent(state.currentDeptId)}`);
  if (!res.ok) {
    state.sessions = [];
    renderSessions();
    return;
  }
  state.sessions = (res.data && res.data.items) || [];
  renderSessions();
}

async function openSession(sessionId, agentId, kbIds, isPrivate = false) {
  state.sessionId = sessionId;
  renderSessions();
  chatBox.innerHTML = "";
  const privacyLabel = isPrivate ? "私密" : "公开";
  sessionInfo.textContent = `会话：${sessionId} | ${privacyLabel} | 智能体：${agentName(agentId)} | 知识库：${(kbIds || []).length}`;
  const res = await apiJson(`/portal/v1/sessions/${encodeURIComponent(sessionId)}/messages`);
  if (!res.ok) {
    chatMsg.textContent = res.message || "加载历史消息失败";
    return;
  }
  const items = (res.data && res.data.items) || [];
  for (const m of items) appendBubble(m.role, m.content, m.references);
}

async function loadShareRequests() {
  shareListMsg.textContent = "";
  const res = await apiJson("/portal/v1/share-requests");
  if (!res.ok) {
    state.shareRequests = [];
    shareListMsg.textContent = res.message || "加载分享申请失败";
    renderShareRequests();
    updateShareBadge();
    return;
  }
  state.shareRequests = (res.data && res.data.items) || [];
  renderShareRequests();
  updateShareBadge();
}

async function loadShareTargets() {
  const res = await apiJson("/portal/v1/share-targets");
  if (res.ok && res.data && Array.isArray(res.data.items)) {
    state.shareTargets = res.data.items;
  } else {
    state.shareTargets = [];
  }
  renderShareTargetPanel();
}

/* ============================
   业务操作
   ============================ */
async function onLogin() {
  loginMsg.textContent = "";
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  if (!username || !password) {
    loginMsg.textContent = "请输入用户名和密码";
    return;
  }
  try {
    const res = await apiJson("/portal/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      loginMsg.textContent = res.message || "登录失败";
      return;
    }
    state.token = res.data.access_token;
    sessionStorage.setItem("portal_token", state.token);
    await bootstrapAfterLogin();
  } catch (err) {
    loginMsg.textContent = "网络错误：" + err.message;
    console.error("onLogin error:", err);
  }
}

function logout() {
  state.token = "";
  sessionStorage.removeItem("portal_token");
  setLoggedIn(false);
  loginMsg.textContent = "";
  closeAllModals();
  if (accountDropdown) accountDropdown.classList.add("hidden");
  // Reset to login form
  const loginForm = document.getElementById("loginForm");
  const registerForm = document.getElementById("registerForm");
  if (loginForm) loginForm.style.display = "";
  if (registerForm) registerForm.style.display = "none";
}

/* ============================
   注册表单切换 & 注册逻辑
   ============================ */
const showRegister = document.getElementById("showRegister");
const showLogin = document.getElementById("showLogin");
const loginFormEl = document.getElementById("loginForm");
const registerFormEl = document.getElementById("registerForm");

if (showRegister) showRegister.onclick = (e) => {
  e.preventDefault();
  loginMsg.textContent = "";
  loginFormEl.style.display = "none";
  registerFormEl.style.display = "";
};
if (showLogin) showLogin.onclick = (e) => {
  e.preventDefault();
  loginMsg.textContent = "";
  registerFormEl.style.display = "none";
  loginFormEl.style.display = "";
};

async function onRegister() {
  loginMsg.textContent = "";
  const login_id = document.getElementById("regUsername").value.trim();
  const password = document.getElementById("regPassword").value;
  const dept_id = document.getElementById("regDeptId").value;

  if (!login_id) { loginMsg.textContent = "请输入用户名"; return; }
  if (!password || password.length < 6) { loginMsg.textContent = "密码至少6位"; return; }
  if (!dept_id) { loginMsg.textContent = "请选择所属部门"; return; }

  try {
    const res = await apiJson("/portal/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({
        login_id,
        password,
        dept_id,
      }),
    });
    if (!res.ok) {
      loginMsg.textContent = res.message || "注册失败";
      return;
    }
    loginMsg.style.color = "var(--success)";
    loginMsg.textContent = "注册成功！正在自动登录...";

    // Auto-login after registration
    const loginRes = await apiJson("/portal/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: login_id, password }),
    });
    if (loginRes.ok) {
      state.token = loginRes.data.access_token;
      sessionStorage.setItem("portal_token", state.token);
      await bootstrapAfterLogin();
    } else {
      loginMsg.textContent = "注册成功，请手动登录";
      registerFormEl.style.display = "none";
      loginFormEl.style.display = "";
      document.getElementById("username").value = login_id;
    }
    loginMsg.style.color = "";
  } catch (err) {
    loginMsg.textContent = "网络错误：" + err.message;
    console.error("onRegister error:", err);
  }
}

async function onCreateSession() {
  chatMsg.textContent = "";
  const agentId = selectedAgentId();
  const kbIds = selectedKbIds();
  const isPrivate = !!sessionPrivateToggle?.checked;
  const res = await apiJson("/portal/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      agent_id: agentId,
      kb_ids: kbIds,
      dept_id: state.currentDeptId,
      is_private: isPrivate,
    }),
  });
  if (!res.ok) {
    chatMsg.textContent = res.message || "创建会话失败";
    return;
  }
  await openSession(res.data.session_id, agentId, kbIds, !!res.data.is_private);
  closeModal(configModal);
  closeSidebar();
}

async function onAsk() {
  if (!state.sessionId) {
    chatMsg.textContent = "请先创建或选择一个会话";
    return;
  }
  const q = question.value.trim();
  if (!q) return;
  question.value = "";
  appendBubble("user", q);
  const res = await apiJson("/portal/v1/chat", {
    method: "POST",
    body: JSON.stringify({ session_id: state.sessionId, question: q }),
  });
  if (!res.ok) {
    appendBubble("assistant", res.message || "请求失败");
    return;
  }
  console.log("[DEBUG] references:", res.data.references);
  appendBubble("assistant", res.data.answer, res.data.references);
}

async function onDeleteSession(sessionId) {
  if (!confirm("确定要删除此会话吗？")) return;
  const res = await apiJson(`/portal/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  if (!res.ok) {
    chatMsg.textContent = res.message || "删除失败";
    return;
  }
  if (state.sessionId === sessionId) clearChatView();
  await loadSessions();
}

async function onToggleSessionPrivacy(sessionId, isPrivate) {
  const res = await apiJson(`/portal/v1/sessions/${encodeURIComponent(sessionId)}/privacy`, {
    method: "POST",
    body: JSON.stringify({ is_private: !!isPrivate }),
  });
  if (!res.ok) {
    chatMsg.textContent = res.message || "更新会话隐私失败";
    return;
  }
  await loadSessions();
  if (state.sessionId === sessionId) {
    const s = state.sessions.find(s => s.session_id === sessionId);
    await openSession(sessionId, s?.agent_id || "", s?.kb_ids || [], !!isPrivate);
  }
}

async function onUploadKb() {
  kbUploadMsg.textContent = "";
  const files = kbFileInput.files;
  const name = kbNameInput.value.trim();
  if (!name) {
    kbUploadMsg.textContent = "请输入知识库名称";
    return;
  }
  if (files.length === 0) {
    kbUploadMsg.textContent = "请选择要上传的文件";
    return;
  }
  const formData = new FormData();
  formData.append("kb_name", name);
  if (kbPrivateToggle?.checked) formData.append("is_private", "1");
  const description = (kbDescInput?.value || "").trim();
  if (description) formData.append("description", description);
  for (let i = 0; i < files.length; i++) formData.append("files", files[i]);
  const res = await fetch(`/portal/v1/kbs?dept_id=${encodeURIComponent(state.currentDeptId)}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${state.token}` },
    body: formData,
  });
  const json = await res.json();
  if (!json.ok) {
    kbUploadMsg.textContent = json.message || "上传失败";
    return;
  }
  kbUploadMsg.textContent = "上传成功";
  kbNameInput.value = "";
  kbFileInput.value = "";
  if (kbDescInput) kbDescInput.value = "";
  setTimeout(() => { kbUploadMsg.textContent = ""; }, 2000);
  await loadResources();
}

async function onCreateShareRequest() {
  shareReqMsg.textContent = "";
  const kbId = shareKbSelect.value;
  if (!kbId) {
    shareReqMsg.textContent = "请选择要分享的知识库";
    return;
  }
  const scope = shareScopeSelect.value;
  let targetUserLogin = "";

  if (scope === "user") {
    // Share to specific user (direct, no approval needed)
    targetUserLogin = (shareUserInput?.value || "").trim();
    if (!targetUserLogin) {
      shareReqMsg.textContent = "请输入目标账户名";
      return;
    }
    const permission = sharePermSelect?.value || "read";
    const res = await apiJson(`/portal/v1/kbs/${encodeURIComponent(kbId)}/share-to-user`, {
      method: "POST",
      body: JSON.stringify({ target_user_login: targetUserLogin, permission }),
    });
    if (!res.ok) {
      shareReqMsg.textContent = res.message || "分享失败";
      return;
    }
    // Check if it went through as direct share or pending approval
    if (res.data?.status === "pending") {
      shareReqMsg.textContent = `已提交分享申请，等待管理员审批`;
    } else {
      shareReqMsg.textContent = `已分享给 ${targetUserLogin}（${permission === "write" ? "改写" : "只读"}）`;
    }
    if (shareUserInput) shareUserInput.value = "";
    setTimeout(() => { shareReqMsg.textContent = ""; }, 4000);
    await loadResources();
    return;
  }
  const reason = shareReasonInput.value.trim();
  // Get selected departments for "dept" scope
  let targetDeptIds = [];
  if (scope === "dept" && shareTargetPanel) {
    targetDeptIds = [...shareTargetPanel.querySelectorAll("input[type='checkbox']:checked")].map(cb => cb.value);
    if (!targetDeptIds.length) {
      shareReqMsg.textContent = "请选择目标部门";
      return;
    }
  }
  const reqBody = { target_scope: scope, reason };
  if (targetUserLogin) reqBody.target_user_login = targetUserLogin;
  if (targetDeptIds.length) reqBody.target_dept_ids = targetDeptIds;
  const res = await apiJson(`/portal/v1/kbs/${encodeURIComponent(kbId)}/share-requests`, {
    method: "POST",
    body: JSON.stringify(reqBody),
  });
  if (!res.ok) {
    shareReqMsg.textContent = res.message || "申请失败";
    return;
  }
  shareReqMsg.textContent = "申请已提交";
  shareReasonInput.value = "";
  setTimeout(() => { shareReqMsg.textContent = ""; }, 2000);
  await loadShareRequests();
}

async function onUnshareKb() {
  unshareMsg.textContent = "";
  const selected = [...(unshareKbList?.querySelectorAll("input[type='checkbox']:checked") || [])].map((x) => x.value);
  if (!selected.length) {
    unshareMsg.textContent = "请选择要撤销分享的知识库";
    return;
  }
  for (const kbId of selected) {
    const res = await apiJson(`/portal/v1/kbs/${encodeURIComponent(kbId)}/unshare`, { method: "POST" });
    if (!res.ok) {
      unshareMsg.textContent = res.message || "撤销失败";
      return;
    }
  }
  unshareMsg.textContent = "撤销成功";
  setTimeout(() => { unshareMsg.textContent = ""; }, 2000);
  await loadResources();
}

async function onDeleteKbs() {
  if (!deleteKbList) return;
  if (deleteKbMsg) deleteKbMsg.textContent = "";
  const selected = [...deleteKbList.querySelectorAll("input[type='checkbox']:checked")].map((x) => x.value);
  if (!selected.length) {
    if (deleteKbMsg) deleteKbMsg.textContent = "请先选择要删除的知识库";
    return;
  }
  if (!confirm("确定要删除所选知识库吗？")) return;
  const res = await apiJson("/portal/v1/kbs/delete", {
    method: "POST",
    body: JSON.stringify({ kb_ids: selected }),
  });
  if (!res.ok) {
    if (deleteKbMsg) deleteKbMsg.textContent = res.message || "删除失败";
    return;
  }
  if (deleteKbMsg) deleteKbMsg.textContent = "删除成功";
  setTimeout(() => closeModal(deleteKbModal), 800);
  await loadResources();
}

async function onReviewShareRequest(requestId, approved) {
  const actionPath = approved ? "approve" : "reject";
  const res = await apiJson(`/portal/v1/share-requests/${encodeURIComponent(requestId)}/${actionPath}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    shareListMsg.textContent = res.message || "操作失败";
    return;
  }
  shareListMsg.textContent = approved ? "已批准" : "已驳回";
  setTimeout(() => { shareListMsg.textContent = ""; }, 2000);
  await loadShareRequests();
}

async function onDismissShareRequest(requestId) {
  const res = await apiJson(`/portal/v1/share-requests/${encodeURIComponent(requestId)}/dismiss`, {
    method: "POST",
  });
  if (!res.ok) {
    shareListMsg.textContent = res.message || "删除失败";
    return;
  }
  shareListMsg.textContent = "已移除";
  setTimeout(() => { shareListMsg.textContent = ""; }, 1500);
  await loadShareRequests();
}

/* ============================
   启动引导
   ============================ */
async function bootstrapAfterLogin() {
  setLoggedIn(true);
  clearChatView();
  // 第一步必须成功，否则 token 无效，强制重新登录
  try {
    await loadMeAndDepartments();
  } catch (err) {
    console.error("加载用户信息失败:", err);
    chatMsg.textContent = "登录已过期，请重新登录";
    state.token = "";
    sessionStorage.removeItem("portal_token");
    setLoggedIn(false);
    return;
  }
  const tasks = [
    { fn: loadResources, label: "加载资源" },
    { fn: loadSessions, label: "加载会话" },
    { fn: loadShareRequests, label: "加载分享申请" },
    { fn: loadShareTargets, label: "加载部门列表" },
  ];
  for (const task of tasks) {
    try { await task.fn(); }
    catch (err) {
      console.error(`${task.label}失败:`, err);
      chatMsg.textContent = `${task.label}失败：${err.message}`;
    }
  }
}

/* ============================
   折叠功能
   ============================ */
function toggleCollapsible(container, button, isExpanded) {
  if (!container) return;
  const content = container.querySelector('.collapsible-content');
  const icon = button.querySelector('.collapsible-icon');
  if (!content || !icon) return;
  if (isExpanded === undefined) isExpanded = !content.classList.contains('expanded');
  if (isExpanded) {
    content.classList.add('expanded');
    icon.textContent = '▼';
    button.classList.add('active');
  } else {
    content.classList.remove('expanded');
    icon.textContent = '▶';
    button.classList.remove('active');
  }
}

/* ============================
   事件绑定
   ============================ */
document.getElementById("loginBtn").onclick = onLogin;
document.getElementById("registerBtn").onclick = onRegister;
document.getElementById("askBtn").onclick = onAsk;

// 会话弹窗
document.getElementById("newSessionBtn").onclick = () => openModal(configModal);
if (createSessionModalBtn) createSessionModalBtn.onclick = onCreateSession;
if (closeConfigBtn) closeConfigBtn.onclick = () => closeModal(configModal);

// KB 上传弹窗
if (openKbUploadBtn) openKbUploadBtn.onclick = () => { kbUploadMsg.textContent = ""; openModal(kbUploadModal); };
if (closeKbUploadBtn) closeKbUploadBtn.onclick = () => closeModal(kbUploadModal);
if (kbUploadBtn) kbUploadBtn.onclick = onUploadKb;

// KB 名称模糊匹配
const kbNameDropdown = document.getElementById("kbNameDropdown");
let kbNameSearchTimer = null;
if (kbNameInput) {
  kbNameInput.oninput = () => {
    clearTimeout(kbNameSearchTimer);
    const q = kbNameInput.value.trim();
    if (q.length < 1) { kbNameDropdown?.classList.add("hidden"); return; }
    kbNameSearchTimer = setTimeout(() => {
      // Filter existing KBs by name
      const matches = (state.kbs || []).filter(kb =>
        (kb.title || "").toLowerCase().includes(q.toLowerCase())
      );
      if (!matches.length) { kbNameDropdown?.classList.add("hidden"); return; }
      kbNameDropdown.innerHTML = "";
      for (const kb of matches.slice(0, 10)) {
        const item = document.createElement("div");
        item.className = "autocomplete-item";
        item.innerHTML = `<span class="user-login">${kb.title}</span>`;
        item.onmousedown = () => {
          kbNameInput.value = kb.title;
          kbNameDropdown.classList.add("hidden");
        };
        kbNameDropdown.appendChild(item);
      }
      kbNameDropdown.classList.remove("hidden");
    }, 200);
  };
  kbNameInput.onblur = () => {
    setTimeout(() => { if (kbNameDropdown) kbNameDropdown.classList.add("hidden"); }, 200);
  };
}

// KB 分享弹窗
if (openKbShareBtn) openKbShareBtn.onclick = () => { shareReqMsg.textContent = ""; openModal(kbShareModal); };
if (openKbChainBtn) openKbChainBtn.onclick = () => { if (chainMsg) chainMsg.textContent = ""; chainView.innerHTML = ""; populateChainKbSelect(); openModal(kbChainModal); };
if (closeKbChainBtn) closeKbChainBtn.onclick = () => closeModal(kbChainModal);
if (chainKbSelect) chainKbSelect.onchange = () => loadShareChain(chainKbSelect.value);

async function populateChainKbSelect() {
  if (!chainKbSelect) return;
  chainKbSelect.innerHTML = '<option value="">-- 请选择 --</option>';
  const myId = state.me?.user_id || "";
  for (const kb of (state.kbs || [])) {
    if (kb.owner_user_id === myId) {
      const opt = document.createElement("option");
      opt.value = kb.resource_id;
      opt.textContent = kb.title;
      chainKbSelect.appendChild(opt);
    }
  }
}

async function loadShareChain(kbId) {
  if (!chainView) return;
  chainView.innerHTML = "";
  if (!kbId) return;
  if (chainMsg) chainMsg.textContent = "";
  const res = await apiJson(`/portal/v1/kbs/${encodeURIComponent(kbId)}/shares`);
  if (!res.ok) {
    chainMsg.textContent = res.message || "加载失败";
    return;
  }
  const shares = res.data?.items || [];
  if (!shares.length) {
    chainView.innerHTML = '<div class="chain-empty">此知识库暂无分享记录</div>';
    return;
  }
  // Build tree: find direct shares (shared_by = owner) and re-shares
  const ownerKb = (state.kbs || []).find(k => k.resource_id === kbId);
  const ownerName = ownerKb?.owner_user_id || "owner";

  // Group by sharing level
  const directShares = shares.filter(s => s.shared_by === ownerName);
  const reShares = shares.filter(s => s.shared_by !== ownerName);

  const tree = document.createElement("div");
  tree.className = "chain-tree";

  // Render owner node
  function renderNode(userName, permMask, isOwner, isMe) {
    const node = document.createElement("div");
    node.className = "chain-node";
    const userSpan = document.createElement("span");
    userSpan.className = `chain-user${isOwner ? " chain-owner" : ""}${isMe ? " chain-me" : ""}`;
    userSpan.textContent = isOwner ? `${userName} (所有者)` : userName;
    node.appendChild(userSpan);
    if (permMask && !isOwner) {
      const parts = [];
      if (permMask & 2) parts.push("可改写");
      if (permMask & 1) parts.push("可分享");
      const label = parts.length ? parts.join(" · ") : "只读";
      const permSpan = document.createElement("span");
      permSpan.className = `chain-perm ${permMask & 2 ? "perm-write" : "perm-read"}`;
      permSpan.textContent = label;
      node.appendChild(permSpan);
    }
    return node;
  }

  // Get owner login from state
  const myUserId = state.me?.user_id || "";
  let ownerLoginName = "Owner";
  for (const kb of (state.kbs || [])) {
    if (kb.resource_id === kbId && kb.owner_user_id) {
      // Find login from resources
      ownerLoginName = kb.owner_dept_id?.replace("dept_", "").toUpperCase() + " Admin";
      break;
    }
  }

  // Render direct shares
  if (directShares.length) {
    const ownerNode = renderNode(ownerLoginName, "", true, ownerName === myUserId);
    tree.appendChild(ownerNode);
    for (const s of directShares) {
      const arrow = document.createElement("div");
      arrow.className = "chain-node";
      arrow.innerHTML = `<span class="chain-arrow">→</span>`;
      const displayName = s.target_login || (s.target_user_id ? `用户 ${s.target_user_id.slice(0,8)}` : "公开");
      const userNode = renderNode(displayName, s.permission_mask, false, s.target_user_id === myUserId);
      arrow.appendChild(userNode.firstChild.cloneNode(true));
      if (userNode.children.length > 1) arrow.appendChild(userNode.children[1].cloneNode(true));
      tree.appendChild(arrow);

      // Check for re-shares from this user
      const reSharesFromUser = reShares.filter(rs => rs.shared_by === s.target_user_id);
      if (reSharesFromUser.length) {
        const level = document.createElement("div");
        level.className = "chain-level";
        for (const rs of reSharesFromUser) {
          const rArrow = document.createElement("div");
          rArrow.className = "chain-node";
          rArrow.innerHTML = `<span class="chain-arrow">→</span>`;
          const rDisplayName = rs.target_login || (rs.target_user_id ? `用户 ${rs.target_user_id.slice(0,8)}` : "公开");
          const rUser = renderNode(rDisplayName, rs.permission_mask, false, rs.target_user_id === myUserId);
          rArrow.appendChild(rUser.firstChild.cloneNode(true));
          if (rUser.children.length > 1) rArrow.appendChild(rUser.children[1].cloneNode(true));
          level.appendChild(rArrow);
        }
        tree.appendChild(level);
      }
    }
  }
  chainView.appendChild(tree);
}
if (closeKbShareBtn) closeKbShareBtn.onclick = () => closeModal(kbShareModal);
if (shareReqBtn) shareReqBtn.onclick = onCreateShareRequest;
if (shareScopeSelect) shareScopeSelect.onchange = renderShareTargetPanel;

// 用户搜索模糊匹配
const shareUserDropdown = document.getElementById("shareUserDropdown");
let shareSearchTimer = null;
if (shareUserInput) {
  shareUserInput.oninput = () => {
    clearTimeout(shareSearchTimer);
    const q = shareUserInput.value.trim();
    if (q.length < 1) { shareUserDropdown?.classList.add("hidden"); return; }
    shareSearchTimer = setTimeout(async () => {
      const res = await apiJson(`/portal/v1/users/search?q=${encodeURIComponent(q)}`);
      if (!res.ok || !res.data?.items?.length) { shareUserDropdown?.classList.add("hidden"); return; }
      shareUserDropdown.innerHTML = "";
      for (const u of res.data.items) {
        const item = document.createElement("div");
        item.className = "autocomplete-item";
        item.innerHTML = `<span class="user-login">${u.login_id}</span><span class="user-dept">${u.dept_code || ""} | ${u.display_name || ""}</span>`;
        item.onclick = () => {
          shareUserInput.value = u.login_id;
          shareUserDropdown.classList.add("hidden");
        };
        shareUserDropdown.appendChild(item);
      }
      shareUserDropdown.classList.remove("hidden");
    }, 300);
  };
  shareUserInput.onblur = () => {
    setTimeout(() => { if (shareUserDropdown) shareUserDropdown.classList.add("hidden"); }, 200);
  };
}

// KB 撤销分享弹窗
if (openKbUnshareBtn) openKbUnshareBtn.onclick = () => { unshareMsg.textContent = ""; openModal(kbUnshareModal); };
if (closeKbUnshareBtn) closeKbUnshareBtn.onclick = () => closeModal(kbUnshareModal);
if (unshareKbBtn) unshareKbBtn.onclick = onUnshareKb;

// KB 删除弹窗
if (openDeleteKbBtn) openDeleteKbBtn.onclick = () => { if (deleteKbMsg) deleteKbMsg.textContent = ""; openModal(deleteKbModal); };
if (closeDeleteKbBtn) closeDeleteKbBtn.onclick = () => closeModal(deleteKbModal);
if (confirmDeleteKbBtn) confirmDeleteKbBtn.onclick = onDeleteKbs;

// 分享申请弹窗
if (openShareRequestsBtn) openShareRequestsBtn.onclick = () => { loadShareRequests(); openModal(shareRequestsModal); };
if (closeShareRequestsBtn) closeShareRequestsBtn.onclick = () => closeModal(shareRequestsModal);
if (refreshShareBtn) refreshShareBtn.onclick = loadShareRequests;

// 全选/全不选
if (selectAllKbMainBtn) {
  selectAllKbMainBtn.onclick = () => {
    kbPanelMain.querySelectorAll("input[type='checkbox']").forEach((x) => { x.checked = true; });
  };
}
if (deselectAllKbMainBtn) {
  deselectAllKbMainBtn.onclick = () => {
    kbPanelMain.querySelectorAll("input[type='checkbox']").forEach((x) => { x.checked = false; });
  };
}

// 部门切换
deptSelect.onchange = async (e) => {
  state.currentDeptId = e.target.value;
  clearChatView();
  await loadResources();
  await loadSessions();
  await loadShareRequests();
  await loadShareTargets();
};

// Enter 发送
question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onAsk(); }
});
document.getElementById("password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); onLogin(); }
});
document.getElementById("regPassword").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); onRegister(); }
});

// 折叠绑定
if (toggleSessionHistoryBtn) {
  toggleSessionHistoryBtn.onclick = () => {
    toggleCollapsible(document.getElementById('sessionHistorySection'), toggleSessionHistoryBtn);
  };
}
if (toggleWorkbenchBtn) {
  toggleWorkbenchBtn.onclick = () => {
    toggleCollapsible(document.getElementById('workbenchSection'), toggleWorkbenchBtn);
  };
  setTimeout(() => {
    toggleCollapsible(document.getElementById('workbenchSection'), toggleWorkbenchBtn, true);
  }, 100);
}
if (toggleKbManagementBtn) {
  toggleKbManagementBtn.onclick = () => {
    toggleCollapsible(document.getElementById('kbManagementSection'), toggleKbManagementBtn);
  };
}

// 弹窗背景点击关闭
[configModal, kbUploadModal, kbShareModal, kbUnshareModal, deleteKbModal, shareRequestsModal].forEach(modal => {
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal(modal);
    });
  }
});

/* ============================
   页面加载初始化
   ============================ */
(async function boot() {
  if (!state.token) {
    setLoggedIn(false);
    return;
  }
  try {
    await bootstrapAfterLogin();
  } catch {
    logout();
  }
})();
