# r1 視覺設計與色彩系統（visual）

- **範圍**：用 Playwright 測 `docs/`：1280×900 與 390×844，淺色與深色，紅漲綠跌與綠漲紅跌，14 個分頁加上抽屜。
- **方法**：
  - 對比度依 computed style 計算，背景向上合成到第一個不透明層。
  - 色盲用 Machado 2009 矩陣（線性 RGB）。截圖套 SVG `feColorMatrix`，token 之間的差距用 CIEDE2000（ΔE，依序為正常／deutan／protan）。
- **版本**：基線在 662c814 上跑。審查期間 checkout 更新到 087aaf2，提案預覽在新版上跑。style.css 與 index.html 沒有變，app.js 有 +66／−18 行，下文的**行號都以 087aaf2 為準**。
- **證據**（S＝`/tmp/claude-0/-home-user-Stock-New/6f855783-f89b-564b-bdfb-2f7f4ef27d28/scratchpad`）：
  - 腳本：`S/tok.js`、`S/tok2.js`、`S/audit.js`。預覽 CSS：`S/preview.css`。
  - 數據：`S/shots/audit.json`、`S/shots/pv-audit.json`。
  - 拼圖：`S/shots/m1-desktop.png`（首屏）、`m2-phone.png`、`m3-charts.png`、`m4-cvd-chart.png`、`m5-cvd-ui.png`、`m6-full.png`、**`m7-preview.png`（套用提案後）**。
- **限制**：這台 Linux 的中文字型是文泉驛，不是 PingFang 或正黑體，截圖的字形與字寬會和實際不同。

## 1. 一句話結論
token 的骨架是對的：漲跌色有間接層，風險色也獨立了。但「一色一義」在元件層被打破了：
- 紅綠被借去表示加減碼、分數、評等、資料來源成功與否和 +1σ 線。
- 琥珀同時是警示、風險 2、官方旗標與證據 C／D。
- 主色藍同時是連結和 483 個標籤。
- 淺色模式「跌」的綠色只有 4.07:1。

改用第 4 節的六族語意 token 並配合元件規則後，實測 14 頁、淺深兩色共 12,990 個文字元素，**0 個未達 AA**。色盲下也不再有只靠色相才能分辨的資訊（`m7-preview.png`）。

## 2. 現況問題（附證據）

### 2.1 對比：淺色有 1,423 個文字元素未達 AA，深色 259 個
| 級 | 元素 | 色彩 | 對比 | 數量 |
|---|---|---|---|---|
| P0 | `.num.down`（台股的跌） | `#0f8f6b`／白（放在 bg 上 3.73） | **4.07** | **937** |
| P0 | `.pill.s-2`、`.pill.s2` 白字 | 白／`#0f8f6b`；深色白／`#e25555` | 4.07、3.71 | 40 |
| P1（潛在） | `.pill.rk3`「警戒」 | 深色白／`#c79bf2` | **2.23** | 這週 0 |
| P1 | `.chip.add`、`.chip.cut`、`.pill.s±1` | soft 底＋漲跌字 | 3.56–3.88（深色 3.96–4.12） | 219 |
| P1 | `.chip.warn`、`.pill.rk1` | `#9a6400`／`#fdf1d6`、`#6b7280`／`#eef0f3` | 4.46、4.23 | 36 |
| P1 | `--ink-3` 放在 surface-2 與 bg 上（含頁尾） | `#6b7482` | 4.48、4.32 | 約 60 |
| P1 | 表單邊框（1.4.11） | `--line #dde2ea`／白 | **1.30** | 全站 |

### 2.2 一色多義、一義多色
- **紅綠被用在非價格的地方（違反限制 2、3）：**
  1. `.chip.add`／`.chip.cut`（外資買超前 15、名人基金加碼…）。`.chip.exit` 只顯示代號（STZ），「出清」只靠綠色表達，違反限制 4。
  2. 因子分數與基金評等套 `.pill.s2`～`s-2`（`app.js:35`、`:520`）。實心紅底白字的「▲ 強勢偏多」就是限制 3 禁止的徽章。市場週期「多頭回檔」也用 `s-1`。
  3. 選股卡的分數數字（綜合 +0.75、籌碼 +0.60）與 meter（`:39`）是紅色（m5）。
  4. 理由是紅 ▲、風險是綠 ▼；「要先留意的警報」也用 `li.con`（`:756`），所以警報是**綠色 ▼**。
  5. `.dot.ok = --down`（`style.css:250`）。切到綠漲紅跌後，85 個「✓ 成功」變**紅色**。
  6. 停損用 `--down`，「+1σ 上緣」用 `--up`（`:317`）。+1σ 是 K 線圖上最飽和的紅色標籤，看起來像目標價（m3）。
  7. 19 張領先指標卡的 sparkline 都上紅綠，**C 級也一樣**（例如高收益債利差 C 是紅色），違反 11-14b「C、D 不給方向色」的決議。
