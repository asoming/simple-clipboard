# Simple Clipboard · 剪贴板

本地桌面剪贴板管理器 · 当前版本 0.2.0 · GPL-3.0

简洁的本地剪贴板工具，支持文字、HTML 和静态图片。已验证 Ubuntu 22.04 / GNOME / X11；Windows 与 macOS 按后续阶段推进。

![浅色界面，使用自造示例](preview-light.png)

## 启动

在 Ubuntu 22.04 安装系统依赖后运行（需要 X11 桌面会话）：

```bash
sudo apt install python3-pyqt5 python3-gi libx11-6 libxtst6
git clone https://github.com/asoming/simple-clipboard.git
cd simple-clipboard
bash start.sh
```

默认 **Ctrl+Alt+V** 打开或关闭面板，重复启动会打开已运行的窗口。

启动脚本使用 `/usr/bin/python3` 和系统 PyQt5。`requirements.txt` 供开发参考；仅在虚拟环境安装依赖不会改变启动脚本使用的解释器。

0.2.0 会通过数据库事务升级旧版文本历史和收藏；升级失败会回滚，不自动重建数据库。新版数据库不能用 0.1.x 打开。

## 日常操作

1. 在其他应用中复制文字、网页选中内容或图片。
2. 按 **Ctrl+Alt+V**，搜索关键词或切换「全部 / 文本 / 图片 / 收藏」。
3. 用 **↑ / ↓** 选择，按 **Enter** 粘贴。

| 操作 | 方法 |
|---|---|
| 原格式 / 纯文本 | 底部「粘贴格式」，同时影响「仅复制」和「粘贴」 |
| 仅复制 | Ctrl+Enter，或底部「仅复制」 |
| 关闭面板 | Esc；不改变剪贴板 |
| 收藏 / 取消收藏 | Ctrl+D，或底部按钮 |
| 完整文本、图片预览 | Ctrl+Space，或「预览」；图片支持原始尺寸滚动查看 |
| 修改收藏名称、删除 | 右键记录；Ctrl+Delete 删除当前记录 |
| 暂停、忽略下一次复制 | 右上角「···」 |
| 快捷键、外观、自启动、保存规则 | 「··· → 设置」 |
| 存储与隐私说明、清空历史、退出 | 「···」菜单 |

### 格式范围

- **原格式：**保存文字及来源提供的 HTML，或静态图片；不承诺保留 RTF、办公应用专有对象和网页全部排版。HTML 以纯文本预览，避免加载外部网页资源。
- **纯文本：**仅提供原有文字，去掉来源 HTML；目标编辑器仍可沿用光标处的样式。每次重新打开面板默认「原格式」。
- **图片：**转为 PNG 保存，生成小缩略图；保留像素和颜色配置，归一化作者、DPI 等元数据。不做 OCR，不搜索图片中的文字；可收藏命名后按名称搜索。
- 图片最多 2400 万像素、任一边不超过 16000 像素，并受单条字节上限约束。动画和文件复制不在支持范围内。

### 终端

识别到 GNOME Terminal 等终端时，单行文本使用 Ctrl+Shift+V，应用不发送 Enter。

多行文本只复制并提示手动粘贴，因为文本中的换行也可能执行命令。图片不会自动粘贴到已识别的终端。编辑器内嵌终端和网页终端不能仅凭外层窗口可靠识别，请使用「仅复制」。

自动粘贴依赖目标应用支持对应格式和快捷键。无法确认原窗口焦点时，显示手动粘贴提示；发送快捷键不等于目标应用已接收内容。

## 保存与设置

- 数据位于本文件夹 `data/history.sqlite3`，文件权限为当前用户读写。
- 普通历史默认保留 **7 天、最多 500 条**；收藏不自动删除。
- 默认总内容容量 **100 MiB**，单条上限 **10 MiB**；设置可调整。容量包含文字、HTML、PNG 和缩略图，数据库索引额外占用空间，界面同时显示文件实际大小。
- 保存新规则会立即清理超限普通历史；不能把总容量降到收藏占用以下。降低单条上限只限制后续复制。
- 外观默认跟随系统，也可选择浅色或深色。
- **登录自启动默认关闭**。启用后写入 `$XDG_CONFIG_HOME/autostart/codex-clipboard.desktop`（默认 `~/.config/autostart/`）。移动应用目录后，需要重新设置启动项。未实测重登桌面。
- 自启动目录不可写时，界面会明确提示失败；保存规则和外观仍会生效。若运行环境限制系统目录写入，请在有权限的桌面会话中运行后设置。

