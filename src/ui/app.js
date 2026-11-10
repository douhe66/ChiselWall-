/* 凿壁 ChiselWall · 随手记完整版 UI（Vue3 本地内置，无构建工具；m3-impl-brief §2） */
const { createApp } = Vue;

createApp({
  data() {
    return {
      page: "notes",
      theme: localStorage.getItem("chisel_theme") || "dark",
      sidebarCollapsed: localStorage.getItem("chisel_rail_collapsed") === "1",
      query: "",
      hits: [],
      searched: false,
      busy: false,
      stats: { indexed: 0, pending: 0, error: 0, excluded: 0, mounts: 0, chunks: 0, excluded_by_reason: null },
      mounts: [],
      rules: [],
      failed: [],
      excluded: [],
      groups: [],
      docs: [],
      docsFilter: { mountId: "", groupId: "" },
      errorMsg: "",
      // 分组
      groupAddOpen: false,
      newGroupName: "",
      activeGroup: null,
      groupMembers: [],
      memberForm: { mountId: "", relPath: "" },
      // 挂载
      mountAddOpen: false,
      newMount: "",
      newMountKind: "auto",
      // 排除
      ruleAddOpen: false,
      newRule: { kind: "glob", pattern: "" },
      // M4 随手问（relay 广播问答）
      relay: { enabled: false, connected: false, relay_url: "", nick: "", groups: [], online: [] },
      relayGroups: [],
      relayGroupInfo: [],
      relayForm: { relay_url: "", nick: "", token: "", groups: [] },
      relaySaving: false,
      relayTesting: false,
      relayTestResult: "",
      relayTestTone: "",
      groupsSaving: false,
      groupForm: { groups: [] },
      newGroupName: "",
      newGroupDesc: "",
      askText: "",
      askGroupsSel: [],
      askGroupsTouched: false,
      askState: null,
      askBusy: false,
      askError: "",
      askTimer: null,
      askSessions: [],
      askSession: null,
      askTurns: [],
      askInput: "",
      askSending: false,
      askProgress: "",
      askResultsWidth: parseInt(localStorage.getItem("chisel_ask_results_w") || "340", 10) || 340,
      askSummarizing: false,
      askStream: null,
      askAutoSummary: localStorage.getItem("chisel_ask_auto_summary") !== "0",
      askGroupsOpen: false,
      expandedGroup: "",
      privacyNoticeClosed: localStorage.getItem("chisel_privacy_notice") === "1",
      followupText: "",
      followupBusy: false,
      askFollowupSearch: false,
      askPendingText: "",
      askPendingPhase: "",
      // M8 AI 会话
      chatSessions: [],
      chatSession: null,
      chatMessages: [],
      chatInput: "",
      chatBusy: false,
      chatLastCitations: [],
      // M5 AI
      ai: { ready: false, model: "", base_url: "", has_key: false, masked_key: "" },
      aiForm: { mode: "openai", base_url: "", placeholder_files: {}, headers: [], model: "", api_key: "", temperature: 0.2, use_system_proxy: false, system_prompt: "", max_context_chars: 24000, top_n: 24, max_tokens: 4000 },
      // M7 向量检索
      vector: { ready: false, enabled: false, vectors: 0, error: "", provider: "local", model_dir: "", base_url: "", model: "", has_key: false, dim: 512 },
      vectorForm: { enabled: false, provider: "local", model_dir: "", base_url: "", model: "", api_key: "", dim: 512, use_system_proxy: false },
      vectorSaving: false,
      vectorRebuilding: false,
      rerank: { ready: false, name: "", has_key: false },
      rerankForm: { enabled: false, provider: "http", base_url: "", model: "", api_key: "", api_style: "auto", model_dir: "", max_candidates: 20, top_k: 5 },
      rerankSaving: false,
      rerankTesting: false,
      rerankError: "",
      queryCfg: { ready: false, ai_ready: false, enabled: false, max_queries: 3 },
      queryForm: { enabled: false, max_queries: 3, timeout_s: 4, system_prompt: "" },
      querySaving: false,
      aiSummary: localStorage.getItem("chisel_ai_summary") !== "0",
      aiView: null,
      aiPollTimer: null,
      aiRequestedQid: "",
      aiTesting: false,
      aiSaving: false,
      aiTestError: "",
      // 反馈与鉴权
      toast: { visible: false, text: "", tone: "info" },
      _toastTimer: null,
      needToken: false,
      tokenInput: "",
      tokenError: "",
      token: localStorage.getItem("chisel_token") || "",
    };
  },
  watch: {
    aiSummary(val) {
      localStorage.setItem("chisel_ai_summary", val ? "1" : "0");
    },
  },
  computed: {
    strongHits() {
      return (this.hits || []).filter((h) => (h.tier || "strong") !== "weak");
    },
    weakHits() {
      return (this.hits || []).filter((h) => h.tier === "weak");
    },
    askStrongHits() {
      const all = (this.askState && this.askState.aggregated) || [];
      return all.filter((h) => (h.tier || "strong") !== "weak");
    },
    askWeakHits() {
      const all = (this.askState && this.askState.aggregated) || [];
      return all.filter((h) => h.tier === "weak");
    },
    docsStatusCount() {
      const by = {};
      for (const d of this.docs) by[d.status] = (by[d.status] || 0) + 1;
      const parts = [];
      for (const k of Object.keys(by)) parts.push(this.statusLabel(k) + " " + by[k]);
      return parts.join(" · ") || "";
    },
  },
  methods: {
    kindLabel(kind) {
      const map = { directory: "目录", obsidian: "Obsidian", siyuan: "思源", file: "单文件" };
      return map[kind] || kind;
    },
    statusLabel(status) {
      const map = { indexed: "已索引", pending: "待处理", error: "失败", excluded: "排除" };
      return map[status] || status;
    },
    headers() {
      const h = {};
      if (this.token) h["X-Chisel-Token"] = this.token;
      return h;
    },
    async api(path, opts = {}) {
      opts.headers = Object.assign({}, opts.headers || {}, this.headers());
      const res = await fetch(path, opts);
      if (res.status === 401) {
        this.needToken = true;
        this.tokenError = this.token
          ? "本地 API token 无效或已变更；请粘贴当前 config.json 中的 api_token"
          : "请输入本地 API token";
        throw new Error("need token");
      }
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (e) { /* ignore */ }
        throw new Error(detail || res.statusText);
      }
      return res.json();
    },
    applyTheme() {
      document.documentElement.dataset.theme = this.theme;
    },
    toggleRail() {
      this.sidebarCollapsed = !this.sidebarCollapsed;
      localStorage.setItem("chisel_rail_collapsed", this.sidebarCollapsed ? "1" : "0");
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      localStorage.setItem("chisel_theme", this.theme);
      this.applyTheme();
    },
    focusSearch() {
      if (this.page !== "notes") this.page = "notes";
      this.$nextTick(() => {
        const el = document.querySelector(".search-shell input");
        if (el) el.focus();
      });
    },
    async streamSSE(path, body, onEvent) {
      const res = await fetch(path, {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, this.headers()),
        body: body ? JSON.stringify(body) : undefined,
      });
      if (res.status === 401) {
        this.needToken = true;
        this.tokenError = this.token
          ? "本地 API token 无效或已变更；请粘贴当前 config.json 中的 api_token"
          : "请输入本地 API token";
        throw new Error("need token");
      }
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (e) { /* ignore */ }
        throw new Error(detail || res.statusText);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut = buffer.indexOf("\n\n");
        while (cut >= 0) {
          const chunk = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          let event = "message";
          let data = "";
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (data) {
            try { onEvent(event, JSON.parse(data)); } catch (e) { /* 跳过坏帧 */ }
          }
          cut = buffer.indexOf("\n\n");
        }
      }
    },
    async switchPage(page) {
      this.page = page;
      if (page === "ask") { this.loadRelay(); this.loadAI(); this.loadAskSessions(); this.loadQuery(); }
      if (page === "chat") { this.loadChatSessions(); this.loadAI(); }
      if (page === "settings") { this.loadRelayConfig(); this.loadRelay(); this.loadAI(); this.loadVector(); this.loadRerank(); this.loadQuery(); }
    },
    groupDesc(name) {
      const g = (this.relayGroupInfo || []).find((x) => x.name === name);
      return g ? g.description || "" : "";
    },
    async loadRelayConfig() {
      try {
        const r = await this.api("/api/relay/config");
        this.relay = r;
        this.relayForm = {
          relay_url: r.relay_url || "",
          nick: r.nick || "",
          token: "",
        };
        this.groupForm.groups = [...(r.groups || [])];
      } catch (e) {
        this.showToast("读取 relay 配置失败：" + (e.message || e), "error");
      }
    },
    async saveRelayConfig() {
      if (this.relaySaving) return;
      this.relaySaving = true;
      try {
        const body = {
          relay_url: this.relayForm.relay_url,
          nick: this.relayForm.nick,
        };
        if (this.relayForm.token) body.token = this.relayForm.token;
        this.relay = await this.api("/api/relay/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        this.relayForm.token = "";
        this.groupForm.groups = [...(this.relay.groups || [])];
        this.showToast(this.relay.connected ? "已保存并连接" : "已保存，正在连接…", "info");
        await this.loadRelay();
      } catch (e) {
        this.showToast("保存失败：" + (e.message || e), "error");
      } finally {
        this.relaySaving = false;
      }
    },
    async testRelay() {
      if (this.relayTesting) return;
      this.relayTesting = true;
      this.relayTestResult = "正在测试连接…";
      this.relayTestTone = "";
      try {
        const body = {
          relay_url: this.relayForm.relay_url,
          nick: this.relayForm.nick,
          groups: this.groupForm.groups,
        };
        if (this.relayForm.token) body.token = this.relayForm.token;
        const r = await this.api("/api/relay/test", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        this.relayTestResult = "连接成功：" + (r.nick || this.relayForm.nick) + " · 在线 " + (r.online || 0) + " 人 · " + (r.groups || 0) + " 组 · " + (r.elapsed_ms || 0) + "ms";
        this.relayTestTone = "ok";
      } catch (e) {
        this.relayTestResult = "连接失败：" + (e.message || e);
        this.relayTestTone = "error";
      } finally {
        this.relayTesting = false;
      }
    },
    async createGroup() {
      if (!this.newGroupName.trim()) {
        this.showToast("组名不能为空", "error");
        return;
      }
      try {
        await this.api("/api/relay/groups", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: this.newGroupName.trim(), description: this.newGroupDesc.trim() }),
        });
        this.showToast("已建组；在列表中勾选「加入」后保存", "info");
        this.newGroupName = "";
        this.newGroupDesc = "";
        await this.loadRelay();
      } catch (e) {
        this.showToast("建组失败：" + (e.message || e), "error");
      }
    },
    async loadRelay() {
      try {
        this.relay = await this.api("/api/relay");
        const raw = this.relay.server_groups || this.relay.known_groups || [];
        const info = raw.map((g) => (typeof g === "string" ? { name: g, description: "", online: 0 } : g));
        this.relayGroupInfo = info;
        this.relayGroups = info.length ? info.map((g) => g.name) : ["全员组"];
        const joined = (this.relay.groups || []).filter((g) => this.relayGroups.includes(g));
        if (this.askGroupsTouched) {
          // 用户手动勾选过：只保留仍然加入的组（允许不勾选任何组，提问时会提示）
          this.askGroupsSel = this.askGroupsSel.filter((g) => joined.includes(g));
        } else {
          // 默认只选已加入的组；未加入任何组时提问页不会显示可勾选组
          this.askGroupsSel = joined;
        }

      } catch (e) {
        if (String(e.message) !== "need token") this.askError = String(e.message || e);
      }
    },
    askGroupChoices() {
      const joined = this.relay.groups || [];
      return (this.relayGroupInfo || []).filter((g) => joined.includes(g.name));
    },
    async saveGroupMembership() {
      if (this.groupsSaving) return;
      this.groupsSaving = true;
      try {
        const body = { groups: this.groupForm.groups };
        this.relay = await this.api("/api/relay/config", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        this.groupForm.groups = [...(this.relay.groups || [])];
        this.showToast("提问组成员已更新，正在重连 relay…", "info");
        await this.loadRelay();
      } catch (e) {
        this.showToast("保存提问组失败：" + (e.message || e), "error");
      } finally {
        this.groupsSaving = false;
      }
    },
    saveAskAuto() {
      localStorage.setItem("chisel_ask_auto_summary", this.askAutoSummary ? "1" : "0");
    },
    askLatestPayload() {
      const turns = this.askTurns || [];
      for (let i = turns.length - 1; i >= 0; i -= 1) {
        const t = turns[i];
        if (t.role === "user" && t.payload) return t.payload;
      }
      return null;
    },
    askLatestHits() {
      const p = this.askLatestPayload();
      return p ? (p.aggregated || []) : [];
    },
    async summarizeAskSession() {
      if (!this.askSession || this.askSummarizing) return;
      if (!this.askLatestHits().length) {
        this.showToast("本轮还没有搜索结果，先提问收集结果", "error");
        return;
      }
      this.askSummarizing = true;
      this.askStream = { active: true, text: "", citations: [] };
      this.scrollChat();
      try {
        await this.streamSSE("/api/ask-sessions/" + this.askSession.id + "/summarize/stream", null, (ev, data) => {
          if (ev === "start") {
            this.askStream.citations = data.citations || [];
          } else if (ev === "delta") {
            this.askStream.text += data.text || "";
            this.scrollChat();
          } else if (ev === "done") {
            this.askTurns = data.turns || this.askTurns;
            this.askStream = null;
            this.scrollChat();
            this.loadAskSessions();
          } else if (ev === "error") {
            this.askStream = null;
            this.showToast(data.error || "AI 综合失败", "error");
          }
        });
      } catch (e) {
        this.askStream = null;
        this.showToast("AI 综合失败：" + (e.message || e), "error");
      } finally {
        this.askSummarizing = false;
        if (this.askStream) this.askStream = null;
      }
    },
    startAskResize(e) {
      e.preventDefault();
      const startX = e.clientX;
      const startW = this.askResultsWidth;
      const move = (ev) => {
        const next = Math.max(260, Math.min(760, startW + (startX - ev.clientX)));
        this.askResultsWidth = Math.round(next);
      };
      const up = () => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
        localStorage.setItem("chisel_ask_results_w", String(this.askResultsWidth));
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    },
    async loadAskSessions() {
      try {
        this.askSessions = await this.api("/api/ask-sessions");
      } catch (e) {
        if (String(e.message) !== "need token") this.askError = String(e.message || e);
      }
    },
    async newAskSession() {
      try {
        const s = await this.api("/api/ask-sessions", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "新会话" }),
        });
        await this.loadAskSessions();
        await this.openAskSession(s.id);
      } catch (e) {
        this.showToast("新建会话失败：" + (e.message || e), "error");
      }
    },
    scrollChat() {
      this.$nextTick(() => {
        const el = document.querySelector(".chat-messages");
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
    async openAskSession(id) {
      try {
        const r = await this.api("/api/ask-sessions/" + id);
        this.askSession = r.session;
        this.askTurns = r.turns || [];
        this.askError = "";
        this.scrollChat();
      } catch (e) {
        this.showToast("打开会话失败：" + (e.message || e), "error");
      }
    },
    async renameAskSession(s) {
      const title = prompt("会话名称", s.title || "");
      if (title === null || !title.trim()) return;
      try {
        await this.api("/api/ask-sessions/" + s.id, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim() }),
        });
        await this.loadAskSessions();
        if (this.askSession && this.askSession.id === s.id) this.askSession.title = title.trim();
      } catch (e) {
        this.showToast("改名失败：" + (e.message || e), "error");
      }
    },
    async deleteAskSession(s) {
      if (!confirm("删除会话「" + s.title + "」？消息不可恢复。")) return;
      try {
        await this.api("/api/ask-sessions/" + s.id, { method: "DELETE" });
        if (this.askSession && this.askSession.id === s.id) { this.askSession = null; this.askTurns = []; }
        await this.loadAskSessions();
      } catch (e) {
        this.showToast("删除失败：" + (e.message || e), "error");
      }
    },
    askHasTurns() {
      return (this.askTurns || []).some((t) => t.role === "user");
    },
    async sendAsk() {
      const text = (this.askInput || "").trim();
      if (!text || this.askSending) return;
      this.askGroupsTouched = true;
      if (!this.askGroupsSel.length) {
        this.askError = "请先加入并选择一个提问组（设置 → 提问组）";
        this.showToast(this.askError, "error");
        return;
      }
      this.askError = "";
      this.askSending = true;
      this.askProgress = "";
      try {
        if (!this.askSession) {
          const s = await this.api("/api/ask-sessions", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "新会话" }),
          });
          this.askSession = s;
          await this.loadAskSessions();
        }
        const sid = this.askSession.id;
        this.askPendingText = text;
        this.askPendingPhase = (this.queryCfg.enabled && this.queryCfg.ready) ? "正在智能改写问题…" : "正在广播问题…";
        this.askProgress = this.askPendingPhase;
        this.askInput = "";
        this.scrollChat();
        const forceSearch = !this.askHasTurns() || this.askFollowupSearch || !this.askLatestHits().length;
        if (forceSearch) {
          this.askProgress = "正在拆分检索词并广播…";
          const r = await this.api("/api/ask-sessions/" + sid + "/ask", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, groups: this.askGroupsSel }),
          });
          this.askFollowupSearch = false;
          this.askProgress = "正在收集同事回答…";
          await this.openAskSession(sid);
          this.askPendingText = "";
          this.askPendingPhase = "";
          this.startAskPoll(r.qid, r.turn_id);
        } else if (this.askAutoSummary) {
          this.askPendingPhase = "正在生成回答…";
          this.askProgress = "正在生成回答…";
          this.askStream = { active: true, text: "", citations: [] };
          this.scrollChat();
          await this.streamSSE("/api/ask-sessions/" + sid + "/followup/stream", { text, summarize: true }, (ev, data) => {
            if (ev === "start") {
              if (data.user_turn) this.askTurns = [...this.askTurns, data.user_turn];
              this.askPendingText = "";
              this.askPendingPhase = "";
              this.askStream.citations = data.citations || [];
            } else if (ev === "delta") {
              this.askStream.text += data.text || "";
              this.scrollChat();
            } else if (ev === "done") {
              this.askTurns = data.turns || this.askTurns;
              this.askPendingText = "";
              this.askPendingPhase = "";
              this.askStream = null;
              this.scrollChat();
              this.loadAskSessions();
            } else if (ev === "error") {
              this.askPendingText = "";
              this.askPendingPhase = "";
              this.askStream = null;
              this.showToast(data.error || "追问失败", "error");
            }
          });
          this.askSending = false;
        } else {
          const r = await this.api("/api/ask-sessions/" + sid + "/followup", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, summarize: false }),
          });
          this.askTurns = r.turns || [];
          this.askPendingText = "";
          this.askPendingPhase = "";
          this.scrollChat();
          await this.loadAskSessions();
          this.askSending = false;
          this.showToast("已记录追问；点「AI 综合」生成回答", "info");
        }
      } catch (e) {
        this.askError = String(e.message || e);
        this.showToast("提问失败：" + this.askError, "error");
        this.askPendingText = "";
        this.askPendingPhase = "";
        if (!this.askInput) this.askInput = text;
        this.askSending = false;
      }
    },
    startAskPoll(qid, turnId) {
      this.stopAskPoll();
      this.askTimer = setInterval(async () => {
        try {
          const v = await this.api("/api/ask/" + qid);
          this.askProgress = "已收 " + (v.responded || 0) + "/" + (v.expected || 0) + " 位同事…";
          if (v.status === "done" || v.status === "error") {
            this.stopAskPoll();
            if (v.status === "error") this.askError = "提问被 relay 拒绝：" + (v.error || "未知原因") + "（限流：默认每 5 秒 1 问）";
            this.askProgress = "AI 综合中…";
            const r = await this.api("/api/ask-sessions/" + this.askSession.id + "/complete", {
              method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ turn_id: turnId, qid }),
            });
            this.askTurns = r.turns || [];
            this.scrollChat();
            await this.loadAskSessions();
            if (this.askAutoSummary) {
              this.askProgress = "AI 综合中…";
              await this.summarizeAskSession();
            }
            this.askSending = false;
            this.askProgress = "";
          }
        } catch (e) {
          this.stopAskPoll();
          this.askSending = false;
          this.askError = String(e.message || e);
        }
      }, 500);
    },
    stopAskPoll() {
      if (this.askTimer) { clearInterval(this.askTimer); this.askTimer = null; }
    },
    formatElapsed(ms) {
      const n = Number(ms);
      if (!Number.isFinite(n) || n <= 0) return "<1ms";
      return n + "ms";
    },
    routeLabel(route) {
      if (route === "plan") return "计划命中";
      if (route === "hybrid") return "混合命中";
      if (route === "vector") return "语义命中";
      if (route === "jieba" || route === "jieba_and") return "主索引";
      return "兜底命中";
    },
    routeClass(route) {
      if (route === "plan") return "route-hybrid";
      if (route === "hybrid") return "route-hybrid";
      if (route === "vector") return "route-vector";
      if (route === "jieba" || route === "jieba_and") return "route-main";
      return "route-fallback";
    },
    async askFollowup() {
      const qid = this.askState && this.askState.qid;
      const text = (this.followupText || "").trim();
      if (!qid || !text || this.followupBusy) return;
      this.followupBusy = true;
      try {
        const r = await this.api("/api/ask/" + qid + "/followup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        this.askState = Object.assign({}, this.askState, { followups: r.followups || [] });
        this.followupText = "";
      } catch (e) {
        this.showToast("追问失败：" + (e.message || e), "error");
      } finally {
        this.followupBusy = false;
      }
    },
    async loadChatSessions() {
      try {
        this.chatSessions = await this.api("/api/ai/sessions");
      } catch (e) {
        if (String(e.message) !== "need token") this.showToast("读取会话失败：" + (e.message || e), "error");
      }
    },
    async newChatSession() {
      try {
        const s = await this.api("/api/ai/sessions", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "新会话" }),
        });
        await this.loadChatSessions();
        await this.openChatSession(s.id);
      } catch (e) {
        this.showToast("新建会话失败：" + (e.message || e), "error");
      }
    },
    async openChatSession(id) {
      try {
        const r = await this.api("/api/ai/sessions/" + id);
        this.chatSession = r.session;
        this.chatMessages = r.messages || [];
        this.chatLastCitations = [];
      } catch (e) {
        this.showToast("打开会话失败：" + (e.message || e), "error");
      }
    },
    async renameChatSession(s) {
      const title = prompt("会话名称", s.title || "");
      if (title === null || !title.trim()) return;
      try {
        await this.api("/api/ai/sessions/" + s.id, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim() }),
        });
        await this.loadChatSessions();
        if (this.chatSession && this.chatSession.id === s.id) this.chatSession.title = title.trim();
      } catch (e) {
        this.showToast("改名失败：" + (e.message || e), "error");
      }
    },
    async deleteChatSession(s) {
      if (!confirm("删除会话「" + s.title + "」？消息不可恢复。")) return;
      try {
        await this.api("/api/ai/sessions/" + s.id, { method: "DELETE" });
        if (this.chatSession && this.chatSession.id === s.id) {
          this.chatSession = null; this.chatMessages = []; this.chatLastCitations = [];
        }
        await this.loadChatSessions();
      } catch (e) {
        this.showToast("删除失败：" + (e.message || e), "error");
      }
    },
    async sendChat() {
      const text = (this.chatInput || "").trim();
      if (!this.chatSession || !text || this.chatBusy) return;
      this.chatBusy = true;
      try {
        const r = await this.api("/api/ai/sessions/" + this.chatSession.id + "/ask", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
        });
        this.chatMessages = r.messages || [];
        this.chatLastCitations = (r.answer && r.answer.citations) || [];
        this.chatInput = "";
        await this.loadChatSessions();
        this.chatSession = r.session || this.chatSession;
      } catch (e) {
        this.showToast("提问失败：" + (e.message || e), "error");
      } finally {
        this.chatBusy = false;
      }
    },
    async loadVector() {
      try {
        this.vector = await this.api("/api/vector/config");
        this.vectorForm = {
          enabled: !!this.vector.enabled,
          provider: this.vector.provider || "local",
          model_dir: this.vector.model_dir || "",
          base_url: this.vector.base_url || "",
          model: this.vector.model || "",
          api_key: "",
          dim: this.vector.dim || 512,
          use_system_proxy: !!this.vector.use_system_proxy,
        };
      } catch (e) {
        // 读取失败不阻断页面
      }
    },
    async saveVectorConfig() {
      if (this.vectorSaving) return;
      this.vectorSaving = true;
      try {
        const body = {
          enabled: !!this.vectorForm.enabled,
          provider: this.vectorForm.provider,
          model_dir: this.vectorForm.model_dir,
          base_url: this.vectorForm.base_url,
          model: this.vectorForm.model,
          dim: Number(this.vectorForm.dim) || 512,
          use_system_proxy: !!this.vectorForm.use_system_proxy,
        };
        if (this.vectorForm.api_key) body.api_key = this.vectorForm.api_key;
        this.vector = await this.api("/api/vector/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        this.vectorForm.api_key = "";
        this.showToast(this.vector.ready ? "向量检索已启用" : "已保存（未就绪或未启用）", "info");
      } catch (e) {
        this.showToast("保存失败：" + (e.message || e), "error");
      } finally {
        this.vectorSaving = false;
      }
    },
    async rebuildVector() {
      if (this.vectorRebuilding) return;
      if (!this.vector.ready) {
        this.showToast("先启用并保存向量配置（或等模型就绪）", "error");
        return;
      }
      this.vectorRebuilding = true;
      this.showToast("正在重建向量索引（文档多时需要一会儿）…", "info");
      try {
        const r = await this.api("/api/vector/rebuild", { method: "POST" });
        this.vector = r.status || this.vector;
        this.showToast("向量重建完成：" + (r.chunks || 0) + " 块", "info");
      } catch (e) {
        this.showToast("重建失败：" + (e.message || e), "error");
      } finally {
        this.vectorRebuilding = false;
      }
    },
    async loadRerank() {
      try {
        this.rerank = await this.api("/api/rerank/config");
        this.rerankForm = {
          enabled: !!this.rerank.enabled,
          provider: this.rerank.provider || "http",
          base_url: this.rerank.base_url || "",
          model: this.rerank.model || "",
          api_key: "",
          api_style: this.rerank.api_style || "auto",
          model_dir: this.rerank.model_dir || "",
          max_candidates: this.rerank.max_candidates || 20,
          top_k: this.rerank.top_k || 5,
        };
      } catch (e) {
        // 不阻断页面
      }
    },
    async saveRerankConfig() {
      if (this.rerankSaving) return;
      this.rerankSaving = true;
      this.rerankError = "";
      try {
        const body = {
          enabled: !!this.rerankForm.enabled,
          provider: this.rerankForm.provider,
          base_url: this.rerankForm.base_url,
          model: this.rerankForm.model,
          api_style: this.rerankForm.api_style,
          model_dir: this.rerankForm.model_dir,
          max_candidates: Number(this.rerankForm.max_candidates) || 20,
          top_k: Number(this.rerankForm.top_k) || 5,
        };
        if (this.rerankForm.api_key) body.api_key = this.rerankForm.api_key;
        this.rerank = await this.api("/api/rerank/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        this.rerankForm.api_key = "";
        this.showToast(this.rerank.ready ? "重排已启用" : "已保存（未就绪或未启用）", "info");
      } catch (e) {
        this.rerankError = String(e.message || e);
        this.showToast("保存失败：" + this.rerankError, "error");
      } finally {
        this.rerankSaving = false;
      }
    },
    async testRerank() {
      if (this.rerankTesting) return;
      this.rerankTesting = true;
      this.rerankError = "";
      try {
        const r = await this.api("/api/rerank/test", { method: "POST" });
        this.showToast("reRank 可用：" + (r.name || "") + "，分数 " + (r.scores || []).join(" / "), "info");
      } catch (e) {
        this.rerankError = String(e.message || e);
        this.showToast("测试失败：" + this.rerankError, "error");
      } finally {
        this.rerankTesting = false;
      }
    },
    async loadQuery() {
      try {
        this.queryCfg = await this.api("/api/query/config");
        this.queryForm = {
          enabled: !!this.queryCfg.enabled,
          max_queries: this.queryCfg.max_queries || 3,
          timeout_s: this.queryCfg.timeout_s || 4,
          system_prompt: this.queryCfg.system_prompt || this.queryCfg.default_prompt || "",
        };
      } catch (e) {
        // 不阻断页面
      }
    },
    async saveQueryConfig() {
      if (this.querySaving) return;
      this.querySaving = true;
      try {
        this.queryCfg = await this.api("/api/query/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled: !!this.queryForm.enabled,
            max_queries: Number(this.queryForm.max_queries) || 3,
            timeout_s: Number(this.queryForm.timeout_s) || 4,
            system_prompt: this.queryForm.system_prompt || "",
          }),
        });
        this.showToast(this.queryCfg.ready ? "查询改写已启用" : "已保存（需要 AI 配置/启用）", "info");
      } catch (e) {
        this.showToast("保存失败：" + (e.message || e), "error");
      } finally {
        this.querySaving = false;
      }
    },
    resetQueryPrompt() {
      this.queryForm.system_prompt = this.queryCfg.default_prompt || "";
    },
    async loadAI() {
      try {
        this.ai = await this.api("/api/ai/config");
        if (!this.aiForm.base_url && this.ai.base_url) this.aiForm.base_url = this.ai.base_url;
        if (!this.aiForm.model && this.ai.model) this.aiForm.model = this.ai.model;
        this.aiForm.mode = this.ai.mode || "openai";
        this.aiForm.placeholder_files = Object.assign({}, this.ai.placeholder_files || {});
        this.aiForm.headers = (this.ai.headers || []).map((h) => ({ name: h.name || "", value: "", has_value: !!h.has_value }));
        this.aiForm.use_system_proxy = !!this.ai.use_system_proxy;
        this.aiForm.system_prompt = this.ai.system_prompt || "";
        this.aiForm.max_context_chars = this.ai.max_context_chars || 24000;
        this.aiForm.top_n = this.ai.top_n || 24;
        this.aiForm.max_tokens = this.ai.max_tokens || 4000;
      } catch (e) {
        // AI 配置读取失败不阻断页面；设置页保存/测试时会给出明确错误
      }
    },
    syncAIFormFromPublic() {
      this.aiForm.mode = this.ai.mode || "openai";
      this.aiForm.placeholder_files = Object.assign({}, this.ai.placeholder_files || {});
      this.aiForm.headers = (this.ai.headers || []).map((h) => ({ name: h.name || "", value: "", has_value: !!h.has_value }));
    },
    placeholderLabel(name) {
      const labels = { host: "主机地址", path: "请求路径", tenant: "租户", region: "区域" };
      return labels[name] || ("变量 " + name);
    },
    aiPlaceholders() {
      const text = this.aiForm.base_url || "";
      const out = [];
      const re = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
      let m;
      while ((m = re.exec(text))) {
        if (!out.includes(m[1])) out.push(m[1]);
      }
      return out;
    },
    addAIHeader() {
      this.aiForm.headers.push({ name: "", value: "", has_value: false });
    },
    removeAIHeader(i) {
      this.aiForm.headers.splice(i, 1);
    },
    async copyText(text) {
      if (!text) return;
      try {
        await navigator.clipboard.writeText(String(text));
        this.showToast("已复制到剪贴板", "info");
      } catch (e) {
        this.showToast("复制失败：请手动选中错误文本后 Ctrl+C", "error");
      }
    },
    copyAIError() {
      this.copyText(this.aiTestError);
    },
    async saveAIConfig() {
      if (this.aiSaving) return;
      this.aiSaving = true;
      try {
        const body = {
          mode: this.aiForm.mode || "openai",
          base_url: this.aiForm.base_url,
          placeholder_files: this.aiForm.placeholder_files || {},
          headers: (this.aiForm.headers || []).filter((h) => h.name).map((h) => ({ name: h.name, value: h.value || "" })),
          model: this.aiForm.model,
          temperature: Number(this.aiForm.temperature) || 0.2,
          use_system_proxy: !!this.aiForm.use_system_proxy,
          system_prompt: this.aiForm.system_prompt || "",
          max_context_chars: Number(this.aiForm.max_context_chars) || 24000,
          top_n: Number(this.aiForm.top_n) || 24,
          max_tokens: Number(this.aiForm.max_tokens) || 4000,
        };
        if (this.aiForm.api_key) body.api_key = this.aiForm.api_key;
        this.ai = await this.api("/api/ai/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        this.aiForm.api_key = "";
        this.syncAIFormFromPublic();
        this.showToast(this.ai.ready ? "AI 配置已保存并生效" : "已保存，但 base_url / model 未填全，AI 未就绪", "info");
      } catch (e) {
        this.showToast(String(e.message || e), "error");
      } finally {
        this.aiSaving = false;
      }
    },
    async testAI() {
      if (this.aiTesting) return;
      this.aiTesting = true;
      this.aiTestError = "";
      this.showToast("正在测试连接（使用表单中当前填写的内容，无需先保存）…", "info");
      try {
        const body = {
          mode: this.aiForm.mode || "openai",
          base_url: this.aiForm.base_url,
          placeholder_files: this.aiForm.placeholder_files || {},
          headers: (this.aiForm.headers || []).filter((h) => h.name).map((h) => ({ name: h.name, value: h.value || "" })),
          model: this.aiForm.model,
          use_system_proxy: !!this.aiForm.use_system_proxy,
        };
        if (this.aiForm.api_key) body.api_key = this.aiForm.api_key;
        const r = await this.api("/api/ai/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        this.showToast("模型连通：" + (r.model || "") + "（" + r.elapsed_ms + "ms）", "info");
        this.ai = await this.api("/api/ai/config");
      } catch (e) {
        this.aiTestError = String(e.message || e);
        this.showToast("测试失败（错误已保留在下方，可复制分享）", "error");
      } finally {
        this.aiTesting = false;
      }
    },
    renderMarkdown(text, opts = {}) {
      const esc = (s) =>
        String(s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
      let src = esc(text || "");
      const fences = (src.match(/```/g) || []).length;
      if (fences % 2 === 1) src += "\n```";
      const blocks = [];
      src = src.replace(/```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g, (m, lang, code) => {
        const label = (lang || "code").toLowerCase();
        blocks.push(
          '<div class="md-code"><div class="md-code-head">' + label + "</div><pre><code>" +
            code.replace(/\n$/, "") + "</code></pre></div>"
        );
        return "@@CODE" + (blocks.length - 1) + "@@";
      });
      const inline = (s) => {
        let out = s
          .replace(/`([^`]+)`/g, "<code>$1</code>")
          .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
          .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
          .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        if (opts.citations) {
          out = out.replace(/\[([0-9]+)\]/g, '<a class="cite-link" href="#cite-$1">[$1]</a>');
        }
        return out;
      };
      const splitRow = (line) =>
        line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      const lines = src.split(/\r?\n/);
      const out = [];
      let listType = "";
      let listBuf = [];
      const flushList = () => {
        if (!listBuf.length) return;
        out.push("<" + listType + ">" + listBuf.map((i) => "<li>" + inline(i) + "</li>").join("") + "</" + listType + ">");
        listBuf = [];
        listType = "";
      };
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) { flushList(); continue; }
        if (/^@@CODE\d+@@$/.test(line)) { flushList(); out.push(line); continue; }
        const h = line.match(/^(#{1,6})\s+(.*)$/);
        if (h) {
          flushList();
          const lvl = Math.min(h[1].length + 2, 6);
          out.push("<h" + lvl + ">" + inline(h[2]) + "</h" + lvl + ">");
          continue;
        }
        if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { flushList(); out.push("<hr>"); continue; }
        if (line.startsWith("&gt;")) {
          flushList();
          out.push("<blockquote>" + inline(line.replace(/^&gt;\s?/, "")) + "</blockquote>");
          continue;
        }
        if (line.indexOf("|") >= 0 && i + 1 < lines.length &&
            /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(lines[i + 1].trim())) {
          flushList();
          const header = splitRow(line);
          const rows = [];
          i += 2;
          while (i < lines.length && lines[i].trim().indexOf("|") >= 0) {
            rows.push(splitRow(lines[i].trim()));
            i++;
          }
          i--;
          out.push(
            '<div class="md-table"><table><thead><tr>' +
              header.map((c) => "<th>" + inline(c) + "</th>").join("") +
              "</tr></thead><tbody>" +
              rows.map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") +
              "</tbody></table></div>"
          );
          continue;
        }
        const ol = line.match(/^\d+[.、)]\s+(.*)$/);
        const ul = line.match(/^[-*•]\s+(.*)$/);
        if (ol) {
          if (listType && listType !== "ol") flushList();
          listType = "ol";
          listBuf.push(ol[1]);
          continue;
        }
        if (ul) {
          if (listType && listType !== "ul") flushList();
          listType = "ul";
          listBuf.push(ul[1]);
          continue;
        }
        flushList();
        out.push("<p>" + inline(line) + "</p>");
      }
      flushList();
      return out.join("\n").replace(/@@CODE(\d+)@@/g, (m, i) => blocks[Number(i)] || "");
    },
    renderAnswer(text) {
      return this.renderMarkdown(text, { citations: true });
    },
    renderSnippet(hit) {
      return this.renderMarkdown((hit && hit.snippet_text) || "", {});
    },
    async toggleLocalPreview(hit) {
      const src = this.localSource(hit);
      if (!src) return;
      if (hit.previewOpen) { hit.previewOpen = false; return; }
      hit.previewOpen = true;
      hit.preview = "加载中…";
      try {
        const r = await this.api("/api/docs/" + src.doc_id + "?max_chars=6000");
        hit.preview = r.text;
      } catch (e) {
        hit.preview = "无法预览：" + (e.message || e);
      }
    },
    localSource(hit) {
      const sources = (hit && hit.sources) || [];
      return sources.find((s) => s && s.node === this.relay.nick) || null;
    },
    remoteSource(hit) {
      const sources = (hit && hit.sources) || [];
      return sources.find((s) => s && s.node !== this.relay.nick) || null;
    },
    async fetchRemote(hit) {
      const src = this.remoteSource(hit);
      if (!src) return;
      if (hit.previewOpen) {
        hit.previewOpen = false;
        return;
      }
      hit.previewOpen = true;
      hit.preview = "正在向 @" + src.node + " 请求全文…";
      try {
        const r = await this.api("/api/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ node: src.node, doc_id: src.doc_id }),
        });
        hit.preview = r.text;
      } catch (e) {
        hit.preview = "无法获取远端全文：" + (e.message || e);
      }
    },
    maybeStartAISummary(qid, view) {
      if (!this.aiSummary || !this.ai.ready) return;
      if (!view || view.status === "collecting") return;
      if (this.aiRequestedQid === qid) return;
      this.aiRequestedQid = qid;
      this.aiView = { status: "generating", answer: "", citations: [], model: this.ai.model, elapsed_ms: 0 };
      this.pollAISummary(qid);
    },
    pollAISummary(qid) {
      clearInterval(this.aiPollTimer);
      this.aiPollTimer = setInterval(async () => {
        try {
          const r = await this.api("/api/ai/ask/" + qid);
          if (r.ai) this.aiView = r.ai;
          if (r.ai && r.ai.status !== "generating") {
            clearInterval(this.aiPollTimer);
            this.aiPollTimer = null;
          }
        } catch (e) {
          clearInterval(this.aiPollTimer);
          this.aiPollTimer = null;
          this.aiView = { status: "error", answer: "", citations: [], error: String(e.message || e), model: "", elapsed_ms: 0 };
        }
      }, 800);
    },
    showToast(text, tone) {
      this.toast = { visible: true, text, tone };
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => { this.toast.visible = false; }, 3500);
    },
    async refresh() {
      try {
        this.stats = await this.api("/api/stats");
        this.mounts = await this.api("/api/mounts");
        this.rules = await this.api("/api/excludes");
        this.failed = await this.api("/api/failed");
        this.excluded = await this.api("/api/excluded");
        this.groups = await this.api("/api/groups");
        this.errorMsg = "";
      } catch (e) {
        if (String(e.message) !== "need token") this.errorMsg = String(e.message || e);
      }
      this.loadDocs();
      if (this.activeGroup) {
        const still = this.groups.find((g) => g.group_id === this.activeGroup.group_id);
        if (!still) { this.activeGroup = null; this.docsFilter.groupId = ""; this.groupMembers = []; }
        else { this.activeGroup = still; this.loadGroupDetail(still.group_id); }
      }
    },
    async loadDocs() {
      try {
        const p = [];
        if (this.docsFilter.mountId) p.push("mount_id=" + encodeURIComponent(this.docsFilter.mountId));
        if (this.docsFilter.groupId) p.push("group_id=" + encodeURIComponent(this.docsFilter.groupId));
        this.docs = await this.api("/api/docs?" + p.join("&"));
      } catch (e) { /* 静默：refresh 已展示错误 */ }
    },
    // ---- 搜索 ----
    clearSearch() {
      this.searched = false;
      this.hits = [];
      this.query = "";
      this.errorMsg = "";
    },
    async doSearch() {
      if (!this.query) return;
      this.busy = true;
      try {
        const r = await this.api("/api/search?q=" + encodeURIComponent(this.query));
        this.hits = r.hits;
        this.searched = true;
      } catch (e) { this.errorMsg = String(e.message || e); }
      finally { this.busy = false; }
    },
    // ---- 分组（§15.6 / GR-07） ----
    async addGroup() {
      if (!this.newGroupName) return;
      try {
        await this.api("/api/groups", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: this.newGroupName }),
        });
        this.newGroupName = "";
        this.groupAddOpen = false;
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    async removeGroup(g) {
      try {
        await this.api("/api/groups/" + g.group_id, { method: "DELETE" });
        if (this.docsFilter.groupId === g.group_id) this.docsFilter.groupId = "";
        if (this.activeGroup && this.activeGroup.group_id === g.group_id) { this.activeGroup = null; this.groupMembers = []; }
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    dismissPrivacyNotice() {
      this.privacyNoticeClosed = true;
      localStorage.setItem("chisel_privacy_notice", "1");
    },
    async toggleMount(m) {
      try {
        const r = await this.api("/api/mounts/" + m.mount_id, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: m.enabled === false }),
        });
        m.enabled = !!r.enabled;
        await this.refresh();
        this.showToast(r.enabled ? "挂载已启用" : "挂载已暂停；不参与搜索，文档仍可预览/打开", "info");
      } catch (e) {
        this.showToast("挂载状态修改失败：" + (e.message || e), "error");
      }
    },
    async setDocShare(d, state) {
      try {
        await this.api("/api/docs/" + d.doc_id + "/sharing", {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state }),
        });
        d.share_state = state;
        this.showToast(state === "shared" ? "已设为共享" : (state === "private" ? "已设为仅本机：远端不可搜，本机仍可搜" : "已隐藏：本机与远端都不参与搜索"), "info");
      } catch (e) {
        this.showToast("共享状态修改失败：" + (e.message || e), "error");
      }
    },
    toggleGroupMembers(name) {
      this.expandedGroup = this.expandedGroup === name ? "" : name;
    },
    selectGroup(g) {
      this.activeGroup = g;
      this.docsFilter.groupId = g ? g.group_id : "";
      this.docsFilter.mountId = "";
      this.groupMembers = [];
      if (g) this.loadGroupDetail(g.group_id);
      this.loadDocs();
    },
    visibleMounts() {
      if (!this.activeGroup) return this.mounts;
      const ids = new Set((this.groupMembers || []).map((m) => Number(m.mount_id)));
      return this.mounts.filter((m) => ids.has(Number(m.mount_id)));
    },
    filterMount(m) {
      this.docsFilter.mountId = this.docsFilter.mountId === m.mount_id ? "" : m.mount_id;
      this.loadDocs();
    },
    onDocsGroupFilter() {
      const g = this.groups.find((x) => x.group_id === this.docsFilter.groupId) || null;
      this.activeGroup = g;
      this.groupMembers = [];
      if (g) this.loadGroupDetail(g.group_id);
      this.loadDocs();
    },
    toggleGroupFilter(g) {
      this.docsFilter.groupId = this.docsFilter.groupId === g.group_id ? "" : g.group_id;
      this.loadDocs();
    },
    async toggleGroupDetail(g) {
      this.activeGroup = this.activeGroup && this.activeGroup.group_id === g.group_id ? null : g;
      this.groupMembers = [];
      if (this.activeGroup) await this.loadGroupDetail(g.group_id);
    },
    async loadGroupDetail(groupId) {
      try { this.groupMembers = await this.api("/api/groups/" + groupId + "/members"); }
      catch (e) { /* ignore */ }
    },
    async addMember() {
      if (!this.activeGroup || !this.memberForm.mountId) return;
      try {
        await this.api("/api/groups/" + this.activeGroup.group_id + "/members", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mount_id: this.memberForm.mountId, rel_path: this.memberForm.relPath }),
        });
        this.memberForm.relPath = "";
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    async removeMember(m) {
      try {
        await this.api("/api/groups/" + m.group_id + "/members/" + m.member_id, { method: "DELETE" });
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    async excludeGroup(g) {
      try {
        const r = await this.api("/api/groups/" + g.group_id + "/exclude", { method: "POST" });
        this.showToast(
          "整组排除已生效：生成 " + (r.rules_created ? r.rules_created.length : 0) + " 条排除规则（可在排除面板单独删除）",
          "info"
        );
        this.refresh();
      } catch (e) { this.showToast("整组排除失败：" + (e.message || e), "error"); }
    },
    // ---- 挂载 ----
    async addMount() {
      if (!this.newMount) return;
      try {
        const body = { path: this.newMount };
        if (this.newMountKind && this.newMountKind !== "auto") body.kind = this.newMountKind;
        await this.api("/api/mounts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        this.newMount = "";
        this.mountAddOpen = false;
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    async removeMount(id) {
      try {
        await this.api("/api/mounts/" + id, { method: "DELETE" });
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    // ---- 排除规则 ----
    async addRule() {
      if (!this.newRule.pattern) return;
      try {
        await this.api("/api/excludes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this.newRule),
        });
        this.newRule.pattern = "";
        this.ruleAddOpen = false;
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    async removeRule(id) {
      try {
        await this.api("/api/excludes/" + id, { method: "DELETE" });
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    // ---- 打开与预览 ----
    async openLocation(docId) {
      try {
        const r = await this.api("/api/docs/" + docId + "/open-location");
        this.showToast(r.hint || ("已在文件管理器中定位（" + r.how + "）"), "info");
      } catch (e) {
        this.showToast("打开所在位置失败：" + (e.message || e), "error");
      }
    },
    async openFile(hit) {
      try {
        const r = await this.api("/api/docs/" + hit.doc_id + "/open");
        this.showToast(
          r.hint || ("已用系统默认程序打开" + (r.how ? "（" + r.how + "）" : "")),
          r.ok ? "info" : "error"
        );
        hit.alts = (r.alternatives && r.alternatives.length) ? r.alternatives : [];
      } catch (e) {
        this.showToast("直接打开失败：" + (e.message || e), "error");
      }
    },
    async openWith(hit, app) {
      try {
        const r = await this.api("/api/docs/" + hit.doc_id + "/open-with?app=" + encodeURIComponent(app));
        this.showToast(r.hint || ("已用" + r.how + "打开"), r.ok ? "info" : "error");
        if (r.ok) hit.alts = [];
      } catch (e) {
        this.showToast("打开失败：" + (e.message || e), "error");
      }
    },
    async togglePreview(hit) {
      if (hit.previewOpen) { hit.previewOpen = false; return; }
      hit.previewOpen = true;
      try {
        const r = await this.api("/api/docs/" + hit.doc_id + "?max_chars=2000");
        hit.preview = r.text;
      } catch (e) {
        hit.preview = "无法预览：" + (e.message || e);
      }
    },
    async toggleDocPreview(d) {
      await this.togglePreview(d);
    },
    // ---- 其他 ----
    async reindex() {
      try {
        await this.api("/api/reindex", { method: "POST" });
        this.refresh();
      } catch (e) { this.errorMsg = String(e.message || e); }
    },
    async saveToken() {
      const candidate = (this.tokenInput || "").trim();
      if (!candidate) {
        this.tokenError = "请输入本地 API token";
        return;
      }
      this.tokenError = "";
      try {
        const res = await fetch("/api/stats", { headers: { "X-Chisel-Token": candidate } });
        if (res.status === 401) {
          this.needToken = true;
          this.tokenError = "token 无效：请使用当前运行节点的 config.json 中的 api_token";
          return;
        }
        if (!res.ok) throw new Error("HTTP " + res.status);
        this.token = candidate;
        localStorage.setItem("chisel_token", candidate);
        this.tokenInput = "";
        this.needToken = false;
        this.tokenError = "";
        await this.refresh();
      } catch (e) {
        this.tokenError = "校验失败：" + (e.message || e);
      }
    },
  },
  mounted() {
    this.applyTheme();
    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === "k") {
        e.preventDefault();
        this.focusSearch();
      }
    });
    this.refresh();
    this.loadRelay();
    this.loadAI();
    this.loadVector();
    this.loadRerank();
    this.loadQuery();
    setInterval(() => this.refresh(), 5000); // 索引进度轮询
    setInterval(() => { if (this.page === "ask") this.loadRelay(); }, 15000); // relay 在线状态
  },
}).mount("#app");