- **琥珀有 10 種意思**（`--warn` 與 `--rk2` 的 ΔE：淺色 5.0，深色 0）：
  - 警示、風險 2、官方旗標（注意、處置、除權息）。
  - **證據 C、D**：`:694` 也用 `.chip.flag`，和「注意股」完全同款。
  - 自選與精選標記（`:766`）、★、「操作參考」、百分位條（`:1354`）。
  - `.alert.lv2` 的外框，但同一則警報的徽章是紫色，形成一義兩色。
- **主色藍有約 10 種意思**（19 個 selector）：
  - 連結、焦點與選取。
  - 483 個 `.chip.mk` 標籤：市場別、群組、**證據 A、B**、AI 撰寫、壓力與支撐。
  - `.fn-n`、`.ai`、`.spot`、台股散布點、品牌字。
  - 可點的 55 個 `button.chip` 和 293 個不可點的 chip 長得一樣。
- **相近色撞義**：
  - `--ma1` 同時是 MA20、支撐、買權與融資線，和風險 0 的 ΔE 是 7.3。
  - MA120 和風險 3 的 ΔE 是 9.0。
  - 風險 1 和 ink-3 的 ΔE 只有 **1.2**，「中等風險」看起來像停用的灰字。
  - 籌碼圖參考線寫死成 `#888`（`:431`）。

### 2.3 色盲（m4、m5）
- **風險 0 與 3**：淺色 26.2 → **8.1**／10.1，深色 21.5 → **3.6**／6.7；徽章底色 12.3 → **1.7**。風險條的頭尾兩段都變成藍色。四級的 L\* 是 45／48／46／42，順序沒有用明度表達。
- **MA20 與 MA120**：5.6／8.1，深色 protan 只有 **1.8**。MA60 與漲紅在 deutan 下是 9.3。
- **紅綠**：16.9／14.9。數字都帶 ± 或 ▲▼，這部分做得好，但 K 棒只靠顏色。

### 2.4 字體與層級
- CSS 有 **18 種字級**（11–22px，含半像素）。
- **12px 的中文標籤約 1,600 個**。
- **301 個 Plex Mono 假粗體**：只載入 400 和 500，`b.num` 卻是 700。
- 可點的 chip 高 21px，★ 17px。
- 總覽首屏的第一張卡「本週變化」本週沒有內容；所有卡片都是同樣的白卡、細線、小字，看不出主次。
- 精選頁在 1280×900 的首屏**看不到任何選股卡**。
- 手機的頁首占首屏 30%，第 5 個分類位於 372–466px，超出畫面。

## 3. 提案（依優先序；色值見第 4 節）

### visual-1 漲跌色只用於價格與實際成交（P0・M）
- **為什麼**：散戶把紅色讀成「漲、該買」。弱訊號被畫成紅色徽章和紅色分數，就會被誤讀成強烈看多。
- **改法**：
  - 分數與評等：`.pill.s2,.pill.s1,.pill.s0,.pill.s-1,.pill.s-2{background:var(--surface-2);color:var(--ink-2);border-color:var(--line)}`。`app.js:33` 的 ARROW 改成 `{2:"▲▲",1:"▲",0:"■","-1":"▼","-2":"▼▼"}`，強度用符號表達。
  - 加減碼標籤：`.chip.add,.chip.new,.chip.cut,.chip.exit{background:var(--surface);color:var(--ink-2);border-color:var(--line-strong)}`，`.chip.add::before{content:"+"}`，`.chip.cut::before{content:"−"}`。exit 的文字改成「出清 STZ」。
  - 分數數字不再套 up／down class，meter（`:39`）改用 `var(--score)`。
  - `.why li.pro::before{content:"+";color:var(--ink-3)}`；`li.con` 與警報清單改成 `content:"!";color:var(--warn)`。
  - `.dot.ok{color:var(--ink-2)}`，`.dot.bad{color:var(--warn)}`。
  - 領先指標卡的 `spark()` 改用 `stroke:var(--ink-2)`。
