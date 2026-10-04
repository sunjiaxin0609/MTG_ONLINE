# MTG Online UI 深色化与自适应改版 · 实现计划

- 日期：2026-10-04
- 对应规格：[2026-10-04-mtgo-ui-dark-adaptive-redesign-design.md](../specs/2026-10-04-mtgo-ui-dark-adaptive-redesign-design.md)
- 约束：不改动 `mtg/engine/**`、`mtg/data/**`；保留 `theme.py` 已有常量名，避免 UI 层连锁改名。

## 验证工具

- 截图：`python tests/ui_shot.py <回合数> <输出路径>`（默认 7 回合 / `data/_shot.png`）。
- 导入检查：`python -c "import mtg.ui.app"`。
- 冒烟：`python -m mtg.ui.app` 需能正常起窗（或用 `启动游戏.bat`）。
- 每完成一个任务即运行截图，记录输出行（含尺寸与统计），作为该任务验收证据。

---

## 阶段 1 · 地基

### 任务 1.1 重写 `theme.py` 为深色主题
- 文件：`mtg/ui/theme.py`（69 行 → 约 85 行）
- 改动：
  - 按规格 §3.1 替换 `BG / PANEL / PANEL_DARK / BORDER / TEXT / TEXT_DIM / TEXT_LIGHT / CARD_BG / CARD_BORDER`。
  - 按 §3.2 替换 `COLOR_HEX`（W/U/B/R/G/C/M）与 `ACCENT`。
  - 按 §3.3 替换 `SELECTED / ATTACKING / BLOCKING / TAPPED / HIGHLIGHT`；新增 `DIM_MASK = "#0e1116"`。
  - 新增 `OURS = "#4d9fe0"`、`THEIRS = "#e0604a"`、`AMBER = "#d8a84e"`。
  - `FONT_LOG` 改为 `(FONT_FAMILY, 9)`；新增 `FONT_PROMPT = (FONT_FAMILY, 12, "bold")`。
  - 保留 `color_key()` 不变。
- 验证：`python -c "import mtg.ui.app"` 通过；截图出图正常（配色已变深）。

### 任务 1.2 新增 `layout.py`
- 文件：新建 `mtg/ui/layout.py`（纯函数，无 tk 依赖）
- 内容：
  - `CARD_TIERS = {"S": (84,118,80,112), "M": (100,140,96,134), "L": (116,162,112,156), "XL": (132,184,128,178)}`（卡宽/卡高/图宽/图高）。
  - `pick_tier(avail_w: int, min_columns: int) -> str`：从 XL 向 S 试探，返回第一个 `avail_w // (W+4) >= min_columns` 的档；都不满足返回 "S"。
  - `columns_for(avail_w: int, tier: str) -> int`：`max(1, avail_w // (W+4))`。
  - `window_size(screen_w: int, screen_h: int) -> tuple[int,int]`：`(min(1400, screen_w-80), min(900, screen_h-80))`，下限 `(1024,700)`。
  - `ROW_WEIGHTS` 常量表（§4.2）：`{"opp":3, "mid":1.6, "mine":3, "hand":2.4}`、`FIXED_HEIGHTS = {"header":40, "action":44}`、各区域 `(min,max)`。
- 验证：`python -c "from mtg.ui.layout import pick_tier, columns_for, window_size; print(pick_tier(1100,5), columns_for(1100,'L'), window_size(1920,1080))"` 输出合理值。

### 任务 1.3 `card_widget.py` 支持尺寸档
- 文件：`mtg/ui/card_widget.py`
- 改动：
  - `CardWidget.__init__` 增加 `size: str = "M"` 参数；由 `layout.CARD_TIERS` 取 `(W,H,PW,PH)` 存为实例属性 `self.card_w/card_h/pic_w/pic_h`。
  - 类属性 `WIDTH/HEIGHT` 保留为 M 档默认值（供旧引用与 `card_grid` 兼容），但实例布局一律用实例尺寸。
  - `_build_skeleton` 的 `wraplength` 改为 `self.card_w - 8`；骨架卡底/文字色按深色主题调整（当前写死的 `#1a1a1a / #3a3a3a / #55504a / #6b6558` 需改为 `TEXT / TEXT_DIM` 家族）。
  - `_load_art` / `set_dimmed` 中的 `PIC_W/PIC_H` 改为 `self.pic_w/self.pic_h`。
  - `_show_art` 徽章与副标题用的浅色底 `#f7f4e8` 改为深色徽章（`#0e1116` 底 + `AMBER` 边框 + 亮字）。
  - `HOVER_W/HOVER_H` 保留不变（放大预览仍用高清图）。
