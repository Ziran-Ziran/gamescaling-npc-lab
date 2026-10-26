# Windows 游戏控制实验

该模块将游戏画面送给 GameScaling 策略客户端，过滤模型建议并记录实际执行反馈。它是既有实验基础设施；当前尚未与 UE 的 SLAM 地图接成完整 NPC 决策闭环。

## 离线开发

在仓库根目录安装 `requirements/controller.txt` 后运行：

```powershell
python -m unittest discover -s tests -v
```

这些测试不启动模型或操作游戏。依赖官方 `gamescaling` 包的测试未满足依赖时明确跳过。

## 组件

| 文件 | 作用 |
|---|---|
| `automatic.py` | 自动启动编排与有限会话入口 |
| `bounded_session.py` | 参数校验、限时、停止与会话记录 |
| `run_agent.py` | 检查、截图、影子、校准与探索模式 |
| `control_core.py` | 动作校验、WASD 过滤、分段执行与释放 |
| `windows_adapter.py` | 客户区截图、窗口与权限检查、输入和急停 |
| `supervisor.py` | 独立线程监督失焦、急停和时限 |
| `wsl_bridge.py` | 有超时的 JSONL 桥接和进程清理 |
| `policy_worker.py` | 策略推理，建议动作与实际执行历史分离 |
| `backends.py` | 受限本地/云端回环端点与模型身份检查 |
| `evaluate_saved.py` | 已保存画面的受控评估 |
| `telemetry.py` | GPU 采样记录 |
| `cloud/` | 特定云平台的下载、预检和服务辅助脚本 |

## 接入前的限制

启动推理前，先在当前终端配置本机路径。WSL 本地后端需要前三项，Windows 运行的 cloud worker 只需要第四项；保存画面的控制对照评估另用第五项。

| 环境变量 | 用途与路径格式 |
|---|---|
| `GAMESCALING_WSL_DISTRO` | 本机实际安装的 WSL 发行版名称 |
| `GAMESCALING_WSL_CLIENT_REPO` | WSL 内客户端仓库的绝对 Linux 路径 |
| `GAMESCALING_WSL_PYTHON` | WSL 内 Python 解释器的绝对 Linux 路径 |
| `GAMESCALING_WINDOWS_CLIENT_REPO` | Windows worker 可读的客户端仓库，使用绝对盘符路径或 UNC 路径 |
| `GAMESCALING_CLIENT_REPO` | 控制对照评估使用的客户端仓库，使用运行评估程序的操作系统路径；`--official-repo` 优先于此变量 |

PowerShell 示例仅演示格式，请先核实并替换为自己机器的值：

```powershell
$env:GAMESCALING_WSL_DISTRO = 'Ubuntu'
$env:GAMESCALING_WSL_CLIENT_REPO = '/home/developer/src/GameScaling'
$env:GAMESCALING_WSL_PYTHON = '/home/developer/venvs/game/bin/python'
$env:GAMESCALING_WINDOWS_CLIENT_REPO = 'D:\clients\GameScaling'
```

在 WSL 里执行评估时，按实际路径配置，例如：

```bash
export GAMESCALING_CLIENT_REPO=/home/developer/src/GameScaling
python -m controller.evaluate_saved --help
```

环境变量在创建桥接时读取，修改后无需重新导入模块。程序级 `WSLBridge(distro=..., repo=..., python=...)` 显式参数优先于环境变量；空的显式值会报错，不会悄悄回退。`.env` 文件不会自动加载。缺失、空白或路径格式错误会提前报出变量名称，不会启动推理进程；检查只验证配置格式，目录存在、解释器依赖与服务可用性仍需在目标机器验证。

旧版本依赖默认个人目录的启动方式需要迁移到上述显式配置。GPU 推理运行时、官方客户端、权重、云机器、隧道与凭据仍需独立准备，仓库不包含这些资源。

现有端点固定为本地回环地址，不应为接入方便直接去掉校验或开放公网服务。云端服务脚本含 `--trust-remote-code`，正式运行前应审查所选上游版本及远程代码。

完成环境适配后，先进行无输入检查与影子模式。任何真实输入实验都需要独立授权，并在现场验证权限兼容、实体急停、失焦停止、超时和按键释放。不要把本次仓库初始化当成运行游戏输入的授权。

## 结果解释

程序状态成功只代表对应流程结束。已发送输入数不代表游戏接受动作或任务完成；模型建议不等于已执行历史。真实历史仅应提交确认完成的动作，过期或部分执行必须独立记录。

本地输出默认位于忽略的 `output/local-control` 下，可能包括截图、原始模型输出、窗口信息和运行日志。分享时先脱敏并审查内容，不直接上传整包。

跨电脑配置与 JSONL 通信已有离线回归测试；独立上游依赖记录和真实两台机器的完整模型集成仍需验收。详见 [开发指南](../docs/development.md) 和 [路线图](../docs/roadmap.md)。
