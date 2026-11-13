/* ================================================================
   界面多语言：中文 / English / 日本語
   ---------------------------------------------------------------
   ★ 机制借鉴「江湖时序」(DeanChensj/jiangshan-map)：
     **中文原文 → 译文字典** 查表 + 独立字典文件 + 顶栏切换器。
     本文件只做「界面框架文案」的翻译（页签/按钮/口径条/页脚/状态提示），
     **不翻元数据**：文献题名、图注、标签、地区名、分类名、原文正文一律保持中文。

   ★ 落地方式 = DOM 查表，不改业务代码：
     ① 整段精确命中 → 直接替换；
     ② 子串命中（长度 ≥ 2 的键，按长度降序，先长后短，
        避免「文献」先吃掉「文献总数」）→ 就地替换。
   ★ 数据容器一律跳过（SKIP 选择器）：里面的中文是**数据**，误翻就是数据损坏。
   ★ 切回中文时按每个节点缓存的原文还原，所以「中 → 英 → 中」无损。
   ================================================================ */
(function (global) {
  "use strict";

  var LS_KEY = "lzkg.lang";
  var HTML_LANG = { zh: "zh-CN", en: "en", ja: "ja" };

  /* ---- 词典：键 = 中文原文，值 = [英文, 日语] ---- */
  var ENTRIES = {
    /* ── 顶栏 · 页签 ── */
    "总览": ["Overview", "概要"],
    "文献": ["Literature", "文献"],
    "图版": ["Plates", "図版"],
    "图谱": ["Graph", "グラフ"],
    "统计": ["Statistics", "統計"],
    "工作台": ["Workbench", "ワークベンチ"],

    /* ── 顶栏 · 状态行 ── */
    "数据更新日期：": ["Data updated: ", "データ更新日："],
    "服务状态：": ["Service status: ", "サービス状態："],
    "检测中…": ["Checking…", "確認中…"],
    "后端正常": ["Backend OK", "バックエンド正常"],
    "后端未启动": ["Backend not running", "バックエンド未起動"],
    "不通": ["Down", "切断"],
    "全部数据由本机后端 ": ["All data is served by the local backend ", "すべてのデータはローカルバックエンド "],
    "提供，同源访问，断网可用": [" — same-origin access, works offline", "提供（同オリジンアクセス、オフラインでも利用可能）"],

    /* ── 顶栏 · 壁纸 / 同步控件 ── */
    "切换壁纸": ["Switch wallpaper", "壁紙を切替"],
    "原版": ["Plain", "標準"],
    "图纸 · 石墨": ["Blueprint · Graphite", "図面 · グラファイト"],
    "古地图 · 宣纸": ["Ancient Map · Xuan Paper", "古地図・宣紙"],
    "宫墙 · 墨绿": ["Palace Wall · Deep Green", "宮壁 · 深緑"],
    "界面语言": ["UI language", "表示言語"],
    "自动同步": ["Auto sync", "自動同期"],
    "立即同步": ["Sync now", "今すぐ同期"],
    "⟳ 立即同步": ["⟳ Sync now", "⟳ 今すぐ同期"],
    "同步：读取中…": ["Sync: reading…", "同期：読み込み中…"],
    "同步：未设置": ["Sync: not configured", "同期：未設定"],
    "同步中…": ["Syncing…", "同期中…"],
    "自动同步：开": ["Auto sync: on", "自動同期：オン"],
    "自动同步：关": ["Auto sync: off", "自動同期：オフ"],
    "还没有可用的同步位置": ["No sync target available yet", "利用可能な同期先がまだありません"],
    "点一下去设置（Web Dev 页面：配自己的网盘，或用零配置的服务器存储）":
      ["Click to set up (Web Dev page: your own cloud drive, or zero-config server storage)",
       "クリックして設定（Web Dev ページ：自分のクラウドドライブ、またはゼロコンフィグのサーバー保存）"],
    "目标：": ["Target: ", "対象："],
    "立即同步一次：先下载远端合并到本地，再上传（目标：":
      ["Sync once now: download the remote copy, merge it locally, then upload (target: ",
       "今すぐ一度同期：リモートを取得してローカルに統合してからアップロード（対象："],

      "宁波地域文明探源工程 · 良渚文化文献目录":
      ["Ningbo Regional Civilisation Origins Project · Liangzhu Culture Literature Catalogue",
       "寧波地域文明探源プロジェクト · 良渚文化文献目録"],
    "请先登录（工作台 → 登录）": ["Please sign in first (Workbench → Sign in)", "先にログインしてください（ワークベンチ → ログイン）"],
    "。说明：": [". Note: ", "。注記："],
    "读取，图谱渲染用 vis-network 由后端内联提供，无外网依赖。":
      ["; the graph is rendered by the vis-network runtime inlined by the backend, with no external dependency.",
       "。グラフはバックエンドがインラインで提供する vis-network ランタイムで描画され、外部依存はありません。"],
    "文献检索": ["Literature search", "文献検索"],
    "数据管理": ["Data management", "データ管理"],
    "⬇ 导出 xlsx": ["⬇ Export xlsx", "⬇ xlsx を書き出し"],
    "关键词与类别同时生效（AND）；关键词留空即按类别浏览。":
      ["Keyword and category filters combine (AND); leave the keyword empty to browse by category.",
       "キーワードとカテゴリは AND で併用されます。キーワードを空にするとカテゴリで閲覧できます。"],
    "条（/api/overview）": [" items (/api/overview)", "件（/api/overview）"],
    "条（关键词 AND 类别，后端 total）": [" items (keyword AND category, backend total)", "件（キーワード AND カテゴリ、バックエンド total）"],
    " 条": [" items", "件"],
    " 篇": [" records", "件"],
    " 个": ["", "個"],
    "第 ": ["Page ", "ページ "],
    " 页 / 每页 ": [" / per page ", " / 1ページあたり "],
    "子串 ": ["substring ", "部分一致 "],
    "词级 ": ["token ", "単語 "],
    "同义 ": ["synonym ", "類義語 "],
    "库内": ["In library", "庫内"],
    "当前命中": ["hits", "ヒット"],
    "分词精确（不含同义）": ["Token-exact (no synonyms)", "分割一致（同義語なし）"],
    "LIKE 兜底（查询词太短，trigram 用不上）": ["LIKE fallback (the query is too short for trigram)", "LIKE フォールバック（trigram が使えないほど短い）"],
    "FTS5 索引": ["FTS5 index", "FTS5 インデックス"],
    "序号": ["No.", "No."],
    "题名 / 作者 / 标签": ["Title / Author / Tags", "タイトル／著者／タグ"],
    "年份": ["Year", "年"],
    "类型 / 语种": ["Type / Language", "種別／言語"],
    "复核状态": ["Review status", "復核状態"],

    "覆盖 ": ["covering ", "収録 "],
    "复核状态只作显示，不参与检索过滤与排序。": ["Review status is shown only; it takes no part in search filtering or sorting.", "復核状態は表示のみ。検索の絞り込み・並び替えには使われません。"],
    "关键词（空格分隔；多个词按 AND）": ["Keywords (space separated; several words are combined with AND)", "キーワード（スペース区切り・複数の語は AND で結合）"],
    "检索": ["Search", "検索"],
    "图像类型": ["Image type", "画像種別"],
    "全部类型": ["All types", "すべての種別"],
    "对象类型": ["Object type", "対象種別"],
    "全部对象类型": ["All object types", "すべての対象種別"],
    "维度": ["Dimension", "ディメンション"],
    "不限维度": ["Any dimension", "ディメンション指定なし"],
    "不限标签": ["Any tag", "タグ指定なし"],
    "结果视图": ["Result view", "表示形式"],
    "网格": ["Grid", "グリッド"],
    "列表": ["List", "リスト"],
    "正在检索图版…": ["Searching plates…", "図版を検索中…"],
    "正在读取统计…": ["Loading statistics…", "統計を読み込み中…"],
    "正在构建图谱…": ["Building the graph…", "グラフを構築中…"],
    "边列表": ["Edge list", "エッジ一覧"],
    "图谱分析指标": ["Graph analysis metrics", "グラフ分析指標"],
    "分析指标": ["Metrics", "分析指標"],
    "复位视图": ["Reset view", "ビューをリセット"],
    "应用并绘图": ["Apply and draw", "適用して描画"],
    "最小度数": ["Min degree", "最小次数"],
    "关键词": ["Keyword", "キーワード"],
    "聚焦节点": ["Focus node", "注目ノード"],
    "跳数": ["Hops", "ホップ数"],
    "上限": ["Limit", "上限"],
    "点击「分析指标」加载度中心性 / 介数 / 社区（Louvain，seed 固定）。": ["Click “Metrics” to load degree centrality / betweenness / communities (Louvain, fixed seed).", "「分析指標」をクリックすると次数中心性・媒介中心性・コミュニティ（Louvain、seed 固定）が読み込まれます。"],
    "点击图中的边或左侧边列表的一行，这里显示该关系的原始证据与「打开文献」入口。": ["Click an edge in the graph or a row in the edge list to see the raw evidence for that relation and a link to the record.", "グラフのエッジか左のエッジ一覧の行をクリックすると、その関係の生の証拠と文献への入口が表示されます。"],
    "打开文献": ["open the record", "文献を開く"],
    "证据": ["Evidence", "証拠"],
    "入口页：每个功能点进去是独立界面。": ["Entry page: every function opens its own screen.", "エントリーページ：各機能を開くと独立した画面になります。"],
    "登录 / 账号": ["Sign in / account", "ログイン／アカウント"],
    "我的收藏": ["My favourites", "お気に入り"],
    "提案与审核": ["Proposals and review", "提案と審査"],
    "MinerU 解析与录入": ["MinerU parsing and entry", "MinerU 解析と登録"],
    "AI 问答": ["AI Q&A", "AI 问答"],
    "导入历史": ["Import history", "インポート履歴"],
    "Web Dev（浏览数据与同步）": ["Web Dev (browsing data and sync)", "Web Dev（閲覧データと同期）"],
    "协作交换（XML）": ["Collaboration exchange (XML)", "協働交換（XML）"],
    "分词实验室与词典": ["Token lab and dictionary", "トークン実験室と辞書"],
    "数据包（图片 / 原文）": ["Data package (images / full text)", "データパッケージ（画像／原文）"],
    "权限：": ["Access: ", "権限："],
    "进入 →": ["Enter →", "進む →"],
    "状态读取中…": ["Reading status…", "状態を読み込み中…"],
    "已登录：": ["Signed in: ", "ログイン中："],
    "未登录（可在本机免登录模式下直接用）": ["Not signed in (on this machine it can be used without signing in)", "未ログイン（本機では免ログインでも利用できます）"],
    "登录、退出，查看当前身份与角色。": ["Sign in, sign out, and check the current identity and role.", "ログイン・ログアウトと、現在の利用者と権限の確認。"],
    "查看收藏的文献，点进去或取消收藏。": ["Browse your favourite records, open them, or unfavourite them.", "お気に入りの文献を見て、開き、外せます。"],
    "逐条批准/驳回待审提案（导入与录入产生的提案都在这里）。": ["Approve or reject pending proposals one by one (both imported and manually entered records land here).", "保留中の提案を1件ずつ承認・却下します（インポート分も直接入力分もここに集まります）。"],
    "上传 PDF 解析成 Markdown＋图版，填题录后作为提案提交入库。": ["Upload a PDF, parse it into Markdown plus plates, fill in the bibliographic fields, then submit it as a proposal.", "PDF をアップロードして Markdown と図版に解析し、書誌情報を記入して提案として送信します。"],
    "用自然语言问本库；接口配置存在各自浏览器里，不填则降级为本地检索摘要。": ["Ask this library in natural language; the endpoint is configured in your own browser — without one, answers fall back to local search summaries.", "自然言語で本庫に質問できます。接続先は各人のブラウザに保存され、未設定の場合はローカルの検索要約にフォールバックします。"],
    "历次登记表导入的批次、时间与写入条数。": ["The batches, times and record counts written by past registry imports.", "これまでの登记表インポートのバッチ・時刻・書込件数。"],
    "浏览记录存在自己浏览器里（各存各的，本站不保存），可同步到自己的 WebDAV 网盘（坚果云等），换设备也能带走。": ["Browsing history stays in your own browser (each person keeps their own; this site stores nothing), and can be synced to your own WebDAV drive (Nutstore and the like) so it travels with you.", "閲覧記録は各人のブラウザに保存されます（このサイトには保存しません）。自分の WebDAV（Nutstore など）へ同期すれば、別の端末にも引き継げます。"],
    "把题录与图版标注导出成 XML，成员改完回传；回传件与 xlsx 走同一条「预检→批准→入库」链路。": ["Export the bibliographic records and plate annotations as XML for members to edit and return; returned files follow the same pre-check → approve → import path as xlsx.", "書誌情報と図版注釈を XML に書き出し、メンバーが編集して返送できます。返送分は xlsx と同じ「事前チェック→承認→取込」の経路を通ります。"],
    "维护良渚领域词典（词条 / 同义组 / 领域后缀表），发现候选新词并人工勾选采纳，重建词级索引。": ["Maintain the Liangzhu domain dictionary (entries / synonym groups / domain suffix list), discover candidate words, tick the ones to adopt, and rebuild the token index.", "良渚分野の辞書（語条例・同義語グループ・分野接尾辞表）を管理し、候補語を発見して採用を選び、単語インデックスを再構築します。"],
    "← 返回文献列表": ["← Back to the literature list", "← 文献一覧へ戻る"],
    "★ 已收藏": ["★ Favourited", "★ お気に入り済み"],
    "当前文献编号：": ["Record ID: ", "文献番号："],
    "基本信息": ["Basic information", "基本情報"],
    "作者": ["Author", "著者"],
    "文献类型": ["Document type", "文書種別"],
    "本组主题": ["Subject group", "主題グループ"],
    "载体题名": ["Host title", "掲載誌名"],
    "出版者/机构": ["Publisher / institution", "出版者・機関"],
    "页码范围": ["Page range", "ページ範囲"],
    "CLC 分类号": ["CLC classification", "CLC 分類番号"],
    "汉语主题词": ["Chinese subject terms", "中国語主題詞"],
    "著录人": ["Catalogued by", "目録作成者"],
    "著录日期": ["Catalogued on", "目録作成日"],
    "审核状态": ["Review status", "審査状態"],
    "著录备注": ["Cataloguing notes", "目録注記"],
    "摘要": ["Abstract", "要旨"],
    "关键词（原文）": ["Keywords (original)", "キーワード（原文）"],
    "四维标签": ["Four-dimensional tags", "四次元タグ"],
    "时间分期": ["Period", "時期"],
    "空间分区": ["Spatial division", "空間分区"],
    "主题分类": ["Topic class", "主題分類"],
    "文化谱系": ["Cultural lineage", "文化系譜"],
    "登记表原文：": ["Registry text: ", "登记表原文："],
    "地图标记": ["Map markers", "地図マーカー"],
    "底图": ["Base map", "背景地図"],
    "矢量": ["Vector", "ベクター"],
    "精度：": ["Precision: ", "精度："],
    "坐标：": ["Coordinates: ", "座標："],
    "出处：": ["Source: ", "典拠："],
    "命中依据：": ["Match basis: ", "命中根拠："],
    "去哪核实": ["Where to verify", "確認先"],
    "图版信息": ["Plate information", "図版情報"],
    "相似文献": ["Similar records", "類似文献"],
    "使用条件以「开放类型」为准；标注为机构订阅/限内部使用的条目不得外传。": ["Usage terms follow the “open type”; items marked institutional-subscription or internal-use-only must not be shared outside.", "利用条件は「公開区分」に従います。機関購読・社内限定の項目は外部に提供できません。"],
    "坐标以文献为准（WGS84），不做推测填充；精度分四档：": ["Coordinates follow the records (WGS84) and are never guessed; precision has four tiers:", "座標は文献に従い（WGS84）、推測で補完はしません。精度は4段階です："],
    "文献坐标由文献给出（本篇／他篇）": ["Literature coordinate — given by a record (this one / another)", "文献座標 — 文献が示す（本文献／他文献）"],
    "公开权威坐标公开资料": ["Published authoritative coordinate — published source", "公開権威座標 — 公開資料"],
    "分区代表点区块统一坐标，约 ±1.1 km": ["Division representative point — block-wide coordinate, about ±1.1 km", "分区代表点 — ブロック共通座標、約 ±1.1 km"],
    "未定位没有可定位坐标": ["Not located — no locatable coordinate", "未定位 — 落下可能な座標なし"],

    "标签": ["Tags", "タグ"],
    "语种": ["Language", "言語"],
    "本地检索摘要": ["local search summary", "ローカル検索要約"],
    "未配置接口（提问将降级为本地检索摘要）": ["No endpoint configured (questions fall back to a local search summary)", "接続先未設定（質問はローカル検索要約にフォールバックします）"],
    "看图版图片、原文 PDF 与 Markdown 有没有就位。没放数据包时，图版只剩占位图、原文读不了。": ["Check whether the plate images, the original PDFs and the Markdown are in place. Without the data package the plates are placeholders and the full text cannot be opened.", "図版画像・原文 PDF・Markdown が揃っているかを確認します。データパッケージが無いと図版はプレースホルダのまま、原文は読めません。"],
    "坐标标记": ["coordinate markers", "座標マーカー"],
    "共 ": [" ", " "],
    "坐标标记，按精度从高到低排列；最精确的一档是 ": [", ordered from most to least precise; the top tier is ", "、精度の高い順に並べています。最も精密な区分は "],
    "正文/题名/关键词/刊名中出现「": ["occurs in the body / title / keywords / journal name: ", "本文・タイトル・キーワード・誌名に「"],
    "导入批次 ": ["Imported batches ", "インポートバッチ "],
    "批": [" batches", "件"],
    "端点 ": ["Endpoints ", "エンドポイント "],

    "口径：": ["Counting rule: ", "集計基準："],

    "关闭（Esc）": ["Close (Esc)", "閉じる（Esc）"],
    "上一张": ["Previous image", "前の画像"],
    "下一张": ["Next image", "次の画像"],
    "图版大图": ["Plate image", "図版の拡大図"],
    "正在打开原文…": ["Opening the full text…", "原文を開いています…"],
    "原文 PDF": ["Original PDF", "原文 PDF"],
    "原文读取失败：": ["Could not read the full text: ", "原文を読み込めませんでした："],
    "图版未本地化：": ["Plates were not localised: ", "図版がローカル化されていません："],
    "正文 ": ["", "本文 "],
    " 张图版，已本地化 ": [" plate images in the body, ", " 枚の図版、ローカル化済み "],
    " 张未匹配到本地文件（下方已标出，未用别的图顶替）": [" not matched to a local file (listed below; no other image was substituted)", " 枚はローカルファイルと一致せず（下に記しています。別画像で代用はしていません）"],
    "图像登记表": ["Plate registry", "画像登记表"],
    "复核覆盖率：—（后端未返回 review 统计）": ["Review coverage: — (the backend returned no review stats)", "復核カバレッジ：—（バックエンドは review 統計を返していません）"],
    "复核覆盖率：": ["Review coverage: ", "復核カバレッジ："],

    " 张": [" localised", " 枚"],

    "← 返回工作台": ["← Back to the workbench", "← ワークベンチへ戻る"],
    "当前用户：": ["Signed in as: ", "現在のユーザー："],
    "角色：": [" · Role: ", " · 役割："],
    "退出登录": ["Sign out", "ログアウト"],
    "管理员：可见「提案与审核」": ["Admins can see “Proposals and review”", "管理者は「提案と審査」を見られる"],
    "非管理员：不显示「提案与审核」": ["Non-admins do not see “Proposals and review”", "管理者以外は「提案と審査」を表示しない"],
    "这条链路怎么走：① 在下面改词典 → ② 「保存词典」 → ③ 「重建索引」 → 才生效。词典是源数据，索引是派生物；**改完不重建，检索还在用旧词典**。": ["How it works: ① edit the dictionary below → ② “Save the dictionary” → ③ “Rebuild the index” → only then does it take effect. The dictionary is the source data, the index is derived; if you edit without rebuilding, search still uses the old dictionary.", "使い方：① 下の辞書を編集 → ②「辞書を保存」→ ③「インデックスを再構築」→ これで反映されます。辞書が原本でインデックスは派生物です。再構築しないと検索は古い辞書のままです。"],
    "索引状态：": ["Index status: ", "インデックス状態："],
    "　词典 ": [" · Dictionary: ", " · 辞書 "],
    " 词 / ": [" words / ", " 語 / "],
    " 个同义组": [" synonym group(s)", " 個の同義語グループ"],
    "　覆盖 ": [" · covering ", " · 対象 "],
    "放弃改动，重新读取": ["Discard the changes and reload", "変更を破棄して読み直す"],
    "恢复内置初始词典": ["Restore the built-in dictionary", "内蔵の初期辞書を復元"],
    "① 领域词条（": ["① Domain entries (", "① 分野語条例（"],
    "② 同义组（": ["② Synonym groups (", "② 同義語グループ（"],
    " 组）": [" groups)", " グループ）"],
    "词频统一给大，词才会压过通用分词的切分倾向（不然「兽面纹」会被切成「兽面/纹」）。": ["Give domain words a uniformly high frequency so they outweigh the generic tokenizer’s splitting habit (otherwise “兽面纹” gets cut into “兽面/纹”).", "分野語の頻度を，一律に高めにします。そうしないと汎用の分割傾向に負けて「獣面紋」が「獣面/紋」に切られてしまいます。"],
    "删除": ["Delete", "削除"],
    "人工添加": ["Added by hand", "手動追加"],
    "直接添加词条": ["Add an entry directly", "語条を直接追加"],

    "词": ["Word", "語"],
    "分组": ["Group", "グループ"],
    "说明": ["Note", "説明"],
    "操作": ["Actions", "操作"],
    "组名": ["Group name", "グループ名"],
    "成员（逗号分隔）": ["Members (comma separated)", "メンバー（カンマ区切り）"],

    "无需登录": ["No sign-in required", "ログイン不要"],
    "需登录": ["Sign-in required", "ログイン要"],
    "仅管理员可见": ["Admins only", "管理者のみ閲覧可"],
    "需登录（提交提案要审核）": ["Sign-in required (submissions are reviewed)", "ログイン要（提案は承認が必要）"],
    "需登录（导出）；导入预检仅管理员": ["Sign-in required (export); the import pre-check is admin-only", "ログイン要（書き出し）／インポートの事前チェックは管理者のみ"],
    "已就绪 · 源数据在「程序自带副本」": ["Ready · source data in “the bundled copy”", "準備完了・原本データは「プログラム同梱のコピー」にあります"],
    " 批": [" batches", " 件"],
    "词级索引 v": ["Token index v", "単語インデックス v"],
    " 词": [" words", " 語"],

    "添加词条": ["Add an entry", "語条を追加"],
    "同义组用于「Token + synonyms」：组内任一词命中，整组都算命中。系统不会自动生成同义 —— 自动 = 编造。": ["Synonym groups feed “Token + synonyms”: a hit on any member counts as a hit on the whole group. The system never invents synonyms on its own — automatic means made up.", "同義語グループは「Token + synonyms」で使われます。グループ内のどれか一つがヒットすればグループ全体がヒットします。系统在同義語を自動生成することはありません（自動＝作文）。"],
    "添加同义组": ["Add a synonym group", "同義語グループを追加"],
    "③ 领域后缀 / 前缀表（用于新词发现的领域加权）": ["③ Domain suffix / prefix list (weighting for new-word discovery)", "③ 分野接尾辞・接頭辞表（新規語発見の重み付け用）"],
    "命中后缀或前缀的候选词加 3.0 分。加了之后候选榜的 Top10 从「半是垃圾」变成「全是真领域词」。": ["Candidates matching a suffix or prefix get +3.0 points. With that in place the candidate Top10 goes from “half rubbish” to “all real domain words”.", "接尾辞・接頭辞に一致する候補に 3.0 点を加算します。これを入れると候補上位 10 件が「半分がゴミ」から「全部が本物の分野語」に変わります。"],
    "后缀": ["Suffixes", "接尾辞"],
    "前缀": ["Prefixes", "接頭辞"],
    "④ 候选新词（人工勾选才进词典）": ["④ Candidate words (only ticked ones enter the dictionary)", "④ 候補語（チェックしたものが辞書に入ります）"],
    "最小频次": ["Min frequency", "最小頻度"],
    "按得分": ["By score", "スコア順"],
    "按频次": ["By frequency", "頻度順"],

    "收藏检索两层：先题名/备注，再全文": ["Favourite search has two layers: title/note first, then full text", "收藏検索は2層：まず題名・書込、次に全文"],
    "题名/备注": ["Title / note", "題名・書込"],
    "全文关键词": ["Full-text keyword", "全文キーワード"],
    "清空": ["Clear", "クリア"],
    "两层是并且关系：先按题名/备注筛，再在剩下的收藏里做全文检索。检索只在你的收藏里进行，不会翻出没收藏的文献。": ["The two layers are combined with AND: filter by title/note first, then run the full-text search over the remaining favourites. The search never returns records you have not favourited.", "2層は AND 関係：まず題名・書込で絞り、その残った收藏の中で全文検索します。收藏していない文献が検索結果に混ざることはありません。"],
    "本机能力探测": ["Local capability probe", "ローカル能力の探测"],
    "正在探测…": ["Probing…", "探测中…"],
    "解析一个 PDF": ["Parse a PDF", "PDF を1件解析"],
    "解析后端": ["Parsing backend", "解析バックエンド"],
    "本地": ["Local", "ローカル"],
    "云端": ["Cloud", "クラウド"],
    "本地：本机需先跑 mineru-api.exe --host 127.0.0.1 --port 8000（V1 协议，本地无 key）。": ["Local: you must first run mineru-api.exe --host 127.0.0.1 --port 8000 on this machine (V1 protocol, no key locally).", "ローカル：先にこのマシンで mineru-api.exe --host 127.0.0.1 --port 8000 を起動してください（V1 プロトコル、ローカルは key 不要）。"],
    "★ key 只保存在这台机器的浏览器里（localStorage 键名 lzkg.mineru.apikey.v1）：提交解析请求时随请求发给本机后端再转发给 MinerU，后端不落盘、不写日志、不回显。同一台机器上的其他人（公用电脑）能读到它——公用电脑上不要填。": ["★ The key is stored only in the browser on this machine (localStorage key lzkg.mineru.apikey.v1): it is sent with the parse request to the local backend, which forwards it to MinerU. The backend never writes it to disk or a log and never echoes it back. Other people on the same machine (a shared computer) can read it — do not fill it in on a shared computer.", "★ key はこのマシンのブラウザにのみ保存されます（localStorage キー lzkg.mineru.apikey.v1）。解析リクエストと一緒にローカルバックエンドへ送られ、そこから MinerU へ転送されます。バックエンドは保存もログ出力もエコーバックもしません。同じマシンを使う他の人（共用 PC）にも読み取れるため、共用 PC では入力しないでください。"],
    "提问": ["Ask", "質問"],
    "AI 接口设置保存在你的浏览器里": ["The AI endpoint is configured in your own browser", "AI 接続先はあなたのブラウザに保存されます"],
    "协议": ["Protocol", "プロトコル"],
    "OpenAI 兼容（OpenAI / DeepSeek / 通义 / Kimi / one-api 等）": ["OpenAI-compatible (OpenAI / DeepSeek / Tongyi / Kimi / one-api etc.)", "OpenAI 互換（OpenAI / DeepSeek / 通義 / Kimi / one-api など）"],
    "本地 / 私有接口（OpenAI 兼容，key 可空）": ["Local / private endpoint (OpenAI-compatible, key may be empty)", "ローカル / プライベート接続先（OpenAI 互換、key は空でも可）"],
    "接口地址": ["Endpoint", "接続先アドレス"],
    "sk-…（也支持大多数中转站的 sk- 开头）": ["sk-… (most relay services that use an sk- prefix work too)", "sk-…（sk- で始まる中継サービスの大部分も対応）"],
    "这条链路怎么走": ["How the chain works", "この链路の進み方"],
    "① 导出：下面": ["① Export: use the ", "① 書き出し：下の"],
    "② 改：成员拿 XML 改完回传。XML 一条记录一个元素，比 xlsx 好 diff。": ["② Edit: members edit the XML and send it back. One element per record makes the XML much easier to diff than xlsx.", "② 編集：メンバーが XML を編集して返送します。1レコード＝1要素なので xlsx より差分が見やすくなります。"],
    "③ 回传：在「文献 → 导入登记表」里把方式切到「协作交换 XML」→ 预检 → 管理员批准 → 入库。": ["③ Send back: in “Literature → Import registry”, switch the method to “Collaboration exchange XML” → pre-check → admin approval → import.", "③ 返送：「文献 → 登记表をインポート」で方式を「協働交換 XML」に切り替え → 事前チェック → 管理者が承認 → 取り込み。"],
    "原文正文与图版文件不走 XML：正文用「详情 → Read the original」下载，图版在 04_文献图像/ 目录里，XML 里只给文件名与链接。": ["The full text and the plate files do not travel in the XML: download the text from “Detail → Read the original”, the plates live in 04_文献图像/, and the XML only carries file names and links.", "原文本文と図版ファイルは XML には含まれません：本文は「詳細 → Read the original」からダウンロード、図版は 04_文献图像/ にあり、XML はファイル名とリンクだけです。"],
    "范围": ["Scope", "範囲"],
    "全部文献": ["All records", "すべての文献"],
    "仅当前筛选结果（文献页的筛选条件）": ["Only the current filtered result (the filters on the literature page)", "現在の絞り込み結果のみ（文献ページの絞り込み条件）"],
    "⓪ 同步存哪": ["⓪ Where to sync", "⓪ 同期先をどこに保存するか"],
    "● 本站服务器（零配置）": ["● This site’s server (zero config)", "● 本サイトサーバー（ゼロコンフィグ）"],
    "右上角的「同步」按钮按这个顺序挑": ["The “Sync” button in the top-right picks in this order: ", "右上角の「同期」ボタンはこの順で選びます："],
    "配了 WebDAV 就用你的网盘；没配就用本站服务器（零配置，登录即用）；两个都没有才显示「未设置」，点它会跳回本页。": ["if WebDAV is configured it uses your own drive; otherwise it uses this site’s server (zero config, works once signed in); only when neither is set does it show “not configured”, and clicking that brings you back to this page.", "WebDAV を設定済みなら自分のクラウドドライブ、未設定なら本サイトサーバー（ゼロコンフィグ、ログイン即可）を使い、どちらも無いときだけ「未設定」と表示します（押すとこのページに戻ります）。"],
    "换设备时：本站服务器登录即可取回；WebDAV 要重新填一次凭据（凭据只存各自浏览器里）。": ["On another device: sign in to the site’s server to get it back; for WebDAV you must re-enter the credentials once (they live only in each person’s browser).", "別の端末では：本サイトサーバーにログインすれば取り戻せます。WebDAV は資格情報を一度入力し直す必要があります（資格情報は各人のブラウザにのみ保存）。"],
    "本站服务器：已存 ": ["This site’s server: stored ", "本サイトサーバーに保存済み："],
    "立即存到服务器": ["Store on the server now", "今すぐサーバーに保存"],
    "管理员：关闭后所有成员都不能再存到本站服务器（各自浏览器里的记录不受影响）。": ["Admin: once closed, no member can store to this site’s server any more (records already in each person’s browser are unaffected).", "管理者：閉じると全メンバーが本サイトサーバーへ保存できなくなります（各人のブラウザ内の記録は影響を受けません）。"],
    "关闭「存服务器」（对所有成员）": ["Close “store on the server” (for all members)", "「サーバーに保存」を閉じる（全メンバー対象）"],
    "① 我的浏览数据（存在本浏览器里）": ["① My browsing data (kept in this browser)", "① 自分の閲覧データ（このブラウザーに保存）"],
    "源数据是否就位": ["Is the source data in place", "原本データは揃っているか"],
    "对接包里带的是库（题录、图版标注、全文索引），不带文件本体（图版图片、原始 PDF、MinerU 解析出来的 Markdown —— 机构订阅件，不进公开包）。": ["The package carries the database (bibliographic records, plate annotations, the full-text index) but not the files themselves (plate images, original PDFs, the Markdown produced by MinerU — these are institutionally licensed and never enter the public package).", "パッケージにはデータベース（書誌情報・図版注釈・全文索引）が入りますが、ファイル本体（図版画像・原文 PDF・MinerU の解析 Markdown）は入っていません（これらは機関購読物で公開パッケージには入れません）。"],
    "把 1.9 GB 的数据包解压后，把那个文件夹整个拖进程序目录下的「数据包/」即可：不用改配置、不用敲命令，放进去就生效（连重启都不用）。": ["Unpack the 1.9 GB data package and drag the whole folder into 「数据包/」 under the program directory: no config change, no commands — drop it in and it works (no restart needed).", "1.9 GB のデータパッケージを展開し、プログラムディレクトリの「数据包/」へフォルダごとドラッグしてください。設定変更もコマンド入力も不要、置けばそのまま効きます（再起動も不要）。"],
    "源Data source：": ["Source data source: ", "原本データ来源："],
    "就绪 / 应有": ["Ready / expected", "準備 / 想定"],
    "解锁的功能": ["Unlocked features", "解錠される機能"],
    "已就绪": ["Ready", "準備完了"],

    "（题录 + 标签 + 图版标注，不含原文正文与图版二进制）。": [" (bibliographic records + tags + plate annotations; no full text and no plate binaries).", "（書誌情報＋タグ＋図版注釈。原文本文と図版バイナリは含みません）。"],

    "收藏检索": ["Favourite search", "收藏検索"],
    "两层：先": ["Two layers: first ", "2層：まず"],
    "，再全文": [", then the full text", "、次に全文"],
    "两层是": ["The two layers are ", "2層は"],
    "并且": ["combined with AND", "AND で結ばれて"],
    "关系：先按题名/备注筛，再在剩下的收藏里做全文检索。检索": [": filter by title/note first, then run the full-text search over the remaining favourites. The search", "：まず題名・書込で絞り、その残った收藏の中で全文検索します。検索"],
    "只作显示": ["is display only", "は表示のみ"],

  /* ── 页脚 ── */
    "数据来源": ["Data source", "データ出典"],
    "使用条件": ["Terms of use", "利用条件"],
    "免责说明": ["Disclaimer", "免責事項"],
    "数据版本": ["Data version", "データバージョン"],
    "图谱口径": ["Graph notes", "グラフの注記"],
    "中国知网（CNKI）· 中文维普 · 出版商/期刊官网（OA 与开放获取）· 高校机构库 · 公共图书馆数字资源等；每条文献的来源类型与来源判据见「详情 → 去哪核实」。":
      ["CNKI (China National Knowledge Infrastructure) · CQVIP · publisher and journal sites (OA and open access) · university institutional repositories · public library digital collections, etc. The source type and source criterion of each record are listed under “Detail → Where to verify”.",
       "中国知網（CNKI）・中文維普・出版社／期刊サイト（OA・オープンアクセス）・大学の機関リポジトリ・公共図書館のデジタル資料など。各文献の出所タイプと判定根拠は「詳細 → 確認先」に記載。"],
    "以每条文献的「开放类型（open_type）」为准。仅标注为公开的条目可自由引用与转发；标注为机构订阅/限内部使用的文献不得外传，仅供本机核验与著录使用。":
      ["Governed by each record’s “open type” (open_type). Only items marked public may be freely cited and redistributed; items marked institutional-subscription or internal-use-only must not be shared outside, and are here solely for on-machine verification and cataloguing.",
       "各文献の「公開区分（open_type）」に従います。「公開」と明記された項目のみ自由롭게引用・転送でき、「機関購読／社内限定」とされた文献は外部に提供できません（本機での照合と目録作業のみ）。"],
    "本页数据由本机后端从原始登记表与文献附件自动汇整，用于课程作业的著录与检索演示；正式引用请回到原始来源核对。":
      ["This page is assembled automatically by the local backend from the source registry and the document attachments, for cataloguing and search demonstration in a course assignment; for formal citation, verify against the original sources.",
       "本ページのデータは、ローカルバックエンドが原本登记表と文献添付ファイルから自動的に集計したもので、課程課題の目録作成・検索デモ用です。正式な引用は原典で確認してください。"],
    "数据契约 v1.0（2026-10-27 冻结）· 良渚文化文献与图版目录 · 本页所有统计口径均以界面标注为准（总条数 / 当前筛选条数 / 复核覆盖率）。":
      ["Data contract v1.0 (frozen 2026-10-27) · Liangzhu culture literature and plate catalogue · All statistics on this page follow the labels shown in the interface (total records / records under the current filter / review coverage).",
       "データ契約 v1.0（2026-10-27 凍結）・良渚文化の文献・図版目録・本ページの統計口径はすべて画面の表示に従います（総件数／現在の絞り込み件数／復核カバレッジ）。"],
    "图版图像经本机后端 ": ["Plate images are served by the local backend ", "図版画像はローカルバックエンド "],
    "读取。": [".", "で読み込みます。"],
    "使用条件以各条记录的「开放类型/使用条件」为准。站内提供原文通道，不代表获得对外传播授权；标注为机构订阅/限内部使用的条目，请勿外传。":
      ["Usage terms follow the “open type / usage terms” field of each record. The in-site reading channel does not imply any right to redistribute; items marked institutional-subscription or internal-use-only must not be shared outside.",
       "利用条件は各レコードの「公開区分／利用条件」字段に従います。サイト内の原文閲覧は外部への転載権を意味しません。「機関購読／社内限定」の項目は外部に提供しないでください。"],
    "AI 初标只是候选标注，不是考古结论；未复核部分不代表定论。":
      ["AI pre-labelling is only a candidate annotation, not an archaeological conclusion; unreviewed parts do not represent settled findings.",
       "AI の初期ラベル付けは候補注記であり、考古学的結論ではありません。未復核の部分は定説を意味しません。"],
    "图谱是「本数据集可观测到的关联结构」，不等于良渚文化的完整知识体系。":
      ["The graph is “the relational structure observable in this dataset”; it is not the complete knowledge system of Liangzhu culture.",
       "グラフは「このデータセットで観測できる連関構造」であり、良渚文化の完全な知識体系ではありません。"],
    "复核状态按状态字段统计；复核日期非空不代表已复核。":
      ["Review status is counted by the status field; a non-empty review date does not mean the record has been reviewed.",
       "復核状態は状態フィールドを集計したもので、復核日欄が空でないことが復核済みを意味しません。"],

    /* ── 总览页 ── */
    "正在读取总览统计…": ["Loading overview statistics…", "概要統計を読み込み中…"],
    "总览数据加载失败": ["Failed to load overview data", "概要データの読み込みに失敗しました"],
    "数据规模与分布": ["Data scale and distribution", "データ規模と分布"],
    "复核覆盖率": ["Review coverage", "復核カバレッジ"],
    "图版速览": ["Plates at a glance", "図版の早見"],
    "文献类型分布": ["Distribution by document type", "文書種別分布"],
    "语种分布": ["Distribution by language", "言語分布"],
    "出版年份分布（文献出版年，非良渚分期年）":
      ["Publication-year distribution (publication year, not the Liangzhu period)",
       "出版年分布（出版年であり、良渚分期年ではない）"],
    "数据来源类型分布": ["Distribution by source type", "データ出典種別の分布"],
    "开放类型 / 使用条件分布": ["Distribution by access type / usage terms", "公開区分・利用条件の分布"],
    "时间分期标签分布": ["Distribution by period tags", "時期タグの分布"],
    "已通过 / 已复核": ["Passed / verified", "通過・復核済"],
    "待复核": ["Pending review", "復核待ち"],
    "其他 / 未标注": ["Other / unlabelled", "その他・未ラベル"],
    "统计对象：": ["Counted over: ", "集計対象："],
    "暂无数据": ["No data", "データなし"],
    "暂无图版记录": ["No plate records", "図版レコードがありません"],
    "图版速览加载失败：": ["Failed to load the plate preview: ", "図版プレビューの読み込みに失敗しました："],
    "完整图版见每条文献的「详情 → 图版」。": ["See the full plates under each record’s “Detail → Plates”.",
       "図版一覧は各文献の「詳細 → 図版」を参照してください。"],
    "图像登记表 CSV": ["plate registry CSV", "画像登记表 CSV"],
    "下载图像登记表模板（xlsx）": ["Download the plate registry template (xlsx)", "図版登记表テンプレートをダウンロード（xlsx）"],
    "张图像登记表 CSV": [" plate registry CSV", "枚の画像登记表 CSV"],
    "（共 ": ["(total ", "（合計 "],
    " 项，此处显示前 ": [" items; showing the first ", " 件、ここでは先頭 "],
    "项）": [" items)", "件を表示）"],
    "张）": [" plates)", "枚）"],
    "文献总数": ["Total literature", "文献総数"],
    "图像总数": ["Total images", "画像総数"],
    "标签总数": ["Total tags", "タグ総数"],
    "MD 正文字符": ["MD body characters", "MD 本文文字数"],
    "原始 JSON": ["Raw JSON", "生 JSON"],
    "按状态字段统计": ["counted by the status field", "状態フィールド集計"],
    "文献条数": ["Literature records", "文献件数"],
    "图像条数": ["Image records", "画像件数"],
    "标签个数": ["Tag count", "タグ数"],
    "原始 JSON 份数": ["Raw JSON files", "生 JSON ファイル数"],
    "可全文检索文档数": ["Full-text searchable documents", "全文検索可能な文書数"],
    "条": [" items", "件"],
    "个": ["", "個"],
    "字符": [" characters", "文字"],
    "份": [" files", "件"],

    /* ── 通用状态 ── */
    "加载中…": ["Loading…", "読み込み中…"],
    "请求失败": ["Request failed", "リクエスト失敗"],
    "后端未启动或无法连接（": ["Backend not running or unreachable (", "バックエンド未起動または接続不可（"],
    "）。请先启动本机后端服务后刷新本页。": ["). Please start the local backend and reload this page.",
       "）。先にローカルバックエンドを起動して、ページを再読み込みしてください。"],
    "本页所有数据均来自本机后端接口；后端未运行时页面不会显示假数据。":
      ["All data on this page comes from the local backend API; no placeholder data is shown when the backend is down.",
       "本ページのデータはすべてローカルバックエンドの API 由来です。バックエンドが停止している間、偽のデータは表示されません。"],
    "操作失败：": ["Operation failed: ", "操作に失敗しました："],
    "保存失败：": ["Save failed: ", "保存に失敗しました："],
    "保存失败（浏览器禁用了本地存储？）": ["Save failed (local storage blocked by the browser?)", "保存に失敗しました（ブラウザがローカルストレージを無効にしていませんか？）"],
    "写入本地存储失败（浏览器禁用了本地存储或配额已满），本地数据未改动。":
      ["Could not write to local storage (blocked by the browser, or the quota is full); local data is unchanged.",
       "ローカルストレージへの書き込みに失敗しました（ブラウザが無効化したか容量不足）。ローカルデータは変更されていません。"],
    "读取文件失败。": ["Could not read the file.", "ファイルを読み込めませんでした。"],
    "同步失败": ["Sync failed", "同期に失敗しました"],
    "没有需要同步的记录": ["Nothing to sync", "同期するレコードがありません"],
    "已同步：": ["Synced: ", "同期完了："],
    "合并后共 ": ["Merged, ", "件のレコードを統合しました"],
    "未知错误": ["Unknown error", "不明なエラー"],
    "自动同步已开启": ["Auto sync enabled", "自動同期を有効にしました"],
    "自动同步已关闭": ["Auto sync disabled", "自動同期を無効にしました"],
    "已切换。": ["Switched.", "切り替えました。"],
    "切换失败：": ["Switch failed: ", "切り替えに失敗しました："],

    /* ── 检索 / 文献页 ── */
    "检索方式": ["Search mode", "検索方式"],
    "整句精确": ["Exact phrase", "完全一致"],
    "分词扩召回": ["Token + synonyms", "分割＋類義語"],
    "两者合并": ["Both combined", "両方まとめて"],
    "应用": ["Apply", "適用"],
    "重置": ["Reset", "リセット"],
    "排序": ["Sort", "並び替え"],
    "编号": ["ID", "番号"],
    "出版年": ["Year", "出版年"],
    "题名": ["Title", "タイトル"],
    "每页": ["Per page", "表示件数"],
    "类别筛选": ["Category filter", "カテゴリ絞り込み"],
    "类型": ["Type", "種別"],
    "（全部）": ["(All)", "（すべて）"],
    "留空＝浏览；填词＝检索（题名/作者/关键词/MD 全文/原始 JSON）":
      ["Leave empty to browse; type to search (title / author / keywords / MD body / raw JSON)",
       "空欄＝閲覧、入力＝検索（タイトル／著者／キーワード／MD 本文／生 JSON）"],
    "原文里逐字出现才算命中（子串匹配）": ["Only literal occurrences in the text count (substring match)",
       "本文にそのまま現れる場合のみヒット（部分一致）"],
    "分词后按词匹配，并按领域词典的同义组扩展召回":
      ["Match by token, and expand recall through the domain synonym groups",
       "分割した語で照合し、分野辞書の類義語グループで召回を拡張"],
    "两种语义合并，召回最全（默认）": ["Both semantics combined — broadest recall (default)",
       "両方の意味を統合（最も広く召回・既定）"],
    "导出当前结果 CSV": ["Export the current results as CSV", "現在の結果を CSV で書き出し"],
    "⬇ 导出当前结果 CSV": ["⬇ Export the current results as CSV", "⬇ 現在の結果を CSV で書き出し"],
    "没有符合条件的文献。请放宽筛选条件，或把关键词清空改为浏览。":
      ["No literature matches the filters. Relax them, or clear the keyword to browse instead.",
       "条件に合う文献がありません。絞り込みを緩めるか、キーワードを空にして閲覧してください。"],
    "库内文献总数": ["Total records in the library", "庫内文献の総数"],
    "当前条件命中条数": ["Records matching the current filters", "現在の条件でヒットした件数"],
    "当前页显示": ["Shown on this page", "このページの表示件数"],
    "命中片段数": ["Match snippets", "ヒット断片数"],
    "检索方式：": ["Search mode: ", "検索方式："],
    "精确层：": ["Exact layer: ", "精密層："],
    "（子串＝原文逐字出现；词级＝分词后是独立的一个词；同义＝靠领域词典的同义组召回）":
      ["(substring = appears verbatim in the text; token = a whole word after segmentation; synonym = recalled through the domain synonym groups)",
       "（部分一致＝本文に逐語で現れる／単語＝分割後の一語として成立／類義語＝分野の類義語グループで召回）"],
    "没有检测到差异。": ["No differences detected.", "差分は検出されませんでした。"],
    "正在预检…": ["Pre-checking…", "事前チェック中…"],
    "预检失败：": ["Pre-check failed: ", "事前チェックに失敗しました："],
    "正在写入…": ["Writing…", "書き込み中…"],
    "提交失败：": ["Submit failed: ", "送信に失敗しました："],

    /* ── 图版页 ── */
    "图版检索": ["Plate search", "図版検索"],
    "库内图版总数": ["Total plates in the library", "庫内の図版総数"],
    "覆盖文献": ["Covering records", "対応する文献数"],
    "索引状态": ["Index status", "インデックス状態"],
    "仅图版文本": ["Plate text only", "図版テキストのみ"],
    "文本＋标签": ["Text + tags", "テキスト＋タグ"],
    "没有命中的图版。关键词留空即按类型浏览。":
      ["No plate matches. Leave the keyword empty to browse by type.",
       "ヒットする図版がありません。キーワードを空にすると種別で閲覧できます。"],
    "已复核": ["Verified", "復核済"],
    "打开该文献 →": ["Open this record →", "この文献を開く →"],
    "上一页": ["Previous", "前へ"],
    "下一页": ["Next", "次へ"],
    "页　共 ": [" pages · total ", " ページ · 合計 "],
    "第": ["Page ", "ページ "],
    "点击看大图": ["Click to view the large image", "クリックで拡大図を見る"],
    "查看大图：": ["View large image: ", "拡大図を見る："],
    "图像文件未找到": ["Image file not found", "画像ファイルが見つかりません"],
    "图版大图查看器": ["Plate image viewer", "図版・拡大図ビューア"],
    "文献原文阅读": ["Literature full-text reader", "文献原文ビューア"],
    "阅读原文": ["Read the original", "原文を読む"],
    "✕ 关闭": ["✕ Close", "✕ 閉じる"],

    /* ── 统计与地图页 ── */
    "统计与地图": ["Statistics and map", "統計と地図"],
    "① 时间分期（periods）": ["① Period (periods)", "① 時期（periods）"],
    "② 出版年份（years）": ["② Publication year (years)", "② 出版年（years）"],
    "③ 文献类型（doc_types）": ["③ Document type (doc_types)", "③ 文書種別（doc_types）"],
    "时间线统计不可用：": ["Timeline statistics unavailable: ", "時間軸の統計を利用できません："],
    "空间分区统计不可用：": ["Spatial-division statistics unavailable: ", "空間区分の統計を利用できません："],
    "图例": ["Legend", "凡例"],
    "地图数据不可用：": ["Map data unavailable: ", "地図データを取得できません："],
    "卫星影像": ["Satellite imagery", "衛星画像"],
    "中心 ": ["Centre ", "中心 "],
    "比例 约 0.52 m/像素（z18 原生）　影像：": ["Resolution ≈ 0.52 m/pixel (native z18) · imagery: ", "解像度 約 0.52 m/ピクセル（z18 ネイティブ）・画像："],
    "　影像时相未标注": [" · imagery date not stated", "・画像の撮影時点は未記載"],
    "本包未随附卫星影像瓦片（仅组内离线包含）。":
      ["This package does not include the satellite tiles (group offline bundle only).",
       "本パッケージには衛星タイルは含まれません（グループ内オフライン版のみ）。"],
    "卫星影像仅覆盖良渚古城核心区（约 4 km×4 km）；点图看大图。":
      ["Satellite imagery covers only the core of the Liangzhu ancient city (about 4 km × 4 km); click an image to enlarge.",
       "衛星画像は良渚古城の中心域（約 4 km×4 km）のみです。画像をクリックすると拡大できます。"],

    /* ── 图谱页 ── */
    "实际返回节点": ["Nodes actually returned", "実際に返されたノード"],
    "实际返回边": ["Edges actually returned", "実際に返されたエッジ"],
    "节点截断后": ["After node truncation", "ノード切断後"],
    "边裁剪前": ["Edges before pruning", "エッジ刈り込み前"],
    "边裁剪后": ["Edges after pruning", "エッジ刈り込み後"],
    "剔除孤立点": ["Isolated nodes removed", "孤立ノード除去"],
    "显示名字的节点": ["Nodes with visible labels", "名前を表示するノード"],
    "标签渐进披露": ["Progressive labels", "ラベルの段階的開示"],
    "筛选后总节点": ["Nodes after filtering", "絞り込み後のノード数"],
    "筛选后总边": ["Edges after filtering", "絞り込み後のエッジ数"],
    "连通分量": ["Connected components", "連結成分"],
    "截断状态": ["Truncation state", "切断状態"],
    "已截断": ["Truncated", "切断済み"],
    "未截断": ["Not truncated", "切断なし"],
    "按当前子图重算（固定 seed）": ["Recomputed on the current subgraph (fixed seed)", "現在の部分グラフで再計算（固定 seed）"],
    "固定坐标（physics 已关）": ["Fixed coordinates (physics off)", "固定座標（physics オフ）"],
    "力导向兜底": ["Force-directed fallback", "力学レイアウトへフォールバック"],
    "全部 ": ["All ", "すべて "],
    "个节点用后端给的 x/y，位置固定可复现": [" nodes use the backend x/y, so positions are fixed and reproducible",
       "ノードすべてがバックエンドの x/y を使うため、位置は固定で再現可能"],
    "个节点坐标，未给坐标的节点退回力导向": [" nodes have coordinates; the rest fall back to force-directed layout",
       "ノードに座標が与えられ、残りは力学的レイアウトにフォールバック"],
    "后端固定坐标（不开力导向，位置可复现）": ["Backend fixed coordinates (no physics, reproducible positions)",
       "バックエンド固定座標（力学 off、再現可能な位置）"],
    "力导向兜底（后端本次未给坐标）": ["Force-directed fallback (the backend returned no coordinates this time)",
       "力学レイアウトへフォールバック（今回はバックエンドが座標を返していない）"],
    "没有匹配的边（搜索：": ["No matching edges (search: ", "一致するエッジがありません（検索："],
    "本次筛选没有返回任何边。": ["This filter returned no edges.", "この絞り込みではエッジが返りませんでした。"],
    "该关系没有可展示的证据条目。": ["This relation has no evidence record to show.", "この関係には表示できる証拠レコードがありません。"],
    "搜索边：源 / 目标 / 关系 / 节点名": ["Search edges: source / target / relation / node name", "エッジ検索：始点／終点／関係／ノード名"],

    /* ── 工作台 ── */
    "导出 XML": ["Export XML", "XML を書き出し"],
    "导出离线图谱 HTML": ["Export an offline graph HTML", "オフライングラフ HTML を書き出し"],
    "当前身份：": ["Signed in as: ", "現在のアカウント："],
    "当前未登录": ["Not signed in", "未ログイン"],
    "未登录": ["Not signed in", "未ログイン"],
    "我的收藏 ": ["My favourites ", "お気に入り "],
    "待审提案 ": ["Pending proposals ", "承認待ちの提案 "],
    "我的浏览记录 ": ["My browsing history ", "閲覧履歴 "],
    "条（存在本浏览器）": [" records (stored in this browser)", "件（このブラウザーに保存）"],
    "还没有收藏。在文献详情页点「☆ 收藏」即可加入。":
      ["No favourites yet. Open a record and click “☆ Favourite” to add one.",
       "まだお気に入りがありません。文献の詳細画面で「☆ お気に入り」をクリックして追加してください。"],
    "没有待审提案。": ["No pending proposals.", "承認待ちの提案はありません。"],
    "还没有解析任务。": ["No parsing jobs yet.", "解析ジョブはまだありません。"],
    "暂无导入记录。": ["No import history yet.", "インポート履歴はまだありません。"],
    "「分词实验室与词典」仅管理员可见。": ["“Token lab and dictionary” is visible to admins only.",
       "「トークン実験室と辞書」は管理者のみ閲覧できます。"],
    "保存词典": ["Save the dictionary", "辞書を保存"],
    "重建词级索引": ["Rebuild the token index", "単語インデックスを再構築"],
    "发现候选新词": ["Discover candidate words", "候補語を発見"],
    "采纳勾选的": ["Adopt the selected ones", "選択したものを採用"],
    "开始预检": ["Start the pre-check", "事前チェックを開始"],
    "已加入列表，**记得点「保存词典」**。": ["Added to the list — remember to click “Save the dictionary”.",
       "リストに追加しました。「辞書を保存」をクリックするのを忘れずに。"],
    "本地接口可留空": ["The local endpoint may be left blank", "ローカル API は空欄可"],
    "解析服务不可用：": ["Parsing service unavailable: ", "解析サービスを利用できません："],
    "读取任务失败：": ["Could not read the job: ", "ジョブを読み込めませんでした："],
    "已提交，等待管理员审核（提案号 ": ["Submitted, waiting for admin review (proposal no. ", "送信しました。管理者の承認待ちです（提案番号 "],
    "图版清单（可编辑）": ["Plate list (editable)", "図版リスト（編集可）"],
    "本次解析没有产出图版（后端未返回 images）。": ["This parse produced no plates (the backend returned no images).",
       "今回の解析では図版が出力されませんでした（バックエンドが images を返しませんでした）。"],
    "读词典失败：": ["Could not read the dictionary: ", "辞書を読み込めませんでした："],
    "词典已保存（版本 v": ["Dictionary saved (version v", "辞書を保存しました（バージョン v"],
    "重建中…（约 6 秒，201 篇）": ["Rebuilding… (about 6 seconds, 201 records)", "再構築中…（約 6 秒、201 件）"],
    "正在重建词级索引，请稍候…": ["Rebuilding the token index, please wait…", "単語インデックスを再構築しています…"],
    "重建失败：": ["Rebuild failed: ", "再構築に失敗しました："],
    "重建完成：": ["Rebuild finished: ", "再構築が完了しました："],
    "已重新读取词典（未保存的改动已丢弃）。": ["Dictionary reloaded (unsaved changes discarded).",
       "辞書を再読み込みしました（未保存の変更は破棄されました）。"],
    "已恢复。": ["Restored.", "復元しました。"],
    "同义组要有个名字，且至少两个成员。": ["A synonym group needs a name and at least two members.",
       "類義語グループには名前と 2 つ以上のメンバーが必要です。"],
    "发现中…（要扫全库，约十几秒）": ["Scanning… (whole library, about ten seconds)", "スキャン中…（全ライブラリ走査、数十秒）"],
    "找到 ": ["Found ", "件の候補が見つかりました："],
    "个候选。**勾选你要收的**，再点「采纳勾选的」。": [" candidates. Tick the ones you want, then click “Adopt the selected ones”.",
       "件。採用したいもの ticked してから「選択したものを採用」をクリックしてください。"],
    "先勾选要采纳的候选词。": ["Tick the candidate words to adopt first.", "まず採用する候補語を選んでください。"],
    "采纳失败：": ["Adopt failed: ", "採用に失敗しました："],
    "已收 ": ["Adopted ", "件の語を採用しました："],
    "个词。": [" words.", "語。"],
    "（跳过已在词典里的 ": [" (skipped ", "語（辞書に既にある "],
    "个）": [" already in the dictionary)", "語はスキップ）"],
    "图注·中文": ["Caption (Chinese)", "図版注釈・中文"],
    "图注·英文": ["Caption (English)", "図版注釈・英文"],
    "AI 描述": ["AI description", "AI 説明"],
    "AI 物体": ["AI object", "AI 物体"],
    "图号": ["Figure no.", "図番号"],
    "打开": ["Open", "開く"],

    /* ── 表单 / 按钮 ── */
    "保存配置": ["Save the configuration", "設定を保存"],
    "正在测试连接…": ["Testing the connection…", "接続をテスト中…"],
    "测试失败：": ["Test failed: ", "テストに失敗しました："],
    "连接正常：": ["Connection OK: ", "接続は正常です："],
    "正在上传…": ["Uploading…", "アップロード中…"],
    "上传失败：": ["Upload failed: ", "アップロードに失敗しました："],
    "上传成功：": ["Upload succeeded: ", "アップロードに成功しました："],
    "正在下载…": ["Downloading…", "ダウンロード中…"],
    "远端文件不是合法的浏览数据 JSON（解析失败），本地数据未改动。":
      ["The remote file is not valid browsing-history JSON (parse failed); local data is unchanged.",
       "リモートファイルが有効な閲覧データ JSON ではありません（解析失敗）。ローカルデータは変更されていません。"],
    "已合并：文件 ": ["Merged: file ", "マージしました：ファイル "],
    "条，更新时间向前刷新 ": [" records, the update timestamp moved forward ", "件、更新日を新しく "],
    "没有可清除的浏览记录。": ["There is no browsing history to clear.", "消去する閲覧履歴がありません。"],
    "清除失败（浏览器禁用了本地存储？）。": ["Could not clear (local storage blocked by the browser?).",
       "消去できませんでした（ブラウザがローカルストレージを無効にしていませんか？）。"],
    "已清除全部浏览记录。": ["All browsing history cleared.", "閲覧履歴をすべて消去しました。"],
    "已导出 ": ["Exported ", "件を書き出しました："],
    "导出失败：": ["Export failed: ", "書き出しに失敗しました："],
    "正在导出…": ["Exporting…", "書き出し中…"],
    "已开始下载（若没反应，检查浏览器是否拦截了下载）。":
      ["The download has started (if nothing happens, check whether the browser blocked it).",
       "ダウンロードを開始しました（反応がない場合は、ブラウザがダウンロードをブロックしていないか確認してください）。"],
    "还没有浏览记录。到「文献」页打开任意一篇文献，这里就会多一条。":
      ["No browsing history yet. Open any record from the “Literature” page and it will show up here.",
       "閲覧履歴はまだありません。「文献」ページで任意の文献を開くと、ここに記録されます。"],
    "查看大图": ["View large image", "拡大図を見る"],
    "导出离线图谱": ["Export offline graph", "オフライングラフを書き出し"],
    "待审列表不可用：": ["Pending list unavailable: ", "承認待ちリストを利用できません："],
    "收藏列表不可用：": ["Favourites list unavailable: ", "お気に入りリストを利用できません："],
    "导入历史不可用：": ["Import history unavailable: ", "インポート履歴を利用できません："],
    "请先选择一个文件": ["Please select a file first", "先にファイルを選択してください"],
    "云端解析需要 API Key（填在上面的框里，或先「保存到浏览器」）":
      ["Cloud parsing needs an API key (enter it in the box above, or “save to browser” first)",
       "クラウド解析には API キーが必要です（上の入力欄に入力、または先に「ブラウザーに保存」）"],
    /* ── 统计页 · 地图增强（BP12）── */
    "图层": ["Layers", "レイヤー"],
    "复位": ["Reset view", "表示リセット"],
    "定位": ["Locate", "移動"],
    "年份筛选": ["Year filter", "年フィルタ"],
    "图层面板": ["Layers", "レイヤーパネル"],
    "遗址群范围框": ["Site-group bounds", "遺跡群の範囲"],
    "文献坐标点": ["Literature coordinate points", "文献座標点"],
    "空间分区代表点": ["Spatial-zone representative points", "空間区分の代表点"],
    "候选遗址（实测坐标）": ["Candidate sites (surveyed coordinates)", "候補遺跡（実測座標）"],
    "出版年区间": ["Publication-year range", "出版年の範囲"],
    "全部": ["All", "すべて"],
    "个点位": ["points", "ポイント"],
    "个匹配点位": ["matching points", "件の一致"],
    "无匹配点位": ["No matching point", "一致する地点がありません"],
    "无出版年信息": ["(without publication-year data)", "（出版年情報なし）"],
    "勾掉即隐藏该类点位；数据本身不变。": ["Uncheck to hide that layer; the data itself is unchanged.", "チェックを外すとその種別は非表示（データは変わりません）。"],
    "口径：按点位所关联文献的出版年过滤；无出版年信息的点位始终显示。": ["Filter by the publication year of the literature linked to each point; points without year data always stay visible.", "各地点に紐づく文献の出版年で絞り込み。出版年情報のない地点は常に表示されます。"],
    "出处": ["Source", "出典"],
    "关联文献": ["Related literature", "関連文献"],
    "篇": ["items", "件"],
    "搜索文献号 / 地名 / 分区名": ["Search by literature ID, place name or zone name", "文献番号・地名・区分名で検索"],
    /* ── BP14 良渚时间图谱（阶段 1 样例版）── */
    "良渚时间图谱": ["Liangzhu timeline", "良渚時譜"],
    "⑤ 良渚时间图谱 × 交互地图": ["⑤ Liangzhu timeline × interactive map", "⑤ 良渚時譜 × インタラクティブ地図"],
    "X＝良渚文化分期带　Y＝主题族带　格内纵向＝出版年": ["X = Liangzhu cultural periods　Y = topic families　vertical position = publication year", "X＝良渚文化分期帯　Y＝テーマ族帯　縦位置＝出版年"],
    "良渚时间图谱矩阵：横轴为良渚文化分期，纵轴为主题族带，每个点是一篇文献": ["Liangzhu timeline matrix: horizontal axis = cultural periods, vertical axis = topic families, each dot is one literature record", "良渚時譜マトリクス：横軸＝良渚文化分期、縦軸＝テーマ族帯、点1つ＝文献1件"],
    "点＝一篇文献（一篇可带多个主题族，故在多条带各出现一次）；": ["One dot = one literature record (a record may carry several topic families, so it appears in several bands); ", "点1つ＝文献1件（1件に複数のテーマ族があり、複数帯に重複表示）；"],
    "点的颜色＝出版年：": ["dot colour = publication year: ", "点の色＝出版年："],
    "较早": ["earlier", "より古い"],
    "较晚": ["later", "より新しい"],
    "；点它 → 右侧出图版与文献；点格子／列头／行头 → 筛选并联动地图。": ["; click a dot → plates and record on the right; click a cell / column head / row head → filter and link the map.", "；点を選ぶ → 右に図版と文献を表示；セル／列見出し／行見出しをクリック → 絞り込みと地図連動。"],
    "出版年 {y}": ["Year {y}", "出版年 {y}"],
    "图版 {n} 张": ["Plates {n}", "図版 {n} 枚"],
    "分期 {p}": ["Period {p}", "分期 {p}"],
    "主题族 {f}": ["Topic family {f}", "テーマ族 {f}"],
    "空间分区 {r}": ["Spatial division {r}", "空間分区 {r}"],
    "{t}｜{y}｜图版 {n} 张": ["{t}｜{y}｜Plates {n}", "{t}｜{y}｜図版 {n} 枚"],
    "无年份": ["no year", "年不明"],
    "「{b}」中含申遗后文献 {n} 篇": ["In “{b}”, {n} records are post-heritage (after the 2019 inscription)", "「{b}」には申遺後の文献が {n} 件ある"],
    "文献编号：": ["Record ID: ", "文献番号："],
    "出版年：": ["Year: ", "出版年："],
    "类型：": ["Type: ", "種別："],
    "良渚分期：": ["Liangzhu period: ", "良渚分期："],
    "主题族：": ["Topic family: ", "テーマ族："],
    "空间分区：": ["Spatial division: ", "空間分区："],
    "图版：": ["Plates: ", "図版："],
    "文献 {n} 篇（展开点 {m} 个）": ["Literature {n} records ({m} expanded dots)", "文献 {n} 件（展開点 {m} 個）"],
    "全部分期": ["All periods", "全期間"],
    "全部主题族": ["All topic families", "すべてのテーマ族"],
    "收起": ["Collapse", "閉じる"],
    "打开文献详情": ["Open record detail", "文献詳細を開く"],
    "在文献页筛选": ["Filter on the literature page", "文献ページで絞り込む"],
    "正在读取图版…": ["Loading plates…", "図版を読み込み中…"],
    "图版读取失败：": ["Failed to load plates:", "図版の読み込みに失敗："],
    "这篇没有图版。": ["This record has no plates.", "この文献に図版はありません。"],
    "共 {n} 张，以下显示前 {m} 张": ["Total {n} plates; showing the first {m} ", "全 {n} 枚中、先頭 {m} 枚を表示"],
    "（其中 {n} 张尚未人工复核，图注与分类为 AI 初标）": ["({n} of them are not yet manually reviewed; captions and categories are AI initial labels)", "（うち {n} 枚は未だ人手復核されていません。図注と分類は AI の初期ラベルです）"],
    /* ── BP15 良渚时序（首页 · 阶段 1 样例版）── */
    "右轴＝文献出版年（文献学时间，小在上、大在下）": ["Right axis = publication year (bibliographic time; smaller years on top)", "右軸＝出版年（文献学的な時間、小さい年が上）"],
    "右轴＝文献出版年（文献学时间，小在上、大在下；非考古年代）。": ["Right axis = publication year (bibliographic time; smaller years on top; not archaeological).", "右軸＝出版年（文献学的な時間、小さい年が上；考古年代ではない）。"],
    "点右轴＝设定「截至该年」；点年代轴＝设定考古游标（播放轴）；点期段＝定位到该期；点分区＝按该分区筛选。": ["Click the right axis for “up to that year”; click the chronology to set the archaeological cursor (playback axis); click a period to jump to it; click a region to filter by it.", "右軸をクリック＝「その年までを」に設定／年代軸をクリック＝考古カーソル（再生軸）を設定／時期をクリック＝その時期へ／区画をクリック＝区画で絞り込み。"],
    "文献出版年": ["Publication year", "出版年"],
    "良渚时序时间轴：横轴为良渚考古年代（公元前），右轴为文献出版年": ["Liangzhu timeline: horizontal axis = Liangzhu archaeological chronology (BC), right axis = publication year", "良渚時譜：横軸＝良渚考古年代（紀元前）、右軸＝出版年"],
    "下轴＝良渚考古年代轴（公元前，按真实年代比例排）": ["Lower axis = Liangzhu archaeological chronology (BC, drawn to true date proportion)", "下軸＝良渚考古年代軸（紀元前、実年代に比例）"],
    "下轴＝良渚考古年代轴（公元前，按真实年代比例排），是本页播放轴。": ["Lower axis = Liangzhu archaeological chronology (BC, drawn to true date proportion) — the playback axis.", "下軸＝良渚考古年代軸（紀元前、実年代に比例）＝本ページの再生軸。"],
    "出处（本站文献）": ["Sources (site literature)", "出典（本サイト文献）"],
    "前 {n}": ["{n} BC", "紀元前 {n}"],
    "前 {n} 年": ["{n} BC", "紀元前 {n} 年"],
    "原文依据（本站文献）": ["Textual basis (site literature)", "原文の根拠（本サイト文献）"],
    "命中 {n} 篇": ["{n} hits", "該当 {n} 件"],
    "年份区间无法用文献页表达，跳转只带登记分期或分区。": ["Year ranges cannot be expressed on the literature page; links carry only registered period or region.", "年号区間は文献ページで表現できないため、登録分期か区画のみ引き継ぐ。"],
    "期段归属按文献原文表述与绝对年代自动识别（{m}/{s} 篇命中），非人工编目；未入期者不出现。": ["Period assignment is auto-detected from the wording and absolute dates in each text ({m}/{s} hits), not hand-coded; texts with no hit do not appear.", "時期の判定は本文の表現と絶対年代から自動認識（{m}/{s} 件該当）であり、人手による編目ではない。該当しない文献は表示しない。"],
    "期段特征": ["Period characteristics", "時期の特徴"],
    "未定位期段": ["No period located", "時期が未設定"],
    "点年代轴＝设定考古游标（播放轴）；点出版年＝设定「截至该年」；点期段＝定位到该期；点分区＝按该分区筛选。": ["Click the chronology to set the archaeological cursor (playback axis); click a year for “up to that year”; click a period to jump to it; click a region to filter by it.", "年代軸をクリック＝考古カーソル（再生軸）を設定／出版年をクリック＝「その年までを」に設定／時期をクリック＝その時期へ／区画をクリック＝区画で絞り込み。"],
    "点此定位到该期": ["Click to jump to this period", "クリックでこの時期へ"],
    "登记分期标签": ["Registered period tags", "登録分期タグ"],
    "登记分期标签是教科书式分期，与良渚考古分期不同源，不画成年代轴。": ["Registered period tags are textbook-style phases, not the same system as the Liangzhu archaeological chronology, so they are not drawn as a chronology.", "登録分期タグは教科書的な分期であり、良渚考古年代と同じ体系ではないため、年代軸には描かない。"],
    "考古分期年代取自发掘简报与测年研究，存在学术分歧，各期段出处见「原文依据」。": ["Archaeological dates are taken from excavation reports and dating studies; academic disagreement remains, with sources per period under “textual basis”.", "考古分期の年代は発掘簡報と年代測定研究に基づく。学术的な異論もあり、各時期の出典は「原文の根拠」に示す。"],
    "考古游标未设定": ["Archaeological cursor not set", "考古カーソル未設定"],
    "考古游标：前 {n} 年": ["Archaeological cursor: {n} BC", "考古カーソル：紀元前 {n} 年"],
    "良渚考古年代 × 空间分区 × 文献": ["Liangzhu chronology × spatial regions × literature", "良渚考古年代 × 空間区画 × 文献"],
    "期段速览": ["Period overview", "時期一覧"],
    "良渚时序": ["Liangzhu Timeline", "良渚時譜"],
    "出版年 × 良渚分期 × 空间分区": ["Publication year × Liangzhu period × spatial division", "出版年 × 良渚分期 × 空間分区"],
    "搜索题名 / 文献编号": ["Search by title / record ID", "題名・文献番号で検索"],
    "播放": ["Play", "再生"],
    "暂停": ["Pause", "一時停止"],
    "当前状态": ["Current state", "現在の状態"],
    "截至 {y} 年": ["Up to {y}", "{y}年まで"],
    "截至 {y} 年：{n} 篇": ["Up to {y}: {n} records", "{y}年まで：{n} 件"],
    "全部年份": ["All years", "すべての年"],
    "全部分区": ["All spatial divisions", "すべての空間分区"],
    "命中 {n} 篇，共 {m} 篇": ["{n} of {m} records hit", "全 {m} 件中 {n} 件"],
    "全库 {m} 篇": ["{m} records in total", "全庫 {m} 件"],
    "在文献页按该分期筛选": ["Filter by this period on the literature page", "文献ページでこの分期で絞り込む"],
    "在文献页按该分区筛选": ["Filter by this division on the literature page", "文献ページでこの分区で絞り込む"],
    "清除筛选": ["Clear filters", "絞り込みを解除"],
    "命中文献": ["Hit records", "該当文献"],
    "口径与限制": ["Scope and limits", "口径と制限"],
    "其中 {n} 篇无出版年信息（不参与年份筛选）": ["{n} records have no publication year (excluded from year filtering)", "出版年情報のない文献が {n} 件（年の絞り込み対象外）"],
    "地图点位：分区代表点为区域统一坐标（±1.1 km 示意），不是遗址实测坐标。": ["Map points: division representative points use one zone-wide coordinate (±1.1 km schematic), not surveyed site coordinates.", "地図の点：分区代表点は区域全体の代表座標（±1.1 km の概略）で、実測座標ではない。"],
    "本图为良渚区域尺度；不画国界与省界，边界不作官方界线依据。": ["This map is at Liangzhu regional scale; no national or provincial boundaries are drawn, and no boundary is an official demarcation.", "本図は良渚域スケール。国境・省界は描いておらず、境界を公式の境界線として扱わない。"],
    "年份区间无法用文献页表达，跳转只带分期或分区。": ["A year range cannot be expressed on the literature page; jumps carry only the period or the division.", "年の範囲は文献ページでは指定できない。遷移には分期か分区のみが反映される。"],
    "无结果": ["No results", "該当なし"],
    "正在读取良渚时序…": ["Loading the Liangzhu timeline…", "良渚時譜を読み込み中…"],
    "时序数据不可用：": ["Timeline data unavailable: ", "時譜データを取得できません："],
    "Leaflet 未加载，地图不可用。": ["Leaflet is not loaded; the map is unavailable.", "Leaflet が読み込まれていません（地図は利用不可）。"],
    "良渚分期": ["Liangzhu period", "良渚分期"],
  };

  /* 只做**整段精确命中**的键：这些短词在数据里也会出现（图号、类型、年份…），
     一旦做子串替换就会把数据当界面文案翻掉 —— 那属于数据损坏，不是翻译瑕疵。 */
  var AMBIG = {
    "总览": 1, "文献": 1, "图版": 1, "图谱": 1, "统计": 1, "工作台": 1,
    "类型": 1, "题名": 1, "编号": 1, "出版年": 1, "年份": 1, "语种": 1,
    "序号": 1, "主题": 1, "分期": 1, "空间": 1, "谱系": 1, "标签": 1,
    "条": 1, "个": 1, "篇": 1, "份": 1, "字符": 1, "第": 1,
    "已复核": 1, "待复核": 1, "说明": 1, "依据": 1, "来源": 1, "备注": 1,
    "检索": 1, "关键词": 1, "摘要": 1,
    /* BP12 地图增强新增的界面短词：同样只做整段精确命中 */
    "图层": 1, "复位": 1, "定位": 1, "年份筛选": 1, "全部": 1,
    "出处": 1, "关联文献": 1, "个点位": 1, "个匹配点位": 1, "无匹配点位": 1,
    "文献坐标点": 1, "空间分区代表点": 1, "候选遗址（实测坐标）": 1, "遗址群范围框": 1,
    /* BP14：这两个词在文献标题里很常见，只做整段精确命中，别翻数据 */
    "较早": 1, "较晚": 1,
    /* BP15：短词一律只做整段精确命中，别翻数据（分区名/题名里也有这些字） */
    "播放": 1, "暂停": 1, "分区": 1, "良渚分期": 1, "当前状态": 1,
    "命中文献": 1, "口径与限制": 1, "无结果": 1, "文献出版年": 1
  };

  /* ── 数据容器：这里面的中文是**数据**，一律不翻 ── */
  var SKIP = ".ttl,.snip,.au,.tg,.dl dd,.cite,.cap,.imgtile,.jsonlist,.rawbody,.ev," +
    ".ap-t,.ap-v,.ap-ev," +
    ".edgelist,.lbcap,mark,code,pre,textarea,.tablewrap td," +
    ".imgrow .act .small,.imgcard .pnl,.imgcard .small";

  var DICT = { en: {}, ja: {} };
  var subCache = null;

  (function build() {
    for (var k in ENTRIES) {
      if (!Object.prototype.hasOwnProperty.call(ENTRIES, k)) continue;
      DICT.en[k] = ENTRIES[k][0];
      DICT.ja[k] = ENTRIES[k][1];
    }
  })();

  function getLang() {
    try {
      var v = global.localStorage.getItem(LS_KEY);
      if (v === "en" || v === "ja") return v;
    } catch (e) {}
    return "zh";
  }
  function setLang(v) {
    if (v !== "en" && v !== "ja") v = "zh";
    try { global.localStorage.setItem(LS_KEY, v); } catch (e) {}
    subCache = null;
    return v;
  }
  function has(d, k) { return Object.prototype.hasOwnProperty.call(d, k); }

  /* 可做子串替换的键：长度 ≥ 2，按长度降序（先长后短） */
  function subKeys() {
    if (subCache) return subCache;
    var d = DICT[getLang()], out = [];
    if (d) {
      for (var k in d) {
        if (has(d, k) && k.length >= 2 && !AMBIG[k]) out.push(k);
      }
      out.sort(function (a, b) { return b.length - a.length; });
    }
    subCache = out;
    return out;
  }

  /* 文本翻译：整段精确 → 子串替换 */
  function tr(text) {
    var d = DICT[getLang()];
    if (!d || !text) return text;
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
    var lead = m[1], core = m[2], tail = m[3];
    if (core && has(d, core)) return lead + d[core] + tail;
    var keys = subKeys(), out = text, changed = false;
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (out.indexOf(k) >= 0) {
        out = out.split(k).join(d[k]);
        changed = true;
      }
    }
    return changed ? out : text;
  }

  function skip(el) {
    try { return !!(el && el.closest && el.closest(SKIP)); } catch (e) { return false; }
  }

  /* ★ 交互控件里的文字**永远算界面文案**：
     数据容器（如表格单元格里放按钮的那类）整体豁免时，
     按钮上的「删除」这类文案会跟着一起被跳过 —— 那就白豁免了。 */
  var UI_TAGS = { BUTTON: 1, A: 1, INPUT: 1, SELECT: 1, OPTION: 1 };
  function skipText(el) {
    if (!el) return true;
    var t = el.tagName;
    if (t && UI_TAGS[t]) return false;
    return skip(el);
  }

  var ATTRS = ["title", "aria-label", "placeholder", "alt"];

  function trAttr(el, name) {
    var v = el.getAttribute(name);
    if (v === null || v === "") return;
    var store = el.__lzAttr || (el.__lzAttr = {});
    var st = store[name];
    if (!st) { st = store[name] = { orig: v, shown: v }; }
    else if (st.shown !== v) { st.orig = v; }
    var t = tr(st.orig);
    if (t !== el.getAttribute(name)) el.setAttribute(name, t);
    st.shown = t;
  }

  function trAttrs(el) {
    for (var i = 0; i < ATTRS.length; i++) trAttr(el, ATTRS[i]);
  }

  function trTextNode(n) {
    var st = n.__lz;
    if (!st) { st = n.__lz = { orig: n.nodeValue, shown: n.nodeValue }; }
    else if (st.shown !== n.nodeValue) { st.orig = n.nodeValue; }
    var t = tr(st.orig);
    if (n.nodeValue !== t) n.nodeValue = t;
    st.shown = t;
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) {
      if (!skip(root.parentNode)) trTextNode(root);
      return;
    }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1 && skip(root)) return;
    var w = global.document.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */, null, false);
    var n;
    while ((n = w.nextNode())) {
      if (!skipText(n.parentNode)) trTextNode(n);
    }
    if (root.nodeType === 1) {
      trAttrs(root);
      var kids = root.getElementsByTagName("*");
      for (var i = 0; i < kids.length; i++) trAttrs(kids[i]);
    }
  }

  var applying = false;
  var ORIG_TITLE = global.document.title;

  /* 全量应用：切语言时用（先按缓存的原文还原，再翻） */
  function apply(root) {
    root = root || global.document.body;
    applying = true;
    try {
      var d = DICT[getLang()];
      var doc = global.document.documentElement;
      doc.setAttribute("data-lang", getLang());
      doc.setAttribute("lang", HTML_LANG[getLang()]);
      if (ORIG_TITLE) global.document.title = tr(ORIG_TITLE);
      if (!d) {
        /* 回到中文：把译文还原成原文 */
        restore(root);
        return;
      }
      walk(root);
    } finally {
      applying = false;
    }
  }

  function restore(root) {
    var all = root.querySelectorAll("*");
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var st = el.__lzAttr;
      if (st) {
        for (var a in st) {
          if (!has(st, a)) continue;
          if (el.getAttribute(a) !== st[a].orig) el.setAttribute(a, st[a].orig);
          st[a].shown = st[a].orig;
        }
      }
    }
    var w = global.document.createTreeWalker(root, 4, null, false);
    var n;
    while ((n = w.nextNode())) {
      var s = n.__lz;
      if (!s) continue;
      if (n.nodeValue !== s.orig) n.nodeValue = s.orig;
      s.shown = s.orig;
    }
  }

  /* 增量应用：DOM 变动时只处理变动的部分 */
  function onMutations(list) {
    if (applying) return;
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m.type === "characterData") {
        if (!skipText(m.target.parentNode)) trTextNode(m.target);
      } else if (m.type === "attributes") {
        if (m.target.nodeType === 1) trAttrs(m.target);
      } else if (m.type === "childList") {
        for (var j = 0; j < m.addedNodes.length; j++) {
          var nd = m.addedNodes[j];
          if (nd.nodeType === 3) {
            if (!skipText(nd.parentNode)) trTextNode(nd);
          } else {
            walk(nd);
          }
        }
      }
    }
  }

  function init() {
    var sel = global.document.getElementById("ui-lang");
    if (sel) {
      sel.value = getLang();
      sel.addEventListener("change", function () {
        setLang(sel.value);
        apply(global.document.body);
      });
    }
    apply(global.document.body);
    if (global.MutationObserver) {
      var mo = new global.MutationObserver(onMutations);
      mo.observe(global.document.body, {
        childList: true, subtree: true, characterData: true,
        attributes: true, attributeFilter: ATTRS
      });
    }
  }

  global.LZ_I18N = DICT;
  global.LZ_getLang = getLang;
  global.LZ_setLang = setLang;
  global.LZ_tr = tr;
  global.LZ_apply = apply;

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
