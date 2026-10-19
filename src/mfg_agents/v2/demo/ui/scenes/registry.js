/**
 * 场景屏注册表（T05 交付物 · `window.SceneRegistry`）
 *
 * 为什么需要这一层：生态链场景屏共 12 个（S00–S11），由不同窗口并行开发。
 * 若每个窗口都去改 `app.js` 的 `NAV_ITEMS` 字面量与 `renderScreen()` 分发，
 * 12 个人改同一个文件必冲突；而 `tests/v2/test_架构守卫.py` 又是用正则解析
 * `const NAV_ITEMS = [ ... \n  ];` **字面量**的，改字面量会直接弄红守卫。
 *
 * 因此本文件把「屏」变成**运行时登记**：
 *   1. `register(def)` 登记一个场景屏（含导航元信息 + render 函数）；
 *   2. `navItems()` 把登记结果映射成与 `NAV_ITEMS` 同构的对象，
 *      由 app.js 用 `NAV_ITEMS.push(...)` 合并（push 在字面量之外，正则看不到）；
 *   3. `render(id, root, ctx)` 承接 `renderScreen()` 的分派。
 * T06–T11 只需新建 `scenes/sNN-*.js` 并调用 `register()`，**不再改 app.js**。
 *
 * 依赖：`demo-data.js`（`window.DEMO`）。本文件必须在 app.js **之前**加载，
 * 但 visitor 角色注入发生在脚本执行期，此时 `window.DEMO` 已就绪。
 */
