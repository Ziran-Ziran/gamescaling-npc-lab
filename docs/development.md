# 开发与复现

## 离线环境

推荐 Python 3.12；依赖以项目现有环境为起点，并更新已知有安全修复的版本，不表示任意平台组合已验证。先在当前机器确认 Git、Python 路径，再创建虚拟环境并安装 `requirements/dev.txt`。控制器和 SLAM 可分别安装对应依赖文件。

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s ue5/tools -p "test_*.py" -v
python scripts/check_repository.py
git diff --check
```

这些命令只做离线验证。部分控制器测试依赖官方 `gamescaling` 包，未安装时明确 skip；SLAM 中有一项可选的真实会话回归，未提供本地数据时跳过。CI 不替代官方模型或真实数据集成测试。CI 分别运行 Windows 控制器测试与 Linux SLAM 几何测试，不编译 UE、不运行游戏或模型。

## UE5 采集开发

项目描述文件当前关联 UE 5.8。开发者需独立安装有合法授权的对应 UE 版本及 C++ 工具链。公开版只包含项目描述、Source 和三个 Default 配置，**不包含 Content 中的地图和蓝图**；原配置仍引用 First Person 模板资源，因此直接双击不能保证进入可用场景。

可用官方 First Person 模板在本地创建或补齐对应资源，或自行建立带有 Pawn 与 CameraComponent 的测试关卡，并在 Project Settings 中设置启动地图与 GameMode。保留 `GSVSLAM` 模块名称，生成工程文件后编译。自行创建场景时优先在本地覆盖配置，提交前审查与他人场景的兼容性。

采集子系统可向带相机的 Pawn 挂载采集组件，现有入口包含 F9/F10 和 `VisionRec.Start` / `VisionRec.Stop`。默认数据写入根目录 `output/ue5-datasets`，不纳入 Git。记录前核对分辨率、内参、深度编码、渲染设置与时钟；保留完整元数据以便重建。

尚未在全新克隆和空资产环境验证 UE 编译/采集。资产初始化自动化是明确的后续任务，不把缺失资源视为已解决。

## 控制器与模型

控制器说明见 [controller/README.md](../controller/README.md)。官方 `gamescaling` 客户端和模型权重需自行获取，GPU 推理环境与离线测试环境分开管理。

控制器的 WSL 发行版、解释器、客户端仓库与 Windows worker 的客户端仓库通过本机环境变量配置；具体名称、格式和迁移示例见 [控制器说明](../controller/README.md#接入前的限制)。本地后端与 cloud worker 分别检查自身需要的配置，缺失或路径格式错误时在启动推理进程前报错。保存画面的控制对照评估使用 `--official-repo` 或 `GAMESCALING_CLIENT_REPO`，路径属于运行评估的操作系统。

离线测试覆盖两套模拟机器配置、显式参数优先级、缺失/错误配置、按正确发行版清理 worker，以及真实本地子进程的多轮 JSONL 往返。不需要 WSL、模型或游戏。现有固定回环端点、精确模型身份校验和动作安全边界继续生效。真实两台电脑连接模型仍未验收；云端下载/服务辅助脚本仍有特定平台目录，需单独检查，不属于本次控制器入口配置范围。

程序不会自动加载 `.env`，应在自己的 shell 中注入环境变量。云端服务只监听回环地址，通过自己的隧道访问；不得把隧道命令或账户资料提交到仓库。
