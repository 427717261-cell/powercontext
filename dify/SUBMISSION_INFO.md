# 提测信息速查

> 赛题：**Dify × OceanBase · Just for Fun**（总分 20）
> 作品：**深夜电台 · 怪谈档案**（Dify Chatflow × seekdb 混合检索 × PowerContext 跨会话记忆）

这份文件给**评测平台和评审**用：先看这里，再按链接进细节文档。

---

## 一、提交信息（填测评平台用）

| 项 | 值 |
| --- | --- |
| 仓库 | `https://github.com/427717261-cell/powercontext-2026-repo` |
| 分支 | `2026-competition-ecosystem` |
| 作品目录 | **`dify/`**（本文件与全部交付物都在这个目录下） |
| 提交 commit | 见本文件末尾的「本次提交」 |

> **不要把令牌写进仓库 URL。** 仓库是私有的，按赛事约定方式授予平台读取权限即可
> （平台表单的访问令牌字段），不要把 PAT 拼在 `https://user:token@github.com/...` 里。
> 另外，本作品也放在**默认分支 `2026-competition` 的 `dify/` 目录**下，
> 两个分支的 `dify/` 内容完全一致，任取其一都能复现。

### 平台拉取自检（提交前先跑一遍）

在**和评测平台同一台机器**上执行，用来确认平台确实能拉到这份代码：

```bash
# 不带令牌（公开可达时）
git ls-remote https://github.com/427717261-cell/powercontext-2026-repo refs/heads/2026-competition-ecosystem

# 带令牌（私有仓库时，token 从环境变量读，别写进命令行历史）
GIT_TERMINAL_PROMPT=0 git -c "http.extraheader=Authorization: Bearer $GITHUB_TOKEN" \
  ls-remote https://github.com/427717261-cell/powercontext-2026-repo refs/heads/2026-competition-ecosystem
```

两条命令都能输出 `refs/heads/2026-competition-ecosystem  <commit sha>` 才算通过；
只输出 sha 不带 `refs/heads/` 说明分支名填错了。**再确认 sha 与提交表单里填的一致**，
平台是按这个 sha 检出的。

## 二、交付物清单（全部在 `dify/` 下）

| 提交材料要求 | 对应文件 |
| --- | --- |
| **4.1 Dify DSL** | [`apps/midnight-radio.yml`](./apps/midnight-radio.yml)（唯一一个 DSL，导入即用，导入后需点一次「发布」） |
| **4.2 运行说明** | [`README.md`](./README.md)（第 5 章 5.1–5.9 是完整部署步骤，第 6 章是应用入口） |
| **4.3 验证样例与运行证据** | [`VERIFICATION.md`](./VERIFICATION.md) + [`evidence/`](./evidence/) |
| **4.4 按依赖补充的材料** | [`SUBMISSION.md`](./SUBMISSION.md)「4.4 按依赖补充的材料」逐行对应 |
| 逐条对照表 | [`SUBMISSION.md`](./SUBMISSION.md) |

## 三、评审要跑起来，只需要三样外部条件

| 需要什么 | 从哪来 | 对应说明 |
| --- | --- | --- |
| **MiniMax API Key**（必需） | MiniMax 开放平台 `platform.minimaxi.com` → 接口密钥 | README「5.4」。同一个 Key 提供两样东西：对话 `MiniMax-M3`、向量 `embo-01`（1536 维） |
| **seekdb 实例**（必需） | OceanBase 轻量版 seekdb | README「5.1」与 `deploy/seekdb/README.md` 给版本与部署配置；`deploy/seekdb/init_account.sql` 建库建号；`scripts/init_archive.py` 建表 + NGRAM 全文索引 + HNSW 向量索引 + 灌 20 条档案 |
| **Dify 1.4.2**（必需） | `git clone --branch 1.4.2 https://github.com/langgenius/dify.git` | README「5.2」；**未修改 Dify 任何源码** |

> PowerContext 由 `deploy/Dockerfile.powercontext` 自动构建（`powercontext[cli,server]==1.4.1`），
> 评审**不需要**自行安装。

## 四、最短复现路径（评审视角）

