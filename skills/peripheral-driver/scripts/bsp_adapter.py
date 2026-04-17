#!/usr/bin/env python
"""外设驱动 BSP 适配工具。

为 `peripheral-driver` skill 提供可执行入口，支持：

- 扫描开源驱动代码，分析适配难度
- 将开源驱动适配到项目 BSP 规范
- 生成空的 BSP 骨架文件
- 列出已记录的常见设备和推荐库
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_HAL_CALL_RE = re.compile(r"\bHAL_(I2C|SPI|UART|GPIO)_\w+")
_FUNC_SIG_RE = re.compile(
    r"^[ \t]*(?:static\s+)?(?:[\w*]+\s+)+(\w+)\s*\([^)]*\)\s*[{;]",
    re.MULTILINE,
)
_INCLUDE_RE = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
_SNAKE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")
_CAMEL_RE = re.compile(r"^[A-Z][a-zA-Z0-9]+$")

_INTERESTING_KEYWORDS = {"Init", "DeInit", "Read", "Write", "Reset", "Config",
                         "Enable", "Disable", "Start", "Stop", "Get", "Set"}

_BUS_TYPES = {"i2c", "spi", "uart", "gpio"}

_BUS_HAL_HEADER = {
    "i2c": "stm32f4xx_hal_i2c.h",
    "spi": "stm32f4xx_hal_spi.h",
    "uart": "stm32f4xx_hal_uart.h",
    "gpio": "stm32f4xx_hal_gpio.h",
}

_BUS_HANDLE_TYPE = {
    "i2c": "I2C_HandleTypeDef",
    "spi": "SPI_HandleTypeDef",
    "uart": "UART_HandleTypeDef",
    "gpio": "GPIO_TypeDef",
}


# ---------------------------------------------------------------------------
# 名称工具
# ---------------------------------------------------------------------------

def _name_lower(device: str) -> str:
    """e.g. 'BMP280' -> 'bmp280'"""
    return device.strip().lower().replace("-", "_").replace(" ", "_")


def _name_title(device: str) -> str:
    """e.g. 'bmp280' -> 'Bmp280'"""
    return _name_lower(device).replace("_", " ").title().replace(" ", "")


def _name_upper(device: str) -> str:
    """e.g. 'bmp280' -> 'BMP280'"""
    return _name_lower(device).upper()


# ---------------------------------------------------------------------------
# 扫描模式
# ---------------------------------------------------------------------------

def _collect_c_files(directory: Path) -> list[Path]:
    """Walk *directory* and return all .c / .h files."""
    result: list[Path] = []
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f.endswith((".c", ".h")):
                result.append(Path(root) / f)
    return sorted(result)


def _detect_naming_style(names: list[str]) -> str:
    snake = sum(1 for n in names if _SNAKE_RE.match(n))
    camel = sum(1 for n in names if _CAMEL_RE.match(n))
    if snake > camel:
        return "snake_case"
    if camel > snake:
        return "CamelCase"
    return "mixed"


def _detect_prefix(func_names: list[str]) -> str | None:
    """Try to find a common prefix like 'BMP280_' or 'bmp280_'."""
    prefixes: dict[str, int] = {}
    for name in func_names:
        m = re.match(r"^([A-Za-z][A-Za-z0-9]*_)", name)
        if m:
            prefixes[m.group(1)] = prefixes.get(m.group(1), 0) + 1
    if not prefixes:
        return None
    return max(prefixes, key=lambda k: prefixes[k])


def cmd_scan(directory: Path) -> int:
    """Scan C/H files and print adaptation report."""
    files = _collect_c_files(directory)
    if not files:
        print(f"❌ 在 {directory} 中未找到 .c/.h 文件")
        return 1

    all_funcs: list[str] = []
    all_hal_calls: set[str] = set()
    all_includes: set[str] = set()
    interesting_hits: list[str] = []

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _FUNC_SIG_RE.finditer(content):
            all_funcs.append(m.group(1))
        for m in _HAL_CALL_RE.finditer(content):
            all_hal_calls.add(m.group(0))
        for m in _INCLUDE_RE.finditer(content):
            all_includes.add(m.group(1))
        for kw in _INTERESTING_KEYWORDS:
            if re.search(rf"\b\w*{kw}\w*\s*\(", content):
                interesting_hits.append(f"{fpath.name}: *{kw}*")

    style = _detect_naming_style(all_funcs)
    prefix = _detect_prefix(all_funcs)

    print(f"\n📊 扫描报告: {directory}")
    print(f"  文件数量: {len(files)}")
    print(f"  函数数量: {len(all_funcs)}")
    print(f"  命名风格: {style}")
    if prefix:
        print(f"  检测到前缀: {prefix}")
    if all_hal_calls:
        print(f"  HAL 调用: {', '.join(sorted(all_hal_calls)[:10])}")
    else:
        print("  HAL 调用: 无（可能使用寄存器直接操作）")
    if all_includes:
        print(f"  依赖头文件: {', '.join(sorted(all_includes)[:10])}")

    if interesting_hits:
        print("\n  🔍 关键函数:")
        for hit in interesting_hits[:15]:
            print(f"    - {hit}")

    # Suggestions
    print("\n  💡 适配建议:")
    if all_hal_calls:
        print("    - 驱动已使用 HAL，适配难度较低")
    else:
        print("    - 驱动未使用 HAL，需要编写总线抽象层")
    if prefix:
        print(f"    - 可将前缀 '{prefix}' 替换为 'BSP_<Device>_'")
    if "main.h" not in all_includes:
        print("    - 需要添加 #include \"main.h\"")
    return 0


# ---------------------------------------------------------------------------
# 适配模式
# ---------------------------------------------------------------------------

def _ensure_include_guard(content: str, guard: str) -> str:
    if re.search(r"#ifndef\s+\w+_H", content):
        return content
    return f"#ifndef {guard}\n#define {guard}\n\n{content}\n\n#endif /* {guard} */\n"


def _ensure_extern_c(content: str) -> str:
    if "__cplusplus" in content:
        return content
    guard_end = content.rfind("#endif")
    if guard_end == -1:
        return content
    block = (
        '\n#ifdef __cplusplus\nextern "C" {\n#endif\n'
    )
    block_end = '\n#ifdef __cplusplus\n}\n#endif\n'
    return content[:guard_end] + block_end + "\n" + content[guard_end:]


def _ensure_main_h(content: str) -> str:
    if '"main.h"' in content:
        return content
    first_include = content.find("#include")
    if first_include == -1:
        return '#include "main.h"\n\n' + content
    return content[:first_include] + '#include "main.h"\n' + content[first_include:]


def _replace_prefix(content: str, old_prefix: str, new_prefix: str) -> str:
    return content.replace(old_prefix, new_prefix)


def cmd_adapt(directory: Path, device: str, handle: str, output: Path) -> int:
    """Adapt source files to BSP conventions."""
    files = _collect_c_files(directory)
    if not files:
        print(f"❌ 在 {directory} 中未找到 .c/.h 文件")
        return 1

    output.mkdir(parents=True, exist_ok=True)
    dev_lower = _name_lower(device)
    dev_title = _name_title(device)
    dev_upper = _name_upper(device)
    new_prefix = f"BSP_{dev_title}_"

    # Detect old prefix
    all_funcs: list[str] = []
    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _FUNC_SIG_RE.finditer(content):
            all_funcs.append(m.group(1))
    old_prefix = _detect_prefix(all_funcs)

    adapted_files: list[str] = []
    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # Rename prefix
        if old_prefix:
            content = _replace_prefix(content, old_prefix, new_prefix)

        # Replace global handle with static pointer pattern
        handle_re = re.compile(rf"\b{re.escape(handle)}\b")
        if handle_re.search(content):
            content = handle_re.sub(f"(*_h{dev_lower}_handle)", content)
            if f"_h{dev_lower}_handle" not in content.split("\n")[0:5]:
                decl = f"\nstatic {handle.split('.')[-1] if '.' in handle else 'void'} *_h{dev_lower}_handle;\n"
                first_func = _FUNC_SIG_RE.search(content)
                if first_func:
                    pos = first_func.start()
                    content = content[:pos] + decl + "\n" + content[pos:]

        ext = fpath.suffix
        if ext == ".h":
            out_name = f"bsp_{dev_lower}.h"
            guard = f"BSP_{dev_upper}_H"
            content = _ensure_include_guard(content, guard)
            content = _ensure_extern_c(content)
        else:
            out_name = f"bsp_{dev_lower}.c"
            content = _ensure_main_h(content)

        out_path = output / out_name
        out_path.write_text(content, encoding="utf-8")
        adapted_files.append(out_name)
        print(f"  ✅ {fpath.name} -> {out_name}")

    if not adapted_files:
        print("❌ 未成功适配任何文件")
        return 1

    # Print integration guide
    print(f"\n📋 main.c 集成指南:")
    print(f'  #include "bsp_{dev_lower}.h"')
    print(f"  // 在 MX_Init 之后调用:")
    print(f"  BSP_{dev_title}_Init(&h{handle.replace('&', '')});")
    return 0


# ---------------------------------------------------------------------------
# 骨架生成模式
# ---------------------------------------------------------------------------

def _scaffold_header(device: str, bus: str, handle: str, addr: str | None) -> str:
    dev_lower = _name_lower(device)
    dev_title = _name_title(device)
    dev_upper = _name_upper(device)
    handle_type = _BUS_HANDLE_TYPE.get(bus, "void")

    lines: list[str] = []
    lines.append(f"#ifndef BSP_{dev_upper}_H")
    lines.append(f"#define BSP_{dev_upper}_H")
    lines.append("")
    lines.append('#ifdef __cplusplus')
    lines.append('extern "C" {')
    lines.append('#endif')
    lines.append("")
    lines.append("/* Includes ----------------------------------------------------------*/")
    lines.append("#include <stdint.h>")
    lines.append("#include <stdbool.h>")
    hal_hdr = _BUS_HAL_HEADER.get(bus, "stm32f4xx_hal.h")
    lines.append(f'#include "{hal_hdr}"')
    lines.append("")
    lines.append("/* Exported macros ---------------------------------------------------*/")
    if addr and bus == "i2c":
        lines.append(f"#define BSP_{dev_upper}_I2C_ADDR  ({addr})")
    lines.append(f"#define BSP_{dev_upper}_TIMEOUT  (100U)")
    lines.append("")
    lines.append("/* Exported types ----------------------------------------------------*/")
    lines.append(f"#define BSP_{dev_upper}_OK    (0)")
    lines.append(f"#define BSP_{dev_upper}_ERR   (-1)")
    lines.append("")
    lines.append(f"typedef struct {{")
    lines.append(f"    {handle_type} *h{bus};")
    lines.append(f"    uint8_t  initialized;")
    lines.append(f"}} BSP_{dev_title}_Handle_t;")
    lines.append("")
    lines.append("/* Exported functions ------------------------------------------------*/")
    lines.append(f"int BSP_{dev_title}_Init(BSP_{dev_title}_Handle_t *dev, {handle_type} *h{bus});")
    lines.append(f"int BSP_{dev_title}_DeInit(BSP_{dev_title}_Handle_t *dev);")
    lines.append(f"int BSP_{dev_title}_ReadData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, uint8_t *buf, uint16_t len);")
    lines.append(f"int BSP_{dev_title}_WriteData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, const uint8_t *buf, uint16_t len);")
    lines.append("")
    lines.append("#ifdef __cplusplus")
    lines.append("}")
    lines.append("#endif")
    lines.append("")
    lines.append(f"#endif /* BSP_{dev_upper}_H */")
    lines.append("")
    return "\n".join(lines)


def _scaffold_source_i2c(device: str) -> str:
    dev_lower = _name_lower(device)
    dev_title = _name_title(device)
    dev_upper = _name_upper(device)
    return textwrap.dedent(f"""\
        #include "bsp_{dev_lower}.h"
        #include "main.h"

        /* Private macros -----------------------------------------------------*/
        /* Private variables --------------------------------------------------*/
        /* Public functions ---------------------------------------------------*/

        int BSP_{dev_title}_Init(BSP_{dev_title}_Handle_t *dev, I2C_HandleTypeDef *hi2c)
        {{
            if (!dev || !hi2c) return BSP_{dev_upper}_ERR;
            dev->hi2c = hi2c;
            /* Check device presence */
            if (HAL_I2C_IsDeviceReady(hi2c, BSP_{dev_upper}_I2C_ADDR << 1, 3, BSP_{dev_upper}_TIMEOUT) != HAL_OK) {{
                return BSP_{dev_upper}_ERR;
            }}
            dev->initialized = 1;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_DeInit(BSP_{dev_title}_Handle_t *dev)
        {{
            if (!dev) return BSP_{dev_upper}_ERR;
            dev->initialized = 0;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_ReadData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, uint8_t *buf, uint16_t len)
        {{
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            /* TODO: implement register read */
            if (HAL_I2C_Mem_Read(dev->hi2c, BSP_{dev_upper}_I2C_ADDR << 1, reg, I2C_MEMADD_SIZE_8BIT, buf, len, BSP_{dev_upper}_TIMEOUT) != HAL_OK) {{
                return BSP_{dev_upper}_ERR;
            }}
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_WriteData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, const uint8_t *buf, uint16_t len)
        {{
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            /* TODO: implement register write */
            if (HAL_I2C_Mem_Write(dev->hi2c, BSP_{dev_upper}_I2C_ADDR << 1, reg, I2C_MEMADD_SIZE_8BIT, (uint8_t *)buf, len, BSP_{dev_upper}_TIMEOUT) != HAL_OK) {{
                return BSP_{dev_upper}_ERR;
            }}
            return BSP_{dev_upper}_OK;
        }}

        /* Private functions --------------------------------------------------*/
    """)


def _scaffold_source_spi(device: str) -> str:
    dev_lower = _name_lower(device)
    dev_title = _name_title(device)
    dev_upper = _name_upper(device)
    return textwrap.dedent(f"""\
        #include "bsp_{dev_lower}.h"
        #include "main.h"

        /* Private macros -----------------------------------------------------*/
        /* Private variables --------------------------------------------------*/
        /* Public functions ---------------------------------------------------*/

        int BSP_{dev_title}_Init(BSP_{dev_title}_Handle_t *dev, SPI_HandleTypeDef *hspi)
        {{
            if (!dev || !hspi) return BSP_{dev_upper}_ERR;
            dev->hspi = hspi;
            /* TODO: configure CS pin, read WHO_AM_I register */
            dev->initialized = 1;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_DeInit(BSP_{dev_title}_Handle_t *dev)
        {{
            if (!dev) return BSP_{dev_upper}_ERR;
            dev->initialized = 0;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_ReadData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, uint8_t *buf, uint16_t len)
        {{
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            uint8_t tx = reg | 0x80; /* Read bit */
            /* TODO: assert CS */
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_RESET);
            HAL_SPI_Transmit(dev->hspi, &tx, 1, BSP_{dev_upper}_TIMEOUT);
            HAL_SPI_Receive(dev->hspi, buf, len, BSP_{dev_upper}_TIMEOUT);
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_SET);
            /* TODO: deassert CS */
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_WriteData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, const uint8_t *buf, uint16_t len)
        {{
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            uint8_t tx = reg & 0x7F; /* Write bit */
            /* TODO: assert CS */
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_RESET);
            HAL_SPI_Transmit(dev->hspi, &tx, 1, BSP_{dev_upper}_TIMEOUT);
            HAL_SPI_Transmit(dev->hspi, (uint8_t *)buf, len, BSP_{dev_upper}_TIMEOUT);
            HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_SET);
            /* TODO: deassert CS */
            return BSP_{dev_upper}_OK;
        }}

        /* Private functions --------------------------------------------------*/
    """)


def _scaffold_source_uart(device: str) -> str:
    dev_lower = _name_lower(device)
    dev_title = _name_title(device)
    dev_upper = _name_upper(device)
    return textwrap.dedent(f"""\
        #include "bsp_{dev_lower}.h"
        #include "main.h"

        /* Private macros -----------------------------------------------------*/
        /* Private variables --------------------------------------------------*/
        /* Public functions ---------------------------------------------------*/

        int BSP_{dev_title}_Init(BSP_{dev_title}_Handle_t *dev, UART_HandleTypeDef *huart)
        {{
            if (!dev || !huart) return BSP_{dev_upper}_ERR;
            dev->huart = huart;
            dev->initialized = 1;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_DeInit(BSP_{dev_title}_Handle_t *dev)
        {{
            if (!dev) return BSP_{dev_upper}_ERR;
            dev->initialized = 0;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_ReadData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, uint8_t *buf, uint16_t len)
        {{
            (void)reg;
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            /* TODO: implement UART receive protocol */
            if (HAL_UART_Receive(dev->huart, buf, len, BSP_{dev_upper}_TIMEOUT) != HAL_OK) {{
                return BSP_{dev_upper}_ERR;
            }}
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_WriteData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, const uint8_t *buf, uint16_t len)
        {{
            (void)reg;
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            /* TODO: implement UART transmit protocol */
            if (HAL_UART_Transmit(dev->huart, (uint8_t *)buf, len, BSP_{dev_upper}_TIMEOUT) != HAL_OK) {{
                return BSP_{dev_upper}_ERR;
            }}
            return BSP_{dev_upper}_OK;
        }}

        /* Private functions --------------------------------------------------*/
    """)


def _scaffold_source_gpio(device: str) -> str:
    dev_lower = _name_lower(device)
    dev_title = _name_title(device)
    dev_upper = _name_upper(device)
    return textwrap.dedent(f"""\
        #include "bsp_{dev_lower}.h"
        #include "main.h"

        /* Private macros -----------------------------------------------------*/
        /* Private variables --------------------------------------------------*/
        /* Public functions ---------------------------------------------------*/

        int BSP_{dev_title}_Init(BSP_{dev_title}_Handle_t *dev, GPIO_TypeDef *hgpio)
        {{
            if (!dev || !hgpio) return BSP_{dev_upper}_ERR;
            dev->hgpio = hgpio;
            dev->initialized = 1;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_DeInit(BSP_{dev_title}_Handle_t *dev)
        {{
            if (!dev) return BSP_{dev_upper}_ERR;
            dev->initialized = 0;
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_ReadData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, uint8_t *buf, uint16_t len)
        {{
            (void)reg; (void)len;
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            /* TODO: implement GPIO read */
            buf[0] = (uint8_t)HAL_GPIO_ReadPin(dev->hgpio, GPIO_PIN_0);
            return BSP_{dev_upper}_OK;
        }}

        int BSP_{dev_title}_WriteData(BSP_{dev_title}_Handle_t *dev, uint8_t reg, const uint8_t *buf, uint16_t len)
        {{
            (void)reg; (void)len;
            if (!dev || !dev->initialized) return BSP_{dev_upper}_ERR;
            /* TODO: implement GPIO write */
            HAL_GPIO_WritePin(dev->hgpio, GPIO_PIN_0, (GPIO_PinState)buf[0]);
            return BSP_{dev_upper}_OK;
        }}

        /* Private functions --------------------------------------------------*/
    """)


_SCAFFOLD_SOURCE_FN = {
    "i2c": _scaffold_source_i2c,
    "spi": _scaffold_source_spi,
    "uart": _scaffold_source_uart,
    "gpio": _scaffold_source_gpio,
}


def cmd_scaffold(device: str, bus: str, handle: str, addr: str | None, output: Path) -> int:
    """Generate BSP skeleton files."""
    if bus not in _BUS_TYPES:
        print(f"❌ 不支持的总线类型: {bus}（支持: {', '.join(sorted(_BUS_TYPES))}）")
        return 1

    output.mkdir(parents=True, exist_ok=True)
    dev_lower = _name_lower(device)

    # Header
    header_content = _scaffold_header(device, bus, handle, addr)
    header_path = output / f"bsp_{dev_lower}.h"
    header_path.write_text(header_content, encoding="utf-8")
    print(f"  ✅ 生成 {header_path}")

    # Source
    source_fn = _SCAFFOLD_SOURCE_FN.get(bus)
    if source_fn:
        source_content = source_fn(device)
        source_path = output / f"bsp_{dev_lower}.c"
        source_path.write_text(source_content, encoding="utf-8")
        print(f"  ✅ 生成 {source_path}")

    print(f"\n📋 骨架文件已生成到 {output}")
    print(f"  下一步: 填写 TODO 标记处的设备特定逻辑")
    return 0


# ---------------------------------------------------------------------------
# 列出设备模式
# ---------------------------------------------------------------------------

def cmd_list_devices() -> int:
    """Parse device-adaptation.md and list known devices."""
    md_path = _SCRIPT_DIR / ".." / "references" / "device-adaptation.md"
    md_path = md_path.resolve()
    if not md_path.exists():
        print(f"❌ 未找到设备适配文档: {md_path}")
        return 1

    try:
        content = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"❌ 无法读取文件: {exc}")
        return 1

    # Parse markdown table rows: | col1 | col2 | ... |
    table_re = re.compile(r"^\|(.+)\|$", re.MULTILINE)
    rows = table_re.findall(content)
    if len(rows) < 2:
        print("⚠️ 未在文档中找到设备表格")
        return 1

    # Skip header separator row (contains ---)
    data_rows = [r for r in rows if not re.match(r"^[\s\-:|]+$", r)]
    if not data_rows:
        print("⚠️ 设备表格为空")
        return 1

    print("📋 已记录的设备列表：")
    header = data_rows[0]
    cols = [c.strip() for c in header.split("|")]
    print(f"  {' | '.join(cols)}")
    print(f"  {'-' * 60}")
    for row in data_rows[1:]:
        cols = [c.strip() for c in row.split("|")]
        print(f"  {' | '.join(cols)}")

    print(f"\n  共 {len(data_rows) - 1} 个设备")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="外设驱动 BSP 适配工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              %(prog)s --scan ./driver_src
              %(prog)s --adapt ./driver_src --device bmp280 --handle hi2c1 --output ./bsp
              %(prog)s --scaffold --device bmp280 --bus i2c --handle hi2c1 --addr 0x76 --output ./bsp
              %(prog)s --list-devices
        """),
    )
    parser.add_argument("--scan", metavar="DIR", help="扫描目录中的 C/H 文件并分析适配难度")
    parser.add_argument("--adapt", metavar="DIR", help="将目录中的驱动文件适配到 BSP 规范")
    parser.add_argument("--scaffold", action="store_true", help="生成空的 BSP 骨架文件")
    parser.add_argument("--list-devices", action="store_true", help="列出已记录的常见设备")
    parser.add_argument("--device", help="设备名称（如 bmp280, mpu6050）")
    parser.add_argument("--bus", choices=sorted(_BUS_TYPES), help="总线类型: i2c, spi, uart, gpio")
    parser.add_argument("--handle", help="HAL 句柄名称（如 hi2c1, hspi1）")
    parser.add_argument("--addr", help="I2C 设备地址（如 0x76）")
    parser.add_argument("--output", metavar="DIR", help="输出目录")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # --scan
    if args.scan:
        return cmd_scan(Path(args.scan).resolve())

    # --adapt
    if args.adapt:
        if not args.device:
            print("❌ --adapt 需要 --device 参数")
            return 1
        if not args.handle:
            print("❌ --adapt 需要 --handle 参数")
            return 1
        if not args.output:
            print("❌ --adapt 需要 --output 参数")
            return 1
        return cmd_adapt(
            Path(args.adapt).resolve(),
            args.device,
            args.handle,
            Path(args.output).resolve(),
        )

    # --scaffold
    if args.scaffold:
        if not args.device:
            print("❌ --scaffold 需要 --device 参数")
            return 1
        if not args.bus:
            print("❌ --scaffold 需要 --bus 参数")
            return 1
        if not args.handle:
            print("❌ --scaffold 需要 --handle 参数")
            return 1
        if not args.output:
            print("❌ --scaffold 需要 --output 参数")
            return 1
        return cmd_scaffold(
            args.device, args.bus, args.handle, args.addr,
            Path(args.output).resolve(),
        )

    # --list-devices
    if args.list_devices:
        return cmd_list_devices()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
