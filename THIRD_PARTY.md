# 第三方组件声明 / Third-Party Notices

本项目（FrameLab）使用以下第三方组件。各组件版权归其各自所有者所有，
并受其各自许可证条款约束。

---

## 随包分发的二进制

### PresentMon

- **组件**：`external/PresentMon/PresentMon-x64.exe`
- **版本**：2.6.0（x64 官方发行版）
- **作者**：Intel Corporation 及其贡献者
- **许可证**：MIT License
- **来源**：https://github.com/git-for-windows/... → 官方发布页 https://github.com/GameDev-portals/PresentMon
  （PresentMon 现由 Intel 维护，仓库地址可能已迁移至 Intel 官方组织）

MIT 许可证原文：

```
MIT License

Copyright (c) Intel Corporation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> FrameLab 仅为**启动** PresentMon 并解析其 CSV 输出，不修改其代码。

---

## Python 运行时依赖（通过 pip 安装，不随包分发）

| 组件 | 许可证 | 说明 |
|------|--------|------|
| [PyQt6](https://pypi.org/project/PyQt6/) | GPL-3.0 / 商业双许可 | GUI 框架 |
| [pyqtgraph](https://pypi.org/project/pyqtgraph/) | MIT | 图表渲染 |
| [numpy](https://pypi.org/project/numpy/) | BSD-3-Clause | 数值计算 |

> **PyQt6 的 GPL-3.0 条款**：PyQt6 允许在 GPL-3.0 或商业许可下使用。
> 本项目以 **MIT** 发布。若你打算将 FrameLab 用作**闭源商业软件**，
> 需改用 PySide6（LGPL-3.0）或向 Riverbank 购买 PyQt 商业许可。

### 构建期依赖（不随包分发）

| 组件 | 许可证 |
|------|--------|
| [PyInstaller](https://pypi.org/project/pyinstaller/) | GPL-2.0+（附例外条款，允许打包任意许可的软件） |

---

## 运行时调用的系统组件

- **Windows ETW / PresentMon 驱动栈** —— Microsoft Windows 系统组件
- **AMD ADL**（`atiadlxx.dll`）、**NVIDIA NVML**（`nvml.dll`）—— 显卡厂商驱动，
  FrameLab 仅在运行时动态加载，**不分发**这些文件