- 验证：截图出图 → 卡牌尺寸随档变化、文字可读。

### 任务 1.4 `images.py` 缓存上限与淘汰
- 文件：`mtg/ui/images.py`
- 改动：
  - `ImageCache` 新增 `self._order: collections.OrderedDict`（键 = `(token,w,h,dim)`）与 `MAX_PHOTO_CACHE = 300`。
  - `_make_photo` 产出后写入 `_order` 并调用 `_evict()`；`peek` 命中时 `move_to_end`。
  - `_evict()`：超过上限时按 LRU 丢弃最旧键并从 `_photos` 删除（已绑定到 `Label` 的 `PhotoImage` 仍被 Tk 引用，不会闪退）。
- 验证：`python -c "import mtg.ui.images"` 通过；长时间/多档切换截图无异常。

### 任务 1.5 抽出 `dialogs.py`
- 文件：新建 `mtg/ui/dialogs.py`；从 `app.py` 迁移：
  - `new_game_dialog`（新游戏设置，含 `COLOR_CHOICES` 与 `_list_preset_decks` / `_colors_of_deck` / `_opposite_colors` 辅助函数一并迁入或就地引用）。
  - `show_zones` / `show_library`（区域与牌库列表，复用 `card_grid`）。
  - `_show_game_over`（对局结束弹窗）。
  - `scry_decision` / `surveil_decision`（占卜/探查决策窗）——保留为独立函数，参数改为显式传入 `parent`、`payload`、`callback`，去掉对 `MTGApp` 的隐式依赖。
  - `card_grid` 一并迁入（作为列表渲染工具）。
- `app.py` 侧改为 `from .dialogs import ...` 并删除原实现。
- 验证：导入通过；起窗后"新游戏"能打开设置窗并开局。

### 任务 1.6 抽出 `log_panel.py`
- 文件：新建 `mtg/ui/log_panel.py`
- 内容：`class LogPanel(tk.Frame)`：
  - `__init__(master, ...)`：`Text` + `Scrollbar`，`state="disabled"`，`FONT_LOG`，深色底。
  - `append(text: str)`：按规格 §5.3 的行首正则分类着色后插入并自动滚到底。
  - `clear()`。
  - `set_collapsed(bool)`：折叠/展开（供窄窗默认折叠逻辑调用，见任务 1.7）。
- `app.py` 的 `_append_log` / `_on_engine_log` 改为调用 `self.log.append(...)`；保留分类正则集中在本文件。
- 验证：日志中文清晰、分色正确、滚动到底。

### 任务 1.7 抽出 `board.py` 并改造 `app.py` 布局
- 文件：新建 `mtg/ui/board.py`；修改 `app.py`
- `board.py` 内容：
  - `class CardStrip(tk.Frame)`（从 `app.py` 迁移）：升级为先按容器宽度调 `layout.pick_tier` / `columns_for` 再布局；`_relayout` 内按档位调整每张卡尺寸。
  - `render_board(...)` / `render_hand(...)` / `render_permanent(...)` 等牌桌渲染函数（从 `app.py` 迁移，改为接收显式上下文对象而非直接读 `MTGApp` 属性）。
- `app.py` 布局改造：
  - `geometry` 用 `layout.window_size(winfo_screenwidth(), winfo_screenheight())`；`minsize(1024,700)`。
  - `board_frame` 改 `grid` 布局，按 `layout.ROW_WEIGHTS` + 各区域 min/max 设置 `rowconfigure(weight=)`；header / action 条用固定高度 + `grid_propagate(False)`。
  - 删除全部硬编码高度：`height=38 / 200 / 76 / 192 / 46`、`width=300`。
  - 绑定 `<Configure>`（节流）在窗口尺寸变化时重算区域高度并触发重排。
  - 日志栏折叠：宽度 < 1200 默认折叠（调 `LogPanel.set_collapsed`），提供折叠按钮，用户手动切换后以手动状态为准。
- 目标：`app.py` 降到约 400 行，只留组装、`pump` / `_ai_act` / 决策分发 / 事件绑定。
- 验证：
  - `python -c "import mtg.ui.app"` 通过。
  - `python tests/ui_shot.py 4 data/_ui_after_s1.png` 出图，尺寸打印合理。
  - 手动把窗口缩到 `1024×700` 检查无重叠/溢出（截图 + 目视）。

---

## 阶段 2 · 信息层

