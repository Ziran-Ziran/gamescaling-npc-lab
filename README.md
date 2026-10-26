# GameScaling NPC Lab

面向游戏引擎中自主 AI-NPC 的研究与工程实验：将视觉 SLAM 的空间定位、地图记忆与 GameScaling 动作决策逐步连接，构建“观察 → 定位与建图 → 决策 → 行动 → 反馈”的闭环。

**当前阶段：感知与控制基础设施验证。** 已有 UE5 RGB-D 采集、离线几何重建与评测工具、Windows 游戏控制实验及离线测试。实时 SLAM、地图驱动决策和完整自主游玩尚未完成。本仓库是独立实验工程，与 GameScaling 上游项目没有官方隶属关系。

## 当前包含什么

| 模块 | 现有内容 | 边界 |
|---|---|---|
| UE5 采集 | C++ 采集组件、路线驱动、程序化场景与配置 | 公开版不含原项目地图和蓝图，需自行准备场景 |
| 空间感知 | RGB-D VO、COLMAP 重建、深度融合、标定检查 | 离线工具不等于实时 SLAM |
| 评测 | 轨迹、地图、地标和跟踪覆盖相关工具 | 引擎真值只用于事后评测或明确标注的 oracle 基线 |
| 动作控制 | 本地/云端模型桥接、动作过滤、监督停止与执行记录 | Windows 实验；入口路径按机器配置，完整模型集成需独立验收 |
| 协作基础 | PR/Issue 模板、CI、提交检查与安全规范 | 离线检查不证明游戏内成功率 |

## 从这里开始

只运行离线测试，不需要 GPU、游戏、UE 或模型权重。建议 Python 3.12；先在自己的电脑确认 Python 和 Git 路径。

```powershell
git clone https://github.com/Ziran-Ziran/gamescaling-npc-lab.git
cd gamescaling-npc-lab
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements/dev.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m unittest discover -s ue5/tools -p "test_*.py" -v
.venv\Scripts\python.exe scripts/check_repository.py
```

Linux/macOS 将 `.venv\Scripts\python.exe` 换为 `.venv/bin/python`。控制器的完整平台验证以 Windows 为准。依赖官方 `gamescaling` 包的测试在未安装该包时会明确跳过；不能把跳过算作已验收能力。

公开仓库不包含原始采集、模型权重、引擎安装、论文全文、游戏截图、私人日志或云端凭据。首次克隆后可立即做离线开发，完整 UE 采集和模型控制需按 [开发指南](docs/development.md) 配置。

## 目录

```text
controller/          Windows 控制、策略桥接、云端部署辅助脚本
tests/               控制器离线测试
ue5/GSVSLAM/Source/  UE5 C++ 采集、场景和路线逻辑
ue5/GSVSLAM/Config/  可共享的引擎、项目和输入配置
ue5/tools/           VO、重建、标定、融合与评测
requirements/        分层 Python 开发依赖
scripts/             提交内容检查
docs/                对外维护的工程文档
.github/             CI、责任人及协作模板
```

架构与数据边界见 [架构说明](docs/architecture.md)，后续目标见 [路线图](docs/roadmap.md)。欢迎从标为 `good first issue` 的任务开始，提交前请读 [贡献指南](CONTRIBUTING.md) 和 [上传规则](docs/repository-policy.md)。

## 上游与授权

本项目使用或计划适配 GameScaling、COLMAP、ORB-SLAM3 和 Unreal Engine；第三方代码、模型与资产各自遵守原许可证，详见 [NOTICE](NOTICE.md)。本次公开尚未为本项目原创部分授予通用开源许可证；公开可读不等于可任意再分发或商用。协作者贡献前请确认授权安排。