- **影響範圍**：CSS 約 15 行，JS 改 6 處（33、35、39、131、520、756）。
- **優先級與成本**：P0・M

### visual-2 文字全面過 AA（P0・S）
- **為什麼**：937 個「跌」數字只有 4.07:1，在戶外、在手機上、對年長散戶都更難讀。
- **改法**：
  - 換掉 `--rise`、`--fall`、`--ink-3`、`--warn`、`--warn-soft`、`--surface-2` 的值。
  - 刪除 `--rise-soft`、`--fall-soft`，以及所有白字配漲跌底色的組合。
  - 把 395–397 行的補丁併回 `:root`。
- **新紅綠的取捨**：色盲下的 ΔE，淺色從 16.9 提高到 **24.5**，深色從 17.2 提高到 **29.5**。若覺得 `#007d68` 偏青，可改用 `#007e5c`（deutan 下 19.8）。
- **優先級與成本**：P0・S

### visual-3 琥珀只表示警示；官方旗標與證據等級另立樣式（P0・S）
- **為什麼**：「證據 C・無證據」和「注意股」長得一樣，散戶會把「沒有證據」讀成「交易所警示」。
- **改法**：
  - 警示：`.chip.warn::before{content:"!";font-weight:700;margin-right:3px}`；`.alert.lv2{border-color:var(--rk3)}`。
  - 官方旗標：`.chip.flag{background:var(--ink);color:var(--surface);border-color:transparent;border-radius:3px}`（反白印章）。
  - 證據等級：`:694` 改成 `"chip ev ev-"+e.grade`。
    - `.chip.ev{background:transparent;border:1px solid var(--ink-3);color:var(--ink-2)}`
    - `.ev-A{border-color:var(--ink-2);color:var(--ink)}`
    - `.ev-C{border-style:dashed;color:var(--ink-3)}`
    - `.ev-D{border-style:dotted;color:var(--ink-3)}`
  - 星號 `.star[aria-pressed="true"]{color:var(--accent)}`。`:766` 的自選與精選、「操作參考」、`:1354` 的百分位條都改成中性。
- **優先級與成本**：P0・S

### visual-4 風險改成有序的紫色明度階（P1・S）
- **為什麼**：紅綠色盲看「低風險」和「警戒」是同一個顏色。
- **改法**：
  - `--rk0..3` 改為填色階，L\* 依序 93／85／72／36。
  - `.pill.rk0{background:transparent;border-color:var(--rk-line);color:var(--ink-2)}`
  - `.pill.rk1{background:var(--rk1);color:var(--rk-ink)}`，rk2 同樣寫法。
  - `.pill.rk3{background:var(--rk3);color:var(--rk-on)}`
  - `.riskbar{opacity:1;box-shadow:inset 0 0 0 1px var(--rk-line)}`
  - 徽章一定附等級字。
- **驗證**：相鄰兩級在三種視覺下的 ΔE 都 ≥ 7.6。
- **優先級與成本**：P1・S

### visual-5 主色只表示「可以點」；chip 分三型（P1・S）
- **改法**：
  - 標籤：`.chip.mk{background:var(--surface-2);color:var(--ink-2);border-color:var(--line)}`。
  - 可點：`button.chip,a.chip{border-color:var(--line-strong);color:var(--ink);min-height:24px;display:inline-flex;align-items:center}`，`:hover` 換成 accent。
  - 其他去掉主色的地方：
    - `.fn-n{background:transparent;color:var(--ink);box-shadow:inset 0 0 0 1.5px var(--ink-2)}`
    - `.ai{border-left-color:var(--line-strong)}`
    - `.ladder .spot{color:var(--ink-2)}`
    - `.sc-dot.tw{fill:var(--ink-2)}`，`.sc-dot.us{fill:var(--surface);stroke:var(--ink-2)}`
    - `.brand span{color:inherit}`
  - 表單：`select,input,.seg{border-color:var(--line-strong)}`，對比從 1.30 提高到 3.35。
- **優先級與成本**：P1・S

