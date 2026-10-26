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

## ORB-SLAM3 无真值顺序回放

适配器位于 `ue5/tools/orbslam3/`，链接开发者已有的 ORB-SLAM3、OpenCV、Eigen 和 Pangolin；不会安装依赖或替换上游库。先核实本机与 WSL 的身份和实际路径，再执行；上游库/源码/词袋不随本仓库分发，授权见 NOTICE。

```powershell
python ue5/tools/orbslam3_input.py <capture-session> <new-sensor-input>
```

预处理只从元数据提取固定独立标定、尺寸、深度单位和名义频率，输入目录只含图像对、`sensor.json`、`settings.yaml` 和 `frames.tsv`。不复制或打开真值位姿、地标，也不转交整份元数据。BGRA PNG 显式转换为 BGR，uint16 光轴毫米深度中的饱和 65535 转为无效 0，不翻转图像。缺配对或变化标定会报错。默认保留至少 40 GiB 空闲，单次输入复制上限 10 GiB。

在 Linux/WSL 中，`ORB_ROOT` 必须指向本机真实存在的构建；示例中的变量先按本机情况赋值。依赖路径需与既有库一致，避免误用另一套 Eigen。

```bash
cmake -S ue5/tools/orbslam3 -B "$BUILD_DIR" -DORB_ROOT="$ORB_ROOT" \
  -DEigen3_DIR="$EIGEN_CONFIG" -DPangolin_DIR="$PANGOLIN_CONFIG" \
  -DOpenCV_DIR="$OPENCV_CONFIG" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR" -j2
"$BUILD_DIR/gsv_rgbd" "$ORB_ROOT/Vocabulary/ORBvoc.txt" \
  "$SENSOR_INPUT/settings.yaml" "$SENSOR_INPUT" "$NEW_OUTPUT" 10
```

最后参数是回放节流 Hz，0 表示尽快按顺序处理；每次只读当前帧。旧采集的时钟是 `frame_id / target_hz`，明确属于名义时间，不是精确采样时间或端到端延迟。Viewer 与闭环均关闭，输入深度因子固定为 1000。默认虚拟基线 0.1 m、近点阈值 4 m 是初始参数；近点阈值还控制新 RGB-D 地图点创建，需要按传感器工作距离独立核查，不能根据真值误差选择。

输出 `tracking.jsonl` 包含即时状态和 `Twc`（CV 相机到原生地图，米）；仅 OK 且有限的位姿有效，失效为 null。`map_id` 来自公开的已跟踪地图点接口；每张图的原点独立。`sparse_snapshot.jsonl`、`keyframes_snapshot.jsonl` 保留图编号；单图时另存上游异步轨迹。当前本地上游 Shutdown 不等待线程，导出只称为加锁异步快照，不声称静止的最终优化图。地图导出不代表地图重载定位已验收。

原生产物完整落盘后，可单独执行：

```powershell
python ue5/tools/orbslam3_report.py <new-output> --session <capture-session>
python ue5/tools/summarize.py
```

不传 `--session` 只做原生状态统计，不读真值。传入后才读取真值作事后评分；原生产物先保存 SHA-256 并核对评分前后不变。按连续有效段分别报告覆盖率、米制刚体 ATE 与仅供评测的 Sim(3)，多图不拼接为全程轨迹。该适配器当前只提供文件回放，UE 实时通道另行验收。

## ORB 跟踪内部诊断

`orbslam3_diagnostics.py` 在新目录生成本地上游源码副本，保留上游版权头，并复制、哈希已有构建的目标文件。补丁必须精确匹配唯一锚点；源码不兼容时先失败，不修改原安装。诊断构建只重编 Tracking/LocalMapping 两个上游编译单元，加上本项目的日志组件；不修改类布局、匹配阈值或跟踪决策。须使用与原对象一致的编译器、依赖和构建参数，不把复制对象视为通用二进制。

```bash
python3 ue5/tools/orbslam3_diagnostics.py prepare "$ORB_ROOT" "$DIAGNOSTIC_SOURCES"
cmake -S ue5/tools/orbslam3 -B "$DIAGNOSTIC_BUILD" \
  -DORB_ROOT="$ORB_ROOT" -DEigen3_DIR="$EIGEN_CONFIG" \
  -DPangolin_DIR="$PANGOLIN_CONFIG" -DOpenCV_DIR="$OPENCV_CONFIG" \
  -DGSV_BUILD_DIAGNOSTICS=ON -DGSV_DIAGNOSTIC_SOURCES="$DIAGNOSTIC_SOURCES" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "$DIAGNOSTIC_BUILD" --target gsv_rgbd_diagnostics -j1
"$DIAGNOSTIC_BUILD/gsv_rgbd_diagnostics" "$ORB_ROOT/Vocabulary/ORBvoc.txt" \
  "$SENSOR_INPUT/settings.yaml" "$SENSOR_INPUT" "$NEW_OUTPUT" 10
python3 ue5/tools/orbslam3_diagnostics.py report "$NEW_OUTPUT"
```

