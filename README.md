# 大宗商品行情日报 Agent

每天北京时间 **08:30** 自动生成大宗商品行情日报，通过微信推送。

```
GitHub Actions 定时任务
        ↓
Python 行情程序 (main.py)
        ↓
行情数据接口（yfinance / 新浪财经）
        ↓
数据清洗与涨跌计算
        ↓
Markdown 日报生成
        ↓
PushPlus 微信推送
```

覆盖品种：

| 分类 | 品种 |
|---|---|
| 外盘 | 布伦特原油 BZ=F、WTI 原油 CL=F、COMEX 黄金 GC=F、COMEX 白银 SI=F |
| 内盘 | SC 原油主连、AU 沪金主连、AG 沪银主连、PR 瓶片主连 |
| 暗盘 | 美国休市时附加：伦敦金、纽约金、伦敦银、纽约银、布伦特暗油、美油暗油 |

> 本报告只做行情数据汇总，**不含投资建议、买卖判断或行情预测**。

---

## 一、快速部署

### 1. 建仓库并上传代码

```bash
git init
git add .
git commit -m "init: 大宗商品行情日报 Agent"
git branch -M main
git remote add origin git@github.com:<你的用户名>/commodity-report-agent.git
git push -u origin main
```

建议设为 **Public**：公开仓库的 Actions 完全免费且不限时长。

### 2. 选择推送通道

两条通道，配了哪条走哪条；**两条都配时优先 ClawBot**。

#### 通道 A：ClawBot（微信助理）— 推荐

**送达形式是普通聊天消息**，直接进微信会话列表，和好友消息同级，**不会落在「订阅号」里**。

需要三项凭据，都从本机 WorkBuddy 里取（扫码绑定后可读到）：

| 取值 | 位置 |
|---|---|
| `CLAWBOT_BOT_TOKEN` | `~/.workbuddy/settings.json` → `claw.users.<uid>.channels.weixinClawBot.botToken` |
| `CLAWBOT_TO_USER` | 同上的 `userId`（形如 `o9cq80…@im.wechat`） |
| `CLAWBOT_CONTEXT_TOKEN` | `~/.workbuddy/claw-state/weixin/<accountId>.context_token.json` 里的 `context_token` |

⚠️ **这是「回复型协议」，不是广播接口**：`CONTEXT_TOKEN` 只能由「你给 bot 发过一条消息」产生。
- 获取方式：微信里给「微信助理」发一条消息，然后跑
  `python3 ~/.workbuddy/skills/workbuddy-claw-wechat-send/scripts/send_wx.py --acquire`
  （**先挂起轮询、再发消息**，顺序反了会抓不到，因为本机客户端会抢先消费消息）
- 有效期：实测静置 ≥12 天仍有效；每天都用则通常一直有效
- 若某天失败并提示 `ret=-2 prepare failed` → token 过期了，按上面重新获取并更新 Secret

> 该通道是**云端可用**的：发送只依赖 botToken + contextToken + userId，无服务端会话绑定，
> 因此电脑关机也照常送达（已实测：海外节点可直连 `ilinkai.weixin.qq.com`）。

#### 通道 B：PushPlus — 兜底

走微信公众号服务号，消息会落在**订阅号**里。

1. 打开 <https://www.pushplus.plus>，微信扫码登录
2. 完成**实名认证**（未实名无法调用发送接口，会返回 905）
3. 在「一对一推送」页复制 **token**

### 3. 配置 Secret

仓库 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| Name | 必填 | 说明 |
|---|---|---|
| `CLAWBOT_BOT_TOKEN` | 通道 A ✅ | 微信助理 botToken |
| `CLAWBOT_CONTEXT_TOKEN` | 通道 A ✅ | 会话 token，会过期，需定期刷新 |
| `CLAWBOT_TO_USER` | 通道 A ✅ | 收件人 userId |
| `PUSHPLUS_TOKEN` | 通道 B ✅ | PushPlus token，报告发给自己 |
| `PUSHPLUS_TOPIC` | 通道 B ⭕ | 群组编码，配了以后自己 + 群组内好友都会收到 |