## 隐私边界

不上传、不登录、不记录正文或搜索词日志。本地历史未加密，不承诺自动识别所有密码。尊重受支持的敏感 MIME 标记，但来源应用可能不提供标记；复制敏感内容前可以暂停或忽略下一次复制。

首次启动不采集已有剪贴板；暂停后不采集，恢复时不补录，暂停状态跨重启保留。暂停或清空会取消未完成的记录任务。

删除历史不等于清空系统剪贴板。清空对话框默认保留收藏，并提供独立的「同时清空系统当前剪贴板」选项。普通删除不宣称是安全擦除。

应用排除名单尚未实现：还没有完成可靠的来源识别验证，不能把当前前台应用当作剪贴板来源。

**分享或打包时请排除整个 `data/` 目录。** 数据目录与源码放在一起是当前便携运行方式。

## 验收状态

详见 [第二阶段验收报告](docs/第二阶段验收报告.md) 和 [PRD](docs/剪贴板应用-PRD.md)。

已测 Chrome、Edge 的文字、HTML、纯文本和 PNG 往返，GNOME Terminal 单行粘贴及多行保护、当前 IBus 真实拼音输入，以及独立 Qt 编辑器。微信文字复制和中文搜索修复已获用户确认；微信图片粘贴、日常截图流程仍待用户试用。

当前不支持 Wayland、Windows 或 macOS；按第三阶段分别实现并验收。

开发过程和验收规则见 [AGENTS.md](AGENTS.md)。

## 测试

数据测试不接触桌面：

```bash
cd simple-clipboard
QT_QPA_PLATFORM=offscreen PYTHONPATH=. /usr/bin/python3 -m unittest discover -s tests -p test_store.py -v
QT_QPA_PLATFORM=offscreen PYTHONPATH=. /usr/bin/python3 -m unittest discover -s tests -p test_phase2.py -v
```

GitHub Actions 在 Ubuntu 22.04 的 Xvfb 独立显示服务器上运行全套测试。

本机全套测试必须在独立 X11 服务器上运行。**不要把隔离开关用于日常桌面。** 不同桌面测试不要共用一个显示服务器同时运行。自动测试隔离实际输入法；真实中文输入法另行在空白窗口验证。

```bash
# 在单独终端启动，若编号占用则选择其他编号：
Xephyr :100 -ac -screen 1100x850 -noreset -nolisten tcp

# 另一个终端运行：
DISPLAY=:100 QT_IM_MODULE=compose CLIPBOARD_ISOLATED_TEST=1 QT_QPA_PLATFORM=xcb PYTHONPATH=. \
  /usr/bin/python3 -m unittest discover -s tests -v
```

## 开发结构

- `store.py`：SQLite、事务升级、轻量列表查询、收藏、搜索和清理。
- `content.py`：MIME 采集、图片归一化、缩略图和输出格式。
- `monitor.py`：监听、后台处理、队列上限、暂停和取消。
- `ui.py`：搜索面板、预览、设置和操作菜单。
- `preferences.py`：可选自启动、系统外观读取与变化通知。
- `x11.py`：全局快捷键、焦点检查和粘贴按键。
- `input_method.py`：IBus 门户兼容处理。
- `instance.py` / `__main__.py`：内核单实例锁、启动与数据目录。

使用系统 Python 3.10、PyQt5 5.15.6 / Qt 5.15.3、libX11、libXtst 进行了本机验收。仓库中的预览图和测试内容均为自造示例。

## 许可证

本项目采用 **GNU GPL v3.0（GPL-3.0-only）**，见 [LICENSE](LICENSE)。

PyQt5 采用 GPLv3 / 商业双许可，见 [Riverbank 官方说明](https://www.riverbankcomputing.com/software/pyqt)。第三方依赖保留各自许可证；此仓库不附带第三方二进制。