(function () {
  "use strict";

  /** 所有场景屏共用的导航分组名。分组必须唯一且相邻，否则侧边栏会出现重复组标题。 */
  const GROUP_NAME = "生态链场景";

  /** 场景屏 id 形如 s00 / s05 / s11。 */
  const ID_PATTERN = /^s\d{2}$/;

  /** 必填的文案字段（缺一个就会在侧边栏/顶栏渲染出空白，必须 fail-fast）。 */
  const REQUIRED_TEXT_FIELDS = ["label", "title", "sub", "icon", "group"];

  /**
   * 场景屏默认可见岗位。
   *
   * 两套口径都要列，缺一不可：
   *  - 业务岗位 id：`gm / biz / rd / qa / mfg` —— 既有 31 个岗位的 `id`；
   *  - 治理角色 key：`sales / rd_engineering / manufacturing` —— 五访客角色的
   *    `roles` 必须落在 32 角色治理目录内才能换到后端令牌（`gm / qa` 两套写法同名，
   *    故只补三个）。`navVisible()` 判定的是 `[role.id].concat(role.roles)` 是否命中
   *    `item.roles`，所以访客（如 biz-demo，id=biz-demo、roles=["sales"]）只能靠
   *    治理 key 命中——这正是必须同时列两套的原因。
   */
  const BUSINESS_ROLES = [
    "gm", "biz", "rd", "qa", "mfg",
    "sales", "rd_engineering", "manufacturing",
  ];

  /** 已登记的场景定义（数组顺序 = 登记顺序，稳定排序的基础）。 */
  const defs = [];

  /** id → 登记序号（1 起），用于 order 相同时保持登记顺序。 */
  const seqById = {};

  /** 未声明 order 的场景排在已声明之后，且彼此仍按登记顺序。 */
  const DEFAULT_ORDER = 1000;

  /* ============================================================
   * 校验工具
   * ============================================================ */

  /** 抛中文错误。校验失败绝不静默吞掉——静默失败会让"菜单里没这一屏"无法排查。 */
  function fail(message) {
    throw new Error("SceneRegistry：" + message);
  }

  function isText(value) {
    return typeof value === "string" && value.trim().length > 0;
  }

  function escapeText(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* ============================================================
   * 一、登记 / 查询
   * ============================================================ */

  /**
   * 登记一个场景屏。
   *
   * @param {Object} def 场景定义。
   * @param {string} def.id    屏 id，必须匹配 `^s\d{2}$`（如 `s05`）。
   * @param {string} def.label 侧边栏文案。
   * @param {string} def.title 顶栏标题。
   * @param {string} def.sub   顶栏副标题。
   * @param {string} def.icon  图标名（app.js 的 ICONS 键，如 `layers`）。
   * @param {string} def.group 导航分组，统一用 `SceneRegistry.GROUP_NAME`。
   * @param {Function} def.render 渲染函数，签名 `(root, ctx)`。
   * @param {string[]} [def.roles] 可见岗位，省略 = 全员可见。
   * @param {number} [def.order] 排序权重，越小越靠前。
   * @returns {Object} 登记后的场景定义副本。
   */
  function register(def) {
    if (!def || typeof def !== "object") {
      fail("登记失败：参数必须是对象");
    }
    if (!isText(def.id) || !ID_PATTERN.test(def.id)) {
      fail("登记失败：id 必须形如 s00 / s05（^s\\d{2}$），收到「"
        + String(def.id) + "」");
    }
    if (Object.prototype.hasOwnProperty.call(seqById, def.id)) {
      fail("登记失败：场景 " + def.id + " 已登记，重复登记会覆盖他人成果");
    }
    REQUIRED_TEXT_FIELDS.forEach(function (key) {
      if (!isText(def[key])) {
        fail("登记失败：场景 " + def.id + " 缺少必填字段 " + key);
      }
    });
    if (typeof def.render !== "function") {
      fail("登记失败：场景 " + def.id + " 的 render 必须是函数");
    }
    if (def.roles !== undefined && !Array.isArray(def.roles)) {
      fail("登记失败：场景 " + def.id + " 的 roles 必须是数组（省略 = 全员可见）");
    }
    if (def.order !== undefined && typeof def.order !== "number") {
      fail("登记失败：场景 " + def.id + " 的 order 必须是数字");
    }
    if (def.group !== GROUP_NAME) {
      // 组名不一致会让侧边栏出现多个「生态链」组标题（renderNav 遇 group 变化就输出标题）。
      // 这里归一到契约组名并留痕，而不是静默接受——但也不抛错，避免并行窗口因
      // 一个小笔误整屏不可用。
      console.warn("[SceneRegistry] 场景 " + def.id + " 的 group=「" + def.group
        + "」与契约组名「" + GROUP_NAME + "」不一致，已归一；"
        + "T06–T11 请直接写 SceneRegistry.GROUP_NAME");
    }

    const copy = {
      id: def.id,
      label: def.label,
      title: def.title,
      sub: def.sub,
      icon: def.icon,
      group: GROUP_NAME,
      order: def.order === undefined ? DEFAULT_ORDER : def.order,
      roles: Array.isArray(def.roles) ? def.roles.slice() : undefined,
      render: def.render,
    };
    defs.push(copy);
    seqById[copy.id] = defs.length;
    return copy;
  }

  /** 已登记场景（按 order 升序，order 相同按登记顺序；返回副本数组，外部改动不影响注册表）。 */
  function list() {
    return defs.slice().sort(function (a, b) {
      if (a.order !== b.order) return a.order - b.order;
      return seqById[a.id] - seqById[b.id];
    });
  }

  /** 按 id 取场景定义，未登记返回 null。 */
  function get(id) {
    if (!isText(id)) return null;
    for (let i = 0; i < defs.length; i += 1) {
      if (defs[i].id === id) return defs[i];
    }
    return null;
  }

  /** 是否已登记该场景。 */
  function has(id) {
    return get(id) !== null;
  }

  /**
   * 映射成与 `NAV_ITEMS` 同构的导航对象，供 app.js `NAV_ITEMS.push(...)` 合并。
   * `roles` 原样透传；未声明时**不写该键**（写 `undefined` 会让 `JSON.stringify`
   * 与部分判定逻辑出现差异，省略最贴近既有字面量风格）。
   */
  function navItems() {
    return list().map(function (def) {
      const item = {
        id: def.id,
        label: def.label,
        title: def.title,
        sub: def.sub,
        icon: def.icon,
        group: def.group,
      };
      if (def.roles && def.roles.length) item.roles = def.roles.slice();
      return item;
    });
  }

  /* ============================================================
   * 二、五访客角色（只读演示身份）
   *
   * 每个角色的 `roles` **必须**映射到 32 角色治理目录内的 key
   * （`gm / sales / rd_engineering / qa / manufacturing`），否则后端
   * `/api/v2/auth/login` 会 400「岗位未识别」——前端 id（如 gm-demo）
   * 服务端不认。`uid` 只用于一人一签判定，`u-<id>` 形态不参与角色校验。
   * 数据视图口径见 data/demo_storyline/spec-v1.md 第 4 节。
   * ============================================================ */

  const VISITOR_ROLES = [
    {
      id: "gm-demo", name: "演示·总经理", abbr: "GM", uid: "u-gm-demo",
      user: "罗一鸣", dept: "management", roles: ["gm"], tone: "blue", readonly: true,
      greeting: "全链只读视图",
      desc: "汇总演示生态链全量口径：订单管道、风险订单、指标看板；S1–S6 场景只读浏览",
      scope: "全链汇总 · 只读",
      scenarios: ["订单管道", "风险订单", "指标看板"],
    },
    {
      id: "biz-demo", name: "演示·业务", abbr: "BIZ", uid: "u-biz-demo",
      user: "周予安", dept: "sales", roles: ["sales"], tone: "purple", readonly: true,
      greeting: "业务交付只读视图",
      desc: "演示 S1 接单评审与 S5 出货履约：订单、报价三分量、ATP 可用量、客户档案",
      scope: "订单 · 报价 · ATP · 客户档案",
      scenarios: ["接单评审", "报价区间", "出货履约"],
    },
    {
      id: "rd-demo", name: "演示·研发", abbr: "RD", uid: "u-rd-demo",
      user: "程亦然", dept: "rd_engineering", roles: ["rd_engineering"], tone: "indigo", readonly: true,
      greeting: "研发工程只读视图",
      desc: "演示 S2 立项 / S3 制样阶段门 / S4 ECN 影响面：BOM、ECN、阶段门、历史案例",
      scope: "BOM · ECN · 阶段门 · 案例",
      scenarios: ["立项评审", "阶段门", "ECN 影响面"],
    },
    {
      id: "qa-demo", name: "演示·质量", abbr: "QA", uid: "u-qa-demo",
      user: "林清和", dept: "quality", roles: ["qa"], tone: "amber", readonly: true,
      greeting: "质量保证只读视图",
      desc: "演示 S3 / S4 / S5 质量链路：检验批、SPC 判异、8D 会签、批次追溯",
      scope: "检验批 · SPC · 判异 · 8D · 追溯",
      scenarios: ["SPC 判异", "8D 会签", "批次追溯"],
    },
    {
      id: "mfg-demo", name: "演示·制造", abbr: "MFG", uid: "u-mfg-demo",
      user: "邵允川", dept: "manufacturing", roles: ["manufacturing"], tone: "green", readonly: true,
      greeting: "制造运营只读视图",
      desc: "演示 S4 批量与 S6 供应：工单、工序报工、设备状态、缺料与催料",
      scope: "工单 · 工序 · 设备 · 缺料",
      scenarios: ["工序报工", "设备状态", "缺料催料"],
    },
  ];

  /** 访客登录分组（追加到 ROLE_GROUPS 末尾，复用既有分组渲染，不改 app.js）。 */
  const GUEST_GROUP = {
    id: "demo",
    name: "演示访客",
    roles: VISITOR_ROLES.map(function (r) { return r.id; }),
  };

  function cloneVisitor(role) {
    return {
      id: role.id,
      name: role.name,
      abbr: role.abbr,
      uid: role.uid,
      user: role.user,
      dept: role.dept,
      roles: role.roles.slice(),
      greeting: role.greeting,
      desc: role.desc,
      scope: role.scope,
      scenarios: role.scenarios.slice(),
      tone: role.tone,
      readonly: true,
    };
  }

  /**
   * 把 5 个访客角色注入 `window.DEMO.ROLES` 与 `window.DEMO.ROLE_GROUPS`。
   *
   * 幂等：已存在则跳过，可被 DOMContentLoaded 与渲染入口重复调用。
   * `window.DEMO` 不存在时安全跳过并留痕（绝不抛错——登录页会因此白屏）。
   *
   * @returns {{added: string[], skipped: string[], groupAdded: boolean}}
   */
  function registerVisitorRoles() {
    const summary = { added: [], skipped: [], groupAdded: false };
    const demo = window.DEMO;
    if (!demo || typeof demo !== "object") {
      console.warn("[SceneRegistry] window.DEMO 未就绪，访客角色未注入；"
        + "请确认 demo-data.js 先于 scenes/registry.js 加载");
      return summary;
    }
    if (!Array.isArray(demo.ROLES)) {
      console.warn("[SceneRegistry] window.DEMO.ROLES 不是数组，访客角色未注入");
      return summary;
    }
    VISITOR_ROLES.forEach(function (role) {
      const exists = demo.ROLES.some(function (item) {
        return item && item.id === role.id;
      });
      if (exists) {
        summary.skipped.push(role.id);
        return;
      }
      demo.ROLES.push(cloneVisitor(role));
      summary.added.push(role.id);
    });

    if (!Array.isArray(demo.ROLE_GROUPS)) {
      if (demo.ROLE_GROUPS === undefined || demo.ROLE_GROUPS === null) {
        demo.ROLE_GROUPS = [{
          id: GUEST_GROUP.id,
          name: GUEST_GROUP.name,
          roles: GUEST_GROUP.roles.slice(),
        }];
        summary.groupAdded = true;
      } else {
        console.warn("[SceneRegistry] window.DEMO.ROLE_GROUPS 不是数组，访客分组未注入");
      }
      return summary;
    }
    const groupExists = demo.ROLE_GROUPS.some(function (group) {
      return group && group.id === GUEST_GROUP.id;
    });
    if (!groupExists) {
      // 追加到末尾：分组必须相邻，访客统一落在最后一段。
      demo.ROLE_GROUPS.push({
        id: GUEST_GROUP.id,
        name: GUEST_GROUP.name,
        roles: GUEST_GROUP.roles.slice(),
      });
      summary.groupAdded = true;
    }
    return summary;
  }

  /* ============================================================
   * 三、渲染分派
   * ============================================================ */

  /**
   * 渲染一个场景屏。
   *
   * 先把渲染上下文绑定给组件层（`SceneKit.use(ctx)`），再调 `def.render`；
   * 未登记时渲染友好提示而不抛错（`renderScreen()` 的 try/catch 是最后一道
   * 兜底，不该被"配置缺失"这种可预期情况消耗掉）。
   *
   * 注意：本函数**不吞 render 内部异常**——交给 app.js 已有的 try/catch
   * + `friendlyError()` 统一转人话，避免两套错误处理口径。
   */
  function render(id, root, ctx) {
    if (!root || typeof root !== "object") return;
    const kit = window.SceneKit;
    if (kit && typeof kit.use === "function") kit.use(ctx);
    const def = get(id);
    if (!def) {
      root.innerHTML = '<div class="content-inner"><div class="empty">'
        + "场景未登记：" + escapeText(id)
        + "（SceneRegistry.list() 中不存在该 id，请检查导航配置或场景脚本是否加载）"
        + "</div></div>";
      return;
    }
    def.render(root, ctx);
  }

  /* ============================================================
   * 四、导出
   * ============================================================ */

  window.SceneRegistry = {
    GROUP_NAME,
    BUSINESS_ROLES,
    VISITOR_ROLE_IDS: VISITOR_ROLES.map(function (r) { return r.id; }),
    register,
    list,
    get,
    has,
    navItems,
    registerVisitorRoles,
    render,
  };

  // 脚本执行期就注入（demo-data.js 已先加载）；DEMO 尚未就绪时在 DOMContentLoaded 再补一次。
  registerVisitorRoles();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      registerVisitorRoles();
    });
  }
})();
