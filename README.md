# 写真样片库

一个本地运行的「拍照写真样片收集 + 分类」程序。后端 Python + SQLite，前端浏览器页面，无需构建工具。

## 一、安装与启动

环境要求：Windows 10/11，Python 3.10+（安装时勾选 "Add python.exe to PATH"）。

### 方式一：安装包（推荐）

从 [Releases](https://github.com/AZUIBUZUI/portrait-samples/releases) 下载 `portrait-samples-v1.0.0.zip`：

1. 解压到任意目录（路径不要含中文）；
2. 双击 `install.bat`：自动创建 `.venv` 虚拟环境并安装依赖，只需执行一次；
3. 双击 `start.bat`：启动服务并自动打开浏览器 http://127.0.0.1:5000。

> 安装包只含程序代码；`data/`（数据库、原图、登录态）在首次运行时自动创建，不会被打包上传。

### 方式二：手动

```bash
pip install -r requirements.txt
python run.py
```

浏览器打开 http://127.0.0.1:5000。首次运行会自动创建 `data/`（数据库、原图、缩略图）。

要用「关注博主」自动补采小红书作品，先登录一次（只需一次）。需要本机装有 Chrome：

```bash
python run.py --login      # 弹出窗口扫码，登录态存进 data/browser_profile
```

## 二、目录结构

```
portrait-samples/
├── run.py                     启动入口
├── config.py                  配置：路径 / 尺寸 / 服务参数 / AI 预留位
├── requirements.txt
├── install.bat                Windows 一键安装（建 .venv + 装依赖）
├── start.bat                  Windows 一键启动
├── core/                      领域逻辑（不依赖 Flask）
│   ├── db.py                  SQLite 表结构与连接
│   ├── storage.py             文件落盘、哈希去重、缩略图、EXIF 读取
│   ├── library.py             业务服务：导入 / 分类 / 检索 / 任务 / 统计
│   ├── browser.py             小红书浏览器会话（playwright，登录态持久化 + 抓主页/笔记）
│   ├── watch.py               关注博主：手动触发补采新作品（重复跑不重复添加）
│   └── collectors/            采集器插件
│       ├── base.py            CollectItem 与 BaseCollector
│       ├── local_folder.py    本地文件夹批量导入
│       └── web.py             网页 / 社媒采集
├── web/                       Web 层
│   ├── __init__.py            create_app：注册蓝图、媒体目录
│   ├── pages.py               页面路由（/、/collect、/watch、/categories）
│   ├── api.py                 REST API（/api/*）
│   ├── templates/             base / gallery / collect / watch / categories
│   └── static/                style.css + api.js / gallery.js / collect.js / watch.js / categories.js
└── data/                      运行时生成
    ├── library.db             SQLite 数据库
    ├── originals/年/月/        原图（按拍摄日期分目录）
    ├── thumbs/                缩略图
    └── browser_profile/       小红书登录态（Chrome 用户目录，别外传）
```

## 三、数据模型

| 表 | 作用 | 关键字段 |
|---|---|---|
| `samples` | 样片主表 | `file_hash`（唯一，去重）、`rel_path`、`thumb_path`、`source_type`(local/web/manual)、`source_url`、`source_author`、`shot_date`（网页样片=原帖发布日期，本地=EXIF 拍摄日期）、`group_key`（分组键）、`category_id`、`is_favorite`、`auto_labeled` |
| `categories` | 分类 | `name`、`keywords`（逗号分隔，自动分类依据）、`color`、`parent_id`（预留二级） |
| `tags` / `sample_tags` | 自由标签 | 多对多，用于风格 / 场景 / 器材等 |
| `jobs` | 采集任务记录 | `source_type`、`target`、`status`、`succeeded` / `duplicated` / `failed`、`message` |
| `watchers` | 关注的博主 | `user_id`（唯一，与 platform 组合）、`url`、`name`、`enabled`、`last_run_at` / `last_status` / `last_message` |
| `seen_notes` | 处理过的笔记（去重账本） | `note_id`（与 platform 组合唯一）、`watcher_id`、`status`(done/empty/failed)、`images_added` |

## 四、四条采集通路

四者在 `core/library.py` 里共用同一条流水线：**算内容哈希 → 判重 → 落盘 → 生成缩略图 → 规则归类 → 入库**。

1. **本地文件夹批量导入**：填绝对路径，递归扫描（可在采集页取消勾选「包含子文件夹」）。
2. **手工上传 / 拖拽**：采集页拖入图片或点选文件，走 `POST /api/upload`。
3. **网页 / 社媒采集**：一行一个地址。图片直链直接下载（不过滤，那是你指名要的）。页面地址会抽取 `og:image` 与 `<img>`，然后做三层**样片过滤**，把 logo / 图标 / 占位图剔掉：
   - URL 关键词（`logo`、`icon`、`avatar`、`banner`…）与平台前端静态资源域名（如 `fe-static.xhscdn.com`，小红书界面骨架图的宿主）；
   - 页面自己声明的宽高，两边都小于阈值就跳过；
   - 下载后用**真实像素尺寸**兜底：小边小于阈值的丢弃。图标再换名字也躲不过这一层。
   - 表情包 / 梗图 / 动图另有一道：文件名或地址里带「表情包 / 表情 / 梗图 / 斗图 / emoji / meme / sticker / doutu / biaoqing」，或本身就是 gif，直接剔除，不看尺寸。

   阈值默认 `config.WEB_MIN_SIDE = 720`（写真成片短边普遍 ≥1080，想更严可调到 1080），采集页可以当场调。**被过滤的张数会写进采集任务备注**（"已过滤 N 张非样片（例：…）"），不会静默丢掉。小红书 / 微博等需要登录态的专属解析在 `core/collectors/web.py` 的 `PLATFORM_HANDLERS` 里预留了接入点。

> 说明：网页采集只在你自己打开并有权保存的页面上使用，抓取他人作品请留意版权与平台规则。

4. **关注博主（手动补采）**：在「关注博主」页填一位博主的主页地址，想补的时候点一次「立即采集」，程序就用 playwright 打开 TA 的主页，把还没采过的新作品整组抓进来（一篇笔记 = 一组）。**没有定时任务在背后跑**，什么时候采你说了算。去重有两层：
   - `seen_notes` 账本：处理过的笔记（done / empty）下次直接跳过，连请求都不发；failed 的下次会重试；
   - `samples.file_hash`：图片内容哈希唯一索引，同一张图换个地址进来也只会命中已有记录。

   入库前还有两道内容过滤：表情包 / 梗图 / gif 动图直接剔除；真实像素短边小于 `WEB_MIN_SIDE`（默认 720）的糊图也不收。被过滤的张数会写进采集结果（"过滤 N 张（表情包/低清）"）。

   需要先 `python run.py --login` 扫码登录一次：博主主页的笔记列表是前端渲染的，纯 HTTP 拿到的笔记 id 是空的，**拿不到每篇的跳转地址**；而取整组图要靠"在主页里点开笔记"（站内跳转才带访问用的 `xsec_token`，从站外直接开 `/explore/<id>` 会提示页面不见了）。

   **登录态失效会自动兜住**：采集时如果发现没登录，程序会切换到有头窗口、把页面停在登录页等你扫码，登录成功后**继续这次采集**（最多等 10 分钟）；等不到才按失败处理。重复跑有去重兜底，中途登录不会重复入库。

## 五、分类标准与自动分类

### 分类：写真「画面状态」

一张样片只归一类，彼此互斥。当前 3 类（可在分类管理页增删改关键词）：

| 分类 | 关键词 |
|---|---|
| 安静 | 安静 / 静谧 / 静坐 / 垂眸 / 留白 / 慵懒 / 松弛 / 情绪 / 故事感 / 忧郁 / 张力 / 孤独 |
| 元气 | 元气 / 活力 / 明媚 / 开朗 / 俏皮 |
| 酷拽 | 酷拽 / 酷飒 / 酷感 / 高冷 / 街头 / 暗黑 / 机车 / 皮衣 / 墨镜 / 辣妹 / 朋克 / 赛博 / 拽姐 / 硬照 / 冷艳 |

> 「情绪」原先单独成类，因与「安静」在画面上高度重叠（CLIP 留一法里互相误判最多），已并入「安静」；原「情绪」的样片与关键词一并迁移。

**风格 / 光线不建分类**（日系、逆光、胶片、暗黑系…）—— 它们可以多选，放在样片「标签」里。网页采集时会把笔记的话题标签自动同步成样片标签，图库页可按标签筛选。

> 分类关键词**只写描述画面状态的词**。把风格词写进分类关键词，会让整组图被误归到一类（实测踩过：一篇手机广告的话题标签里有"电影感"，13 张全被推进某个分类）。

### 自动分类怎么跑（人工纠正 → 训练 → 批量分类）

1. **话题标签优先** —— 作者自己打的话题标签命中某分类关键词，就归入该分类；
2. **标题 / 文件名 / 作者兜底**；
3. **正文文案不参与分类** —— 正文多是营销话术或拍摄讲解，和画面状态无关，放进来会把广告里的词（如"治愈感"）当成依据。正文仍然存下来，弹窗里能看；
4. **命不中就留「未分类」** —— 不再用画面启发式乱猜，新采集的图也不会被强行塞进某一类；
5. **人工纠正一小批**（`/label` 页）—— 随机抽 60 张先给初始猜测，你逐张点正确分类（键盘数字键），点完即修正并跳到下一张；
6. **训练 + 批量分类** —— 以人工纠正过的样片为训练集，用 CLIP 视觉编码器提取 512 维图像特征，做余弦相似度加权的最近邻（k=5）分类，把剩下未分类 / 之前机器猜过的样片自动归类；会给出留一法准确率。没有 CLIP 模型文件时自动退回手工特征（颜色直方图 / 明暗分布 / 边缘密度）。

人工确认过的样本（`human_labeled = 1`）**永久退出纠错队列，也不会被自动流程覆盖**，以后再点「再抽一批」不会重复让标注。训练样本太少（少于 20 张）时会提示先多纠正几张。纠正完一批可以点「再抽 60 张纠正」，训练样本越攒越多、准确率会继续上升。

> 模型文件放在 `data/models/`：`clip_vision_quantized.onnx`（约 85MB，来自 `Xenova/clip-vit-base-patch32`）。实测：合并前的 4 类、65 张样本，手工特征留一法 54%，CLIP 特征 66%；把「情绪」并入「安静」后（3 类、124 张纠正样本）留一法约 **85%**。分类仍有主观性（安静 vs 元气、元气 vs 酷拽），继续增加纠正样本比换模型更有效。

- **阶段二（已预留）**：`config.py` 里 `AI_ENABLED / AI_ENDPOINT / AI_API_KEY` 留了位置，`core/library.py:auto_classify()` 是接入视觉模型的入口。

人工手动改过分类的样片会被标记 `auto_labeled = 0`，不再被规则覆盖。

## 六、API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 服务状态、可用采集器、AI 是否启用 |
| GET | `/api/stats` | 总数 / 未分类数 / 按分类 / 按来源 |
| GET | `/api/samples` | 列表，支持 `category_id`、`tag`、`source_type`、`group_key`、`q`、`favorite`、`page`、`page_size` |
| GET | `/api/groups` | 分组汇总（按原帖 / 文件夹 / 上传批次），支持 `category_id` |
| GET / PATCH / DELETE | `/api/samples/<id>` | 详情 / 改分类·标签·备注·收藏 / 删除（含原图） |
| GET | `/api/samples/<id>/neighbors` | 大图同组翻页：组内全部 id 与当前下标 |
| POST | `/api/upload` | multipart，字段 `files`、`category_id` |
| POST | `/api/collect` | `{source_type, target, category_id, options}` |
| POST | `/api/classify/auto` | 自动分类：先文字，再用人工纠正训练出的模型 |
| GET | `/api/label/pending` | 待人工纠正的样本与进度 |
| POST | `/api/label/train` | 用人工纠正样本训练最近邻，并给剩余样片分类 |
| GET | `/api/jobs` | 采集记录 |
| GET / POST / PATCH / DELETE | `/api/watchers` | 关注博主的增删改查（启用 / 停用见 PATCH 的 `enabled`） |
| POST | `/api/watchers/run` | 跑一遍关注列表，`{watcher_ids?, limit?, headless?}`，不传 id 就跑全部启用的 |
| GET | `/api/watchers/notes` | 最近处理过的笔记（`seen_notes`） |
| POST | `/api/classify/auto` | 重新跑规则自动分类 |
| GET / POST / PATCH / DELETE | `/api/categories` | 分类维护 |
| GET | `/api/tags` | 全部标签及使用次数 |

## 七、怎么扩展

- **加一种采集来源**：在 `core/collectors/` 下新建类，继承 `BaseCollector`，实现 `collect()` 逐条 `yield CollectItem`，然后注册进 `collectors/__init__.py` 的 `REGISTRY`。路由和前端不用改。
- **换分类思路**：只改 `core/library.py` 的 `auto_classify()` / `match_category()`。
- **加字段**：改 `core/db.py` 的 `SCHEMA` + 对应 `library.py` 的读写函数。（当前为开发阶段，直接改表结构即可，暂不做迁移脚本。）

## 八、已知待办

- 微博的专属解析（小红书已接入：`core/browser.py` 会话 + `core/watch.py` 补采，需先 `--login`）。
- 相似图去重（目前是内容完全一致才判重，可加感知哈希做「近似重复」）。
- 分类二级树的前端展示（`parent_id` 已在表里，界面目前是平铺）。
- 批量操作（一次给多张改分类 / 加标签）。

## 九、图库的两种视图

图库页右上角可切换：

- **平铺**：所有样片铺成瀑布流，支持搜索、来源筛选、只看收藏。
- **分组**：按 `group_key` 成组，方便一次看完整套组图。一个"组"就是一次采集的来源：
  - 网页 / 社媒采集 → **一个原帖 = 一组**（小红书笔记的组图天然对上）；
  - 本地文件夹导入 → 一个文件夹 = 一组；
  - 手工上传 → 同一批上传 = 一组。

  点进一组后，顶部出现「← 返回分组」，下面是该组的瀑布流；返回即回到分组列表。分类筛选条在两种视图下都生效。

大图模式（点缩略图打开）：

- 组内翻页：图片两侧的 `‹` `›` 按钮，或键盘 `←` `→`；底部显示「第几张 / 组内共几张」，顺序与列表一致。未分组的单张不显示翻页。
- 收藏：`F` 键或弹窗里的「收藏」按钮；缩略图右上角也有一键收藏。
- 去掉误判：被标了「非人像」的样片，人工确认是人像后按 `R`（或点「人像无误，去掉「非人像」」按钮），只去标签、不删图。
- 删除：`Delete` 键或弹窗里的「删除」按钮（会二次确认，原图与缩略图一并删除）；鼠标悬停缩略图右上角也有 `×`。
- 关闭：`Esc`。光标在输入框里时快捷键不生效，避免改标签 / 备注时误删。

> `group_key` 是后加的列。早期入库的样片会在启动时由 `db.py` 的 `_migrate()` 自动按 `source_url` 回填，不需要重建数据库。