只想用其中一条通道时，只配那一条的 Secret 即可（`--channel auto` 会自动选择）。

> **要发给指定好友（多人）**：目前只有 PushPlus 支持——后台「一对多推送」→ 新建群组 →
> 把二维码发给好友扫码订阅 → 编码填进 `PUSHPLUS_TOPIC`。ClawBot 是**个人 1:1 助手**，
> 协议里没有群聊能力，无法多人广播。

### 4. 试跑

`Actions` → 左侧「大宗商品行情日报」→ `Run workflow`。几十秒后手机收到日报即部署完成。

---

## 二、目录结构

```
commodity-report-agent/
├── .github/workflows/daily.yml   # 定时任务（北京 08:30 = cron 30 0 * * *）
├── config/
│   ├── products.yaml             # 品种配置（增删品种只改这里）
│   └── holidays.yaml             # 中美交易日历
├── src/
│   ├── market_data.py            # 行情获取（外盘双源 + 内盘结算价 + 暗盘 + 主力合约）
│   ├── calculator.py             # 涨跌计算 + 换月检测 + 状态快照
│   ├── holiday.py                # 中/美交易日判断
│   ├── report.py                 # Markdown 日报生成
│   ├── pushplus.py               # 微信推送
│   └── settings.py               # 配置加载
├── state/                        # 运行状态快照（自动生成并提交）
├── output/                       # 日报存档（不提交，走 Actions artifact）
├── main.py                       # 入口
└── requirements.txt
```

---

## 三、行情口径

| | 外盘 | 内盘 |
|---|---|---|
| 最新价 | 北京时间 08:30 左右的最新价 | **不取 8:30 实时价**（国内未开盘、夜盘已收） |
| 基准 | 上一交易日结算价 | 最近交易日结算价 |
| 对比 | 涨跌额、涨跌幅 | 前一交易日结算价 |

**内盘为什么不用实时接口？**

国内 08:30 时，前一夜的夜盘（至次日 02:30）已经收完，实时接口返回的是夜盘尾部价格，用它算涨跌会失真。因此程序改为调用新浪期货**日线接口**（`InnerFuturesNewService.getDailyKLine`），该接口直接返回每个交易日的**结算价**字段，可以严格取「最近交易日结算价 vs 前一交易日结算价」。

同时程序会自动剔除当天记录——因为 08:30 运行当天尚未结算。

**换月处理**

主力合约切换时，主连价格会出现跳空，导致涨跌幅看似突变。程序的处理：

1. 按品种枚举在市合约，取**持仓量最大**者作为当前主力合约；
2. 与上一次运行的状态快照比对，若发生变化则在日报中输出提示；
3. 首次运行没有基线时，改用「主连单日跳变超过阈值」作兜底提醒。

---

## 四、节假日规则

| 场景 | 行为 |
|---|---|
| 中国休市 | 按需求文档**取消内盘板块**，改为一行休市说明；海外行情保留 |
| 美国休市 | **附加暗盘板块**（伦敦金/纽约金/伦敦银/纽约银/布伦特暗油/美油暗油） |
| 周末 | 两市场均休市，报告正常生成并说明 |

美盘参考日的计算：报告在北京时间 08:30 生成，此时美东时间为前一日 19:30（冬令时）/ 20:30（夏令时），因此**美盘对应的日历日 = 北京日期 − 1 天**。

### ⚠️ 每年需要维护一次

`config/holidays.yaml` 里的节假日表需要人工更新：

- **中国** —— 国务院办公厅「关于 XXXX 年部分节假日安排的通知」，通常在当年 11 月发布次年安排。文件里已按官方原文登记 **2026 全年**。
- **美国** —— NYSE 官网 Holidays & Trading Hours / CME Group Holiday Calendar。文件里已登记 **2026、2027**。

判定规则是「周一至周五且不在节假日列表内」。中国期货市场在**调休补班的周末同样不开市**，因此补班日无需特殊处理，仅登记在 `makeup_workdays` 里作标注。

---

## 五、数据源与回退策略

### 外盘：yfinance 优先，新浪兜底