### visual-6 圖表（P1・S）
- MA20、MA60、MA120 改用 `--ma1..3`（藍、褐、石板），MA120 用 `lineStyle:2`，圖例用 `.key.dash`。三線最小 ΔE 是 19.4，原本是 5.6 與 1.8。
- `app.js:317` 改成 `[["support","支撐","--pl-support"],["stop","停損","--pl-stop"],["target","+1σ","--band"]]`，+1σ 用 `lineStyle:1`，不再用漲跌色。
- `:431` 的 `"#888"` 改成 `css("--band")`。
- 選配（P2）：空心陽線 `upColor:"transparent"`，給色盲使用者一個形狀線索。

### visual-7 字級 6 階、中文至少 13px、粗體數字用真字重（P1・M）
- 字級：`--fs-1:12px`（只給等寬數字）、`--fs-2:13px`（chip、pill、`th`、`dt`、`.k`）、`--fs-3:14px`（表格）、`--fs-4:15px`（內文，手機也是 15）、`--fs-5:17px`（卡片標題與價格）、`--fs-6:20px`（h2、統計值）。舊值依最近的一階對應。
- index.html 改成 `IBM+Plex+Mono:wght@400;500;600`，加上 `.num{font-synthesis:none}`、`b.num,.num b,.fn-n{font-weight:600}`。
- 影響：表格每列高約多 1px。

### visual-8 首屏層級（P1・M，和 IA 隊協調）
- 「本週變化」沒有資料時收成一行 ink-3 字。
- 首屏的結論卡用 `.panel.key{border-color:var(--line-strong)}`，h3 17px；內層的 `.fn`、`.lead-card` 拿掉外框，只留 surface-2 底。
- 精選頁：組合風險壓成一條 `.stats`，預算表收進 `<details>`，讓第一張選股卡進入首屏。
- 手機頁首只留資料日。

### visual-9 對比回歸檢查（P2・S）
- 在 pipeline 用 Python 解析 style.css 的淺深 token，依第 4 節的配對表計算對比，未達門檻就讓建置失敗。
- 用 grep 限制 `var(--up)`、`var(--down)` 只能出現在 `.up`、`.down`、K 棒與 flow 圖。

## 4. 色票與元件規格
| token | 淺色 | 深色 | 用途 |
|---|---|---|---|
| `--bg` | #f3f5f8 | #0e1117 | 頁面、抽屜底 |
| `--surface` | #ffffff | #161a22 | 卡片、面板 |
| `--surface-2` | #f7f8fa | #1b2029 | 內層卡、中性 chip 底 |
| `--line` ／ `--line-soft` | #dde2ea ／ #ebeef3 | #2a313d ／ #212731 | 純裝飾分隔 |
| `--line-strong` | #848d9b | #626c7c | 表單與可點 chip 邊框（≥3:1） |
| `--ink` ／ `--ink-2` ／ `--ink-3` | #131a27 ／ #4c5768 ／ #676f7d | #e9ecf1 ／ #aab3c0 ／ #8d97a6 | 主文、次文、說明；`--score`、`--pl-support` 用 ink-2，`--band` 用 ink-3 |
| `--accent` ／ `--accent-soft` | #1d4fcf ／ #e8eefc | #7fa4ff ／ #1c2740 | 只用於連結、目前位置、焦點、hover |
| `--rise` ／ `--fall` | #cc3924 ／ #007d68 | #ef6447 ／ #2fb3a3 | 調色盤 |
| `--up` ／ `--down` | 台股：rise／fall；美股：fall／rise | 同左 | 只給價格與成交 |
| `--warn` ／ `--warn-soft` | #986300 ／ #fdf3dc | #e0a83a ／ #33290f | 警示（一定加「!」）；`--pl-stop` 也用 warn |
| `--rk0..3` | #ece8fc ／ #d9cff9 ／ #b9a6f2 ／ #5b36c9 | #221d33 ／ #2f2652 ／ #4a3a8c ／ #b9a7ff | 風險 0–3 填色 |
| `--rk-ink` ／ `--rk-on` ／ `--rk-line` | #3a2287 ／ #ffffff ／ #8f7fe0 | #e0d8ff ／ #17122c ／ #7d6fd0 | 風險字、rk3 上的字、rk0 外框 |
| 證據 A／B／C／D | 實框 ink-2 配 ink 字／ink-3 框配 ink-2 字／虛線配 ink-3／點線配 ink-3 | 同左 | 不帶色相 |
| 官方旗標 | 底 ink、字 surface | 同左 | 處置、注意、除權息 |
| `--ma1..3` | #2563c9 ／ #8a7356 ／ #3d4656 | #6ea8ff ／ #b39b7a ／ #c3cad4 | MA20／60／120（實線／實線／虛線） |
| K 棒 | `--up`／`--down` | 同左 | 唯一使用彩色填滿的主體 |