```bash
# 0) 先部署 seekdb（怪谈档案库的承载数据库，跑在宿主机上，不是容器）
#    版本 OceanBase seekdb 1.4.0.0，安装+配置+启动一条脚本搞定
sudo bash <repo>/dify/deploy/seekdb/install_seekdb.sh
#    建库 + 建最小权限账号（先把 init_account.sql 里的 <radio_口令> 换成自己的值）
mysql -h 127.0.0.1 -P 2881 -u root -p < <repo>/dify/deploy/seekdb/init_account.sql
#    可选：确认 seekdb 真的能写入、能全文+向量检索
bash <repo>/dify/deploy/seekdb/verify_seekdb.sh

# 1) 取 Dify 1.4.2
git clone --depth 1 --branch 1.4.2 https://github.com/langgenius/dify.git
cd dify/docker

# 2) 接入本作品（复制 compose 覆盖 + 两个后端 + 生成 .env）
bash <repo>/dify/scripts/install_dify.sh $(pwd)

# 3) 补凭据：在 .env 末尾追加 RADIO_SEEKDB_* 与 RADIO_EMBEDDING_API_KEY
#    （详见 README 5.4，真实值不要提交进仓库）

# 4) 初始化怪谈档案库
docker compose run --rm radio-archive python /app/scripts/init_archive.py

# 5) 拉起整套服务
docker compose up -d

# 6) 打开 http://<host>:<EXPOSE_NGINX_PORT>/install 创建管理员账号
#    （或在 .env 里设 INIT_PASSWORD 自动建号，见 README 5.6）

# 7) 控制台 → 插件商店 → 安装 OpenAI-API-compatible(0.0.73)
#    → 设置 → 模型供应商 → 填 MiniMax base_url / Key / 模型 MiniMax-M3(chat) → 设为默认

# 8) 控制台 → 工作室 → 导入 DSL → <repo>/dify/apps/midnight-radio.yml → 发布

# 9) 点「开始对话」，在开场表单里填听众代号 → 开始聊天
```

> 第 0 步的 seekdb 部署细节（版本、目录布局、配置文件、systemd 单元、账号授权）见
> [`deploy/seekdb/README.md`](./deploy/seekdb/README.md)。

## 五、两条验证样例（结论先行）

| 样例 | 做什么 | 预期 | 证据 |
| --- | --- | --- | --- |
| 样例一：首次到访 | 听众代号填一个**没用过的**名字（如 `小满`），问 `想听末班地铁的怪谈` | 主播说「欢迎第一次来到」；卷宗卡片带 `命中通道:fts` / `命中通道:vector` | `evidence/03_chat_results.json`、`evidence/screenshots/03_首轮播报_卷宗卡片.jpg` |
| 样例二：跨会话记忆 | **新开一段会话**，代号**还填同一个**，问 `我今天特别累，随便讲个短的` | 主播说「第二次来了，我还记得你，上次想听末班地铁」，并给出沿用上轮素材的 A/B 选项 | `evidence/04_powercontext_memory.json`、`evidence/screenshots/06_跨会话记忆_第二次到访_混合检索.png` |

> 记忆存在 **PowerContext** 里（按听众代号 → Scope），不在 Dify 的会话上下文里，
> 所以样例二的 `conversation_id` 与样例一是**两条不同的会话**。
> 想把状态重置回「第一次到访」，换一个没用过的听众代号即可（VERIFICATION 第 0 节给了完整重置步骤）。

## 六、评审最可能踩的三个坑（都已在 README 里给出解法）

1. **中文全文检索不命中** → seekdb 必须用 `WITH PARSER NGRAM` 建全文索引（README 5.4 下方提示）。
2. **MiniMax 额度触顶** → LLM 节点返回 429（`已达到 Token Plan 用量上限`），
   检索链路仍正常；换 Key 或等额度恢复即可。
3. **PowerContext 记忆接口无鉴权** → compose 默认只绑 `127.0.0.1`，别直接暴露公网。

## 七、本次提交

| 项 | 值 |
| --- | --- |
| 分支 | `2026-competition-ecosystem` |
| commit | **取该分支的 tip**（`git ls-remote https://github.com/427717261-cell/powercontext-2026-repo refs/heads/2026-competition-ecosystem` 可查）。本文件随每次提交同步更新，不写死具体 SHA，避免过期 |
| 同一份内容 | 也已同步到默认分支 `2026-competition` 的 `dify/` 目录（内容完全一致） |

> 完整提交历史见仓库的 `dify/` 目录提交记录。
