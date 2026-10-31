# FrameLab 开源到 GitHub —— 准备清单

> 勘察时间：2026-10-31。项目当前位置：`F:\Desktop\项目\不完美项目\VRAMWatch`
> 以下结论均来自对仓库的**实测扫描**，不是泛泛模板。

---

## 一、先做这 5 件"硬前提"

| # | 事项 | 现状 | 你需要做 |
|---|------|------|---------|
| 1 | **安装 Git** | ❌ 本机**未安装**（`git` 命令不存在，无 `C:\Program Files\Git`） | 装 Git for Windows（F 盘） |
| 2 | **GitHub 账号 + 仓库** | 无 | 注册/登录 → New repository |
| 3 | **决定署名身份** | — | 真实姓名 / 笔名 / 匿名，三者选一（影响 `user.name` 与 README 作者栏） |
| 4 | **选许可证** | ❌ 仓库**无 LICENSE 文件** | 建议 **MIT**（最宽松、适合工具类；见 §四） |
| 5 | **配置 Git 身份** | 未配置 | `git config user.name/email`（建议用**专用邮箱**，别用个人主邮箱） |

> ⚠️ 第 1 项是**当前唯一阻塞**：没 Git 无法推送。

---

## 二、仓库内容决策（关键：别把 51MB EXE 塞进 Git）

### 扫描结果
- **源码 + 文档**：67 个文件，**2.8 MB** ✅ 全部可以进仓库
- **若连 dist\FrameLab.exe 一起**：103 个文件，**54.7 MB**

### 逐项建议

| 文件/目录 | 体积 | 建议 | 理由 |
|-----------|------|------|------|
| `framelab/`、`tests/`、`docs/`、`assets/`、`build_tools/`、`README.md`、`requirements.txt` | ~2.8MB | ✅ **提交** | 核心源码与文档 |
| `dist\FrameLab.exe` | 51 MB | ❌ **不进 Git**，走 **GitHub Releases** | Git 不适合大二进制；Releases 提供下载 |
| `external\PresentMon\PresentMon-x64.exe` | 957 KB | ⚠️ **可提交，但需加署名** | Intel 官方构建，**MIT 许可证**，允许再分发（见 §四） |
| `external\PresentMon\PresentMon-x64.exe.amd-head.bak` | 387 KB | ❌ **删除或不提交** | 这是 **AMD Adrenalin 自带构建**，非 Intel 官方版，**许可证不明**，开源有风险 |
| `__pycache__/`（多处） | ~1MB | ❌ **gitignore** | 编译缓存，无意义 |
| `tests\_probe_freeze.py` | 4 KB | ⚠️ 确认是否保留 | 临时性能探针脚本 |
| `framelab_config.json`、`dist\framelab_config.json` | 各 0.5KB | ✅ 可提交 | 纯默认配置，**无个人隐私** |

### 推荐 `.gitignore`（可直接用）
```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/

# 打包产物（EXE 走 Releases，不进仓库）
dist/
build/
*.exe

# 构建环境（项目目录外，但保险起见）
_build_env/

# IDE / 系统
.vscode/
.idea/
*.swp
Thumbs.db
desktop.ini

# 本地敏感
*.local.json
framelab_diag.log
```

> 注意：`dist/` 整目录忽略后，`dist\framelab_config.json` 也忽略——如想让"打包后的示例配置"进仓库，需单独 `!dist/framelab_config.json` 例外，否则就在 README 里给配置模板即可。

---

## 三、敏感信息排查（已帮你扫过，结论：干净）

扫描了 `framelab/`、`tests/`、`docs/`、配置文件，**未发现**：
- 个人邮箱 / 手机号 / 微信 / Token / 密码
- 真实姓名 / 家庭 IP（192.168.x）
- 绝对路径泄露（源码里只有通用占位，无 `C:\Users\xxx` 等）

**唯一注意点**：`README.md` 第 29 行引用了外部文档 `gemini-code-1793074763581.md`（v4.1 建议书），该文件**不在仓库里**。开源后读者点不到，建议改成一句话描述或删掉引用。

> 建议：提交前再跑一次 `git grep -iE "password|token|secret|@qq\.com|@163\.com|192\.168\."` 做双保险。

---

## 四、第三方组件合规（重点：PresentMon）

| 组件 | 来源 | 许可证 | 能否随项目再分发 |
|------|------|--------|------------------|
| **PresentMon 2.6.0** | Intel 官方发布（你下载的 `PresentMon-2.6.0-x64.exe`） | **MIT** | ✅ 可以。MIT 要求**保留版权声明** |
| **PresentMon（AMD 自带版 .bak）** | AMD Adrenalin 目录里的构建 | ⚠️ 不明 | ❌ 不建议随包分发 |
| PyQt6 / pyqtgraph / numpy | pip | 均为宽松许可证 | ✅ 正常声明依赖即可 |

### PresentMon 署名做法
在仓库里加 `NOTICE` 或 `THIRD_PARTY.md`，写明：
```
PresentMon © Intel Corporation — MIT License
Source: https://github.com/GameDev-portals/PresentMon
本仓库内的 external/PresentMon/PresentMon-x64.exe 为 Intel 官方 2.6.0 发行版。
```
> PresentMon 官方仓库现位于 Intel 旗下（原 GameDev-portals/PresentMon 已迁移），MIT 条款不变。

---

## 五、开源后的维护性（建议，非阻塞）

1. **README 补全**：加「截图预览」「免责声明」「已知限制」（Windows-only、需管理员跑 ETW、PresentMon 随包但不保证所有机型）
2. **CI 可选**：GitHub Actions 跑 `python -m pytest` / 你的测试脚本（当前测试是独立脚本，非 pytest 格式，可加个入口）
3. **版本号**：当前 `1.0.4`，首次开源建议打一个 `v1.0.4` 的 **Release + Tag**
4. **Issue/Discussion**：开 Discussion 收集需求，Issue 只留 bug

---

## 六、操作步骤（Git 装好后照做）

```powershell
cd 'F:\Desktop\项目\不完美项目\VRAMWatch'

# 0) 先清理不该提交的东西（确认后再删）
rm external\PresentMon\PresentMon-x64.exe.amd-head.bak   # AMD 版备份，许可证不明

# 1) 初始化
git init
git add .
git commit -m "Initial release: FrameLab 1.0.4 (Windows stutter-diagnosis workbench)"

# 2) 加 LICENSE / .gitignore / THIRD_PARTY.md（见上）
git add LICENSE .gitignore THIRD_PARTY.md
git commit -m "Add MIT license, gitignore, third-party notices"

# 3) 配置远端并推送（<owner>/<repo> 换成你的）
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main

# 4) EXE 走 Release（不要 commit）
gh release create v1.0.4 --title "FrameLab 1.0.4" --notes "..." --asset dist/FrameLab.exe
```

---

## 七、给你的问题（可选，影响细节）

1. **署名用真名还是笔名？** 决定 README 作者栏与 Git 身份。
2. **要不要随包带 PresentMon exe？** 带=别人开箱即用（需 MIT 署名）；不带=让用户自己下（更干净）。
3. **要不要把 `tests\_probe_freeze.py` 这类临时探针保留？**
4. **许可证选 MIT 还是 Apache-2.0？**（Apache 有专利授权条款，工具类通常 MIT 即可）

---

*本清单由对仓库的实际扫描生成，非通用模板。需要我直接帮你生成 LICENSE / .gitignore / THIRD_PARTY.md 并清理仓库的话，告诉我即可。*