### 任务 2.1 顶部回合·阶段条
- 文件：`board.py`（渲染）、`app.py`（组装）
- 改动：顶部固定条改为三段：左（我方/对手回合信息，蓝/红）→ 中（阶段名，当前阶段 `AMBER` 高亮）→ 右（卡池/卡图信息）。回合信息与阶段按 70/30 分割。
- 验证：截图顶部一眼可见回合归属与当前阶段。

### 任务 2.2 不可出牌原因角标
- 文件：`app.py`（`_is_playable`）、`board.py`（手牌渲染）
- 改动：
  - `_is_playable(card) -> tuple[bool, str]`：地 → `human.can_play_land()`（不满足时给"本回合已下地"）；否则用 `game.can_cast` 的 `reason`。
  - 手牌渲染时，不可出的牌调用 `CardWidget` 角标显示简短原因（映射：`不在手牌中/地不是咒语/需法术时机/法术力不足` → 短标签）。
  - tooltip 显示完整原因 + 现有卡面放大。
- 验证：截图可见灰化手牌 + 角标；悬停显示原因。

### 任务 2.3 日志分类着色 + 字体
- 文件：`log_panel.py`
- 改动：落实 §5.3 正则表（回合头 / 阶段 / 施放与入堆叠 / 结算 / 触发 / 战斗 / 警示）。字体沿用任务 1.6 的 `FONT_LOG`。
- 验证：截图日志各类型颜色区分明显。

### 任务 2.4 状态栏拆分
- 文件：`app.py` / `board.py`
- 改动：卡池/卡图信息移到顶部右侧（任务 2.1 已预留）；套牌名、颜色、剩余牌库数下移到中条信息区，与阶段控制并排。
- 验证：截图中条显示套牌信息；顶部右侧显示卡池统计。

---

## 阶段 3 · 交互层

### 任务 3.1 修复"不阻挡" bug
- 文件：`app.py`
- 改动：新增 `_declare_no_blockers()`：清空 `self.pending_blocks` 后提交空阻挡声明；把"不阻挡"按钮的 `command` 从 `_confirm_blockers` 改为它。
- 验证：分配部分阻挡后点"确认阻挡"→ 按分配执行；点"不阻挡"→ 全部不挡，二者行为不同。

### 任务 3.2 产费选色
- 文件：`app.py`（`_tap_for_mana`）
- 改动：收集该永久物所有 `is_mana_ability` 异能；若 >1 或存在多色组合，弹 `tk.Menu` 让用户选（`_ability_colors` 提供候选色）；单一则直接 `activate_ability`。
- 验证：多色地产费出现选色菜单；单色地直接产费。

### 任务 3.3 操作条重做 + 非法操作红色提示
- 文件：`board.py`（操作条渲染）、`app.py`（交互）
- 改动：
  - 操作条三段：左主提示 `FONT_PROMPT`；中上下文按钮（按 `Decision.kind`）；右快捷键灰字提示。
  - 新增 `flash_error(msg)`：操作条内红色提示，`after(2000, 清除)`，取消占用中的旧定时器。所有 `messagebox.showinfo("提示", ...)` 的错误路径改走它（保留真正需要阻塞确认的弹窗）。
- 验证：非法操作出现红色提示且 2 秒消失；操作条提示随决策变化。

### 任务 3.4 快捷键
- 文件：`app.py`
- 改动：绑定 `空格`=让过/结束阶段、`Enter`=确认待选、`Esc`=清除选择、`A`=全选攻击者、`Ctrl+Enter`=结束本阶段。仅在无输入焦点且当前决策允许时生效（对 `Text`/`Entry` 焦点做判断）。
- 验证：逐键手动验证；确认快捷键提示与实际行为一致。

### 任务 3.5 收尾自检
- 逐条对照规格 §9 验收标准手动过一遍（含 `1024×700` 极窄窗、窗口拉大卡牌升档）。
- 截图归档：`data/_ui_final_wide.png`、`data/_ui_final_narrow.png`。

---

## 交付物

- 代码：`theme.py`（改）、`card_widget.py`（改）、`images.py`（改）、`app.py`（瘦身）、新增 `layout.py` / `board.py` / `log_panel.py` / `dialogs.py`。
- 文档：本计划 + 规格。
- 验收证据：各阶段截图。

## 风险与回滚

- 拆分 `app.py` 是最大风险点：按任务 1.5 → 1.6 → 1.7 顺序小步迁移，每步保持 `import mtg.ui.app` 通过 + 截图正常，失败即可回退该步。
- 尺寸档 × 图片缓存可能吃内存：1.3 只加"按档取图"的能力、默认仍用 M 档；1.4 的 LRU 上限先落地，1.7 才真正按窗口切档，避免多档同时涌入缓存。
- 引擎不改动，故对局逻辑风险为零。