`diagnostics.jsonl` 记录特征/有效深度、投影与参考关键帧匹配、优化后地图内点、关键帧创建/入队/处理、局部 BA、重定位候选、重置与新图事件。前台使用源帧号；后台线程的帧号为 null，按关键帧 ID 和时间戳关联，避免误记到当前帧。`diagnostic_report.json` 解释失效分支与入队等待，未观测的分支明确保留未知，不把特征总数当地图匹配内点数。

诊断会改变运行时开销和线程调度，不能拿它的速度作实时验收。日志在 Shutdown 请求后关闭，之后不再写入；本地上游不等待后台线程，runner 关闭本地输出流后结束整个独立进程，避免尚存线程与析构的同步对象竞争。地图仍是加锁异步快照，未声称最终静止或重载定位。

本地 ORB 默认可能屏蔽文字重置消息：无内部事件时报告重置次数未知，另存文字日志出现次数；只有覆盖重置入口的诊断 schema 2 才报告精确计数。旧报告里的零条重置消息不能直接当成零次重置。

## 新数据输入检查

```powershell
python ue5/tools/inspect_slam_input.py <image-directory-or-video-or-map> --out <new-report.json>
# 默认最多解码300帧；完整检查用 --max-frames 0
```

此工具只读源数据，不提取视频、不建图、不改时间、不读取 poses.jsonl/landmarks；拒绝输入及父目录的符号链接/junction。报告包括帧编号、配对、坏帧、尺寸变化、重复画面、独立标定、时钟来源和深度有效比例。`offline_ready` 只表示已完整检查的输入合同通过，不表示 SLAM 成功；局部检查为 false。退出0表示合同通过，退出2表示未就绪，原因在 errors/warnings。显式输出必须为新文件。

- RGB 图像序列或视频建议单目路线，但本项目当前 runner 仍只实现 RGB-D。视频 FPS 只支持名义回放，不证明采样时钟或米制尺度。
- RGB-D 当前检查合同为 uint8 三通道 BGR 解码、同名 uint16 光轴毫米深度，0/65535无效。BGRA/灰度明确提示需转换；UE 原始 BGRA 可先运行 `orbslam3_input.py` 准备独立输入，再检查。其他深度单位/含义需显式适配，不能猜测换算。
- 单独的 PLY/OBJ/GLB 等地图只做文件类型/大小识别，不解析几何，不判为可跟踪序列；定位还需要连续图像、相机标定和地图格式适配。

图像文件名需带数字帧号，建议 `frames/frame_000000.png`，深度同名放 `depth/`。标定从独立 `sensor.json` 或显式 `--calibration` 读取；既有 UE 元数据只提取白名单 calibration 字段，不把 start_pose 转交算法。标定字段为 fx/fy/cx/cy/w/h，工具能检查数值与尺寸，不能代替真实相机标定。

旧数据可声明 `target_hz` 和 `time_source: nominal_frame_id_over_target_hz`；这只支持名义回放。新采集若有独立的 `timestamps.jsonl`，每行使用 `{"frame_id":0,"t_sensor_s":100.0}`（秒），并在 sensor.json 声明 `time_source: sensor_capture_monotonic`。只有真实采样时记录的独立时钟才应这样声明；工具检查声明与单调性，不会独立证明物理时钟同步。不能从真值文件提取时间，也不能把容器FPS或文件保存时间冒充采样时间。

## 控制器与模型

控制器说明见 [controller/README.md](../controller/README.md)。官方 `gamescaling` 客户端和模型权重需自行获取，GPU 推理环境与离线测试环境分开管理。

现有 `backends.py`、`wsl_bridge.py`、`policy_worker.py`、`evaluate_saved.py` 保留早期实验的 WSL 默认路径，云端脚本保留特定平台目录。它们不包含登录凭据，但不能直接跨电脑复用。实际模型连接前需检查路径、发行版、上游版本和模型服务。后续应通过专门 PR 配置化，同时保持端点限制和动作安全边界。

不要把 `.env` 当成当前代码已经支持的配置机制；当前入口仍主要依赖参数及现有默认值。云端服务只监听回环地址，通过自己的隧道访问；不得把隧道命令或账户资料提交到仓库。
