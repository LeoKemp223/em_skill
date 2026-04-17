# 工具探测参考

当某个 skill 需要定位宿主工具时，优先复用本文逻辑，避免在多个地方重复写同一套规则。

## 构建工具

面向 CMake 工作流的探测顺序：

1. `cmake`
2. 如果宿主 shell 无法解析 `cmake`，则在 Windows 上尝试 `cmake.exe`

生成器优先级：

1. 若存在 `ninja`，优先使用 `Ninja`
2. 若宿主已安装 `make`、`gmake` 或平台等价工具，则退回原生 Makefile
3. 若两者都不可用，则阻塞并报告缺少生成器支持

## 烧录与调试工具

OpenOCD 查找顺序：

1. 用户或工程画像中显式给出的路径
2. `openocd`
3. Windows 下的 `openocd.exe`

ARM MCU 目标的 GDB 查找顺序：

1. 用户或工程画像中显式给出的路径
2. `arm-none-eabi-gdb`
3. `gdb-multiarch`
4. 若仍未找到，则阻塞并报告缺失调试器，不要回退到宿主专用 `gdb`

## 串口工具

首选探测顺序：

1. `python -m serial.tools.miniterm`
2. Unix-like 宿主上已安装的终端工具，例如 `picocom` 或 `screen`
3. 若都不存在，则阻塞并报告缺失串口监视依赖

## 探针与配置线索

按以下顺序寻找 OpenOCD 配置线索：

1. 用户显式提供的配置列表
2. 已存在的 `Project Profile`
3. 仓库中命名为 `openocd*.cfg` 的文件
4. IDE 启动配置，例如 `.vscode/launch.json`
5. 工作区内的厂商文档

如果检查后仍有多个合理配置路径，应返回 `ambiguous-context`。