```
yfinance (BZ=F / CL=F / GC=F / SI=F)
    ↓ 失败
新浪 hf_ 实时接口 (hf_OIL / hf_CL / hf_GC / hf_SI)
```

**注意一个现实问题**：Yahoo Finance 在中国大陆网络下返回 `HTTP 403`，在 GitHub Actions 等海外节点则正常。所以：

- 你在**本地**跑，会自动走新浪；
- 在 **GitHub Actions** 里跑，会走 yfinance。

程序开跑前会做一次轻量可达性探测（`yahoo_reachable`），一旦不通就整轮跳过 yfinance —— 否则 yfinance 内部的重试退避会拖慢甚至拖垮整个任务。

想强制指定数据源：

```bash
python main.py --source sina       # 强制新浪，跳过 yfinance
python main.py --source yfinance   # 强制 yfinance
```

### 内盘：新浪期货接口

覆盖 SC / AU / AG / PR（含郑商所的 PR 瓶片），已验证可用。东方财富的期货接口已失效，未采用。

### 加一个新品种

只改 `config/products.yaml`：

```yaml
overseas:
  - id: natgas
    name: 天然气
    code: NG=F
    sina: hf_NG
    unit: 美元/百万英热
    decimals: 3
```

内盘品种需要 `symbol`（主连代码，如 `CU0`）、`prefix`（具体合约前缀，如 `cu`）、`exchange`、`unit`、`decimals`。

其他常用新浪代码参考：`hf_XAU` 伦敦金、`hf_XAG` 伦敦银、`nf_CU0` 沪铜、`nf_AL0` 沪铝、`nf_RB0` 螺纹钢、`gds_AUTD` 黄金延期。

---

## 六、本地调试

```bash
pip install -r requirements.txt

python main.py --dry-run                  # 只打印报告，不推送
python main.py --dry-run --no-state       # 顺便不写状态快照
python main.py --dry-run --date 2026-11-27  # 模拟某一天（验证节假日/暗盘逻辑）
python main.py                            # 真发（需先 export PUSHPLUS_TOKEN=...）
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--dry-run` | 只打印，不推送 |
| `--date YYYY-MM-DD` | 模拟运行日期，用于测试节假日/换月逻辑（行情仍取实时数据） |
| `--source auto\|yfinance\|sina` | 指定外盘数据源 |
| `--template markdown\|html\|txt` | PushPlus 消息模板，默认 markdown |
| `--no-state` | 不写状态快照 |
| `--no-output` | 不写 output/ 日报存档 |

---

## 七、常见问题

**Q：电脑关机还会跑吗？**
会。任务在 GitHub 的服务器上执行，与你本机完全无关。你的电脑只在 `git push` 那一刻参与一次。

**Q：我怎么知道它跑了？**
`Actions` 标签页看到绿色对勾，同时微信收到日报。任一个出现都说明链路正常。

**Q：某天没收到怎么办？**
GitHub 的 schedule 是「尽力而为」的调度，高峰期可能延迟几分钟，极端情况会丢一次。到 `Actions` 页点 `Run workflow` 手动补跑即可。若需要严格准点，可把触发壳换成 Cloudflare Workers 的 Cron Triggers（每天 10 万次请求免费），`main.py` 一行都不用改。

**Q：收不到微信消息，但 Actions 是绿的？**
看 Job 日志里 PushPlus 的返回码：`903` = token 错误，`905` = 未完成实名认证。

**Q：日报里的表格没渲染出来？**
PushPlus 某些模板对表格支持不一致，改用 `--template html` 即可。

**Q：私有仓库要钱吗？**
私有仓库每月 2000 分钟免费，本任务每天约 1–3 分钟，月消耗约 30–90 分钟，在免费额度内。公开仓库则完全不限时长。

**Q：状态快照提交失败会影响推送吗？**
不会。推送是主流程，快照回写失败只影响次日的换月比对（会退化为跳变兜底提醒）。

---

## 八、免责声明

本项目仅自动汇总公开行情数据，**不构成任何投资建议、买卖判断或行情预测**。数据来自第三方公开接口，可能存在延迟、缺失或口径差异，请以交易所官方数据为准。