**對比實測**（淺｜深，全部達標）：

| 組合 | 對比 | 組合 | 對比 |
|---|---|---|---|
| ink-3／bg | 4.64｜6.40 | ink-3／surface-2 | 4.77｜5.53 |
| rise／bg | 4.60｜5.92 | rise／surface-2 | 4.73｜5.12 |
| fall／bg | 4.65｜7.29 | fall／surface-2 | 4.78｜6.30 |
| warn／warn-soft | 4.62｜6.71 | accent／accent-soft | 5.88｜6.12 |
| rk-ink／rk2 | 5.57｜6.78 | rk-on／rk3 | 7.46｜8.66 |
| 官方旗標 | 17.43｜14.72 | line-strong／bg | 3.07｜3.56 |
| rk-line／surface | 3.34｜4.20 | MA 三線／surface | ≥4.50｜≥6.54 |

**元件規則**：
1. **價格色**只用於實際漲跌與成交（漲跌幅、K 棒、量、法人買賣超的金額或張數），一定帶 ± 或 ▲▼，**不做背景填色**。
2. **模型輸出**（分數、評等、預測、+1σ）一律用墨色。方向用位置和符號表達，強度用 ▲▲／▲。
3. **形狀分工**：
   - pill（圓角 999px）：等級與狀態
   - chip（6px）：標籤
   - flag（3px 反白）：官方旗標
   - ev（外框）：證據等級
   - 可點元件：高至少 24px，用 `--line-strong` 框
4. **rk3 是全站唯一的深色實心徽章**。accent 永遠不做實心填色，因為 rk3 和 accent 在 deutan 下的 ΔE 只有 1.1，只能靠形狀區分。
5. **琥珀和漲紅**在 deutan 下的 ΔE 只有 3.3，所以警示一定要加「!」。
6. **禁止寫死色值**，圖表顏色一律用 `css("--token")`。中文至少 13px。

## 5. 待辦 UI 項目的意見（建議順序）
1. **product-7 可及性：先做。** 對比問題用 visual-2 和 visual-5 就能解決（預覽實測 0 個不合格）。焦點鎖定、skip link、巢狀互動交給 a11y 隊；可點 chip 一併提高到 24px。
2. **product-3 手機導覽：做。** 5 個分類現在總寬 450px，比 390px 寬。建議把分類名縮成兩字（總覽、選股、籌碼、配置、知識），每格約 70px，就能全部放進一列；或者改成底部 sticky tab bar（每格 ≥ 48px，加 `env(safe-area-inset-bottom)`）。頁首只留資料日。
3. **product-8 清單模式：做。** 寬度小於 560px 時，表格改成「名稱＋2 個數字」的清單，每列高 ≥ 48px；欄位組用 `.seg` 切換。
4. **product-9 比較與分享：** 分享篩選網址先做，用 hash 參數即可，成本低。比較功能晚做；2–4 檔的序列色用 `--ma1..3` 加 ink，不可用紅綠。
5. **product-10 PWA：最後做。** `theme-color` 要依淺深兩色分別給（`#ffffff`／`#161a22`）。離線時頁首用 warn 標示「離線・資料日 X」，避免使用者把舊資料當成新資料。
6. **product-6：** 00bca9a 已經產生 `calendar.ics` 與 `feed.xml`，只缺 UI 入口。建議用 visual-5 的可點 chip，放在行事曆卡與「本週變化」的標題列。

## 6. 給其他兩隊的問題
1. （a11y）`.pick` 整張卡可以點，裡面又有 ★ 和連結。你們會把整卡改成 `<a>`，還是只讓標題可點？這會決定卡片的 hover 與 focus 樣式。我建議 focus 用 2px 的 accent outline，hover 只改邊框。
2. （IA）精選頁把組合風險和預算表放在選股卡前面，是刻意的誠實設計嗎？如果是，visual-8 只壓縮它們的視覺份量、不調整順序，你們能接受嗎？
3. （兩隊）法人買賣超的金額或張數照台灣慣例用紅綠，我把它歸為「實際成交」，所以允許使用漲跌色；但「外資買超前 15」這類名次標籤改成中性。這條界線你們同意嗎？
