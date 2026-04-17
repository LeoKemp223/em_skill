#!/usr/bin/env python
"""VISA 仪器调试工具。

为 `visa-debug` skill 提供可重复调用的执行入口，支持：

- 探测 PyVISA 环境和 VISA 资源
- 查询仪器标识 (*IDN?)
- 发送 SCPI 查询/写入命令
- 读取测量值
- 捕获示波器波形数据
- 捕获仪器屏幕截图
- 持续监控 SCPI 查询值变化
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Any

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

try:
    import pyvisa
    HAS_PYVISA = True
    PYVISA_VERSION = pyvisa.__version__
except ImportError:
    HAS_PYVISA = False
    PYVISA_VERSION = None


@dataclass
class VISAResult:
    status: str  # success, failure, timeout
    summary: str
    mode: str | None = None
    connection: str | None = None
    response: str | None = None
    values: list[float] = field(default_factory=list)
    output_file: str | None = None
    failure_category: str | None = None
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 连接管理
# ---------------------------------------------------------------------------

def open_resource(resource: str, timeout_ms: int, backend: str | None) -> tuple[Any, str | None]:
    try:
        rm = pyvisa.ResourceManager(backend or "")
        inst = rm.open_resource(resource)
        inst.timeout = timeout_ms
        return inst, None
    except Exception as e:
        return None, str(e)


def check_scpi_error(inst) -> str | None:
    try:
        err = inst.query(":SYST:ERR?").strip()
        if not err.startswith("0,") and not err.startswith("+0,"):
            return err
    except Exception:
        pass
    return None


def classify_error(e: Exception) -> str:
    s = str(e).lower()
    if "timeout" in s or "timed out" in s:
        return "timeout"
    if "not found" in s or "could not open" in s or "connection" in s:
        return "connection-failure"
    if "command error" in s or "undefined header" in s:
        return "command-error"
    return "connection-failure"


def parse_ieee_block(raw: bytes) -> bytes:
    if not raw or raw[0:1] != b"#":
        return raw
    n = int(raw[1:2])
    if n == 0:
        return raw[2:]
    length = int(raw[2:2 + n])
    return raw[2 + n:2 + n + length]


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------

def detect_env(backend: str | None) -> VISAResult:
    print(f"\n📊 VISA 调试环境探测：")
    print(f"  ✅ pyvisa {PYVISA_VERSION}")
    try:
        rm = pyvisa.ResourceManager(backend or "")
        print(f"  后端: {rm.visalib}")
        resources = rm.list_resources()
        if resources:
            print(f"\n  发现 {len(resources)} 个 VISA 资源:")
            for r in resources:
                print(f"    - {r}")
        else:
            print(f"\n  未发现 VISA 资源")
        rm.close()
        return VISAResult(status="success", summary=f"发现 {len(resources)} 个资源", mode="detect")
    except Exception as e:
        print(f"  ⚠️ 后端初始化: {e}")
        return VISAResult(status="success", summary=f"pyvisa {PYVISA_VERSION}（后端受限）",
                          mode="detect", evidence=[str(e)])


# ---------------------------------------------------------------------------
# IDN / 查询 / 写入
# ---------------------------------------------------------------------------

def query_idn(inst, resource: str) -> VISAResult:
    try:
        idn = inst.query("*IDN?").strip()
        print(f"  🔖 {idn}")
        return VISAResult(status="success", summary=idn, mode="idn", connection=resource, response=idn)
    except Exception as e:
        cat = classify_error(e)
        return VISAResult(status="failure", summary=str(e), mode="idn", failure_category=cat, evidence=[str(e)])


def send_query(inst, command: str, resource: str) -> VISAResult:
    try:
        resp = inst.query(command).strip()
        print(f"  📥 {resp}")
        err = check_scpi_error(inst)
        if err:
            return VISAResult(status="failure", summary=f"SCPI 错误: {err}", mode="query",
                              connection=resource, response=resp, failure_category="command-error", evidence=[err])
        return VISAResult(status="success", summary=f"查询成功", mode="query", connection=resource, response=resp)
    except Exception as e:
        cat = classify_error(e)
        return VISAResult(status="failure", summary=str(e), mode="query", failure_category=cat, evidence=[str(e)])


def send_write(inst, command: str, resource: str) -> VISAResult:
    try:
        inst.write(command)
        print(f"  📤 已发送: {command}")
        err = check_scpi_error(inst)
        if err:
            return VISAResult(status="failure", summary=f"SCPI 错误: {err}", mode="write",
                              connection=resource, failure_category="command-error", evidence=[err])
        return VISAResult(status="success", summary=f"写入成功: {command}", mode="write", connection=resource)
    except Exception as e:
        cat = classify_error(e)
        return VISAResult(status="failure", summary=str(e), mode="write", failure_category=cat, evidence=[str(e)])


def read_register(inst, command: str, resource: str) -> VISAResult:
    try:
        resp = inst.query(command).strip()
        vals = []
        for part in resp.replace(";", ",").split(","):
            part = part.strip()
            if part:
                try:
                    vals.append(float(part))
                except ValueError:
                    pass
        if vals:
            print(f"  📊 {', '.join(f'{v:g}' for v in vals)}")
            return VISAResult(status="success", summary=f"读取 {len(vals)} 个值",
                              mode="read-register", connection=resource, response=resp, values=vals)
        print(f"  📥 {resp}")
        return VISAResult(status="success", summary=resp, mode="read-register", connection=resource, response=resp)
    except Exception as e:
        cat = classify_error(e)
        return VISAResult(status="failure", summary=str(e), mode="read-register",
                          failure_category=cat, evidence=[str(e)])


# ---------------------------------------------------------------------------
# 波形捕获
# ---------------------------------------------------------------------------

def capture_waveform(inst, resource: str, output: str, wav_fmt: str, channel: str) -> VISAResult:
    try:
        inst.write(f":WAV:SOUR {channel}")
        if wav_fmt == "ascii":
            inst.write(":WAV:MODE NORM")
            inst.write(":WAV:FORM ASC")
            raw = inst.query(":WAV:DATA?")
            vals = [float(v) for v in raw.strip().split(",") if v.strip()]
        else:
            inst.write(":WAV:MODE NORM")
            inst.write(":WAV:FORM BYTE")
            inst.write(":WAV:DATA?")
            raw_bytes = inst.read_raw()
            data = parse_ieee_block(raw_bytes)
            try:
                pre = inst.query(":WAV:PRE?").strip().split(",")
                y_inc = float(pre[7]) if len(pre) > 7 else 1.0
                y_orig = float(pre[8]) if len(pre) > 8 else 0.0
                y_ref = float(pre[9]) if len(pre) > 9 else 0.0
            except Exception:
                y_inc, y_orig, y_ref = 1.0, 0.0, 0.0
            vals = [(b - y_ref) * y_inc + y_orig for b in data]

        out = output or f"waveform_{channel}.csv"
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index", "value"])
            for i, v in enumerate(vals):
                w.writerow([i, v])

        print(f"  📊 捕获 {len(vals)} 个数据点 -> {out}")
        return VISAResult(status="success", summary=f"波形 {len(vals)} 点已保存",
                          mode="waveform", connection=resource, values=vals, output_file=out)
    except Exception as e:
        cat = "data-error" if "data" in str(e).lower() else classify_error(e)
        return VISAResult(status="failure", summary=str(e), mode="waveform",
                          failure_category=cat, evidence=[str(e)])


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------

def capture_screenshot(inst, resource: str, output: str) -> VISAResult:
    try:
        inst.write(":DISP:DATA?")
        raw = inst.read_raw()
        data = parse_ieee_block(raw)
        out = output or "screenshot.png"
        with open(out, "wb") as f:
            f.write(data)
        print(f"  📸 截图已保存 -> {out}（{len(data)} 字节）")
        return VISAResult(status="success", summary=f"截图已保存 {len(data)} 字节",
                          mode="screenshot", connection=resource, output_file=out)
    except Exception as e:
        cat = "data-error" if "data" in str(e).lower() else classify_error(e)
        return VISAResult(status="failure", summary=str(e), mode="screenshot",
                          failure_category=cat, evidence=[str(e)])


# ---------------------------------------------------------------------------
# 监控
# ---------------------------------------------------------------------------

def monitor_scpi(inst, command: str, resource: str, interval: float, duration: float) -> VISAResult:
    print(f"📊 监控 {command}（间隔 {interval}s）")
    prev: str | None = None
    deadline = time.time() + duration if duration > 0 else float("inf")
    reads = 0

    try:
        while time.time() < deadline:
            try:
                resp = inst.query(command).strip()
                reads += 1
                if resp != prev:
                    ts = time.strftime("%H:%M:%S")
                    print(f"  [{ts}] {resp}")
                    prev = resp
            except Exception as e:
                print(f"  ❌ {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        pass

    return VISAResult(status="success", summary=f"监控完成，共查询 {reads} 次",
                      mode="monitor", connection=resource)


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------

def print_report(result: VISAResult) -> None:
    icon = {"success": "✅", "failure": "❌", "timeout": "⏱️"}.get(result.status, "❓")
    print(f"\n📊 结果: {icon} {result.summary}")

    if result.connection:
        print(f"  资源: {result.connection}")
    if result.response and result.mode in ("query", "read-register"):
        print(f"  响应: {result.response}")
    if result.output_file:
        print(f"  文件: {result.output_file}")
    if result.failure_category:
        print(f"\n  失败分类: {result.failure_category}")
    if result.evidence:
        print("\n📝 证据:")
        for line in result.evidence[:10]:
            print(f"  {line}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="VISA 仪器调试工具",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--detect", action="store_true", help="探测 VISA 资源和 pyvisa 环境")
    p.add_argument("--idn", action="store_true", help="查询 *IDN? 仪器标识")
    p.add_argument("--query", help="发送 SCPI 查询命令（如 :MEAS:VOLT?）")
    p.add_argument("--write", help="发送 SCPI 写入命令（如 :OUTP ON）")
    p.add_argument("--read-register", help="读取测量值（SCPI 命令）")
    p.add_argument("--waveform", action="store_true", help="捕获示波器波形数据")
    p.add_argument("--screenshot", action="store_true", help="捕获仪器屏幕截图")
    p.add_argument("--monitor", help="持续监控 SCPI 查询值（如 :MEAS:FREQ?）")
    p.add_argument("--resource", help="VISA 资源字符串")
    p.add_argument("--timeout", type=int, default=5000, help="超时毫秒（默认 5000）")
    p.add_argument("--backend", help="PyVISA 后端（@py 或 @ivi，默认自动）")
    p.add_argument("--format", choices=["table", "raw", "json"], default="table", help="输出格式")
    p.add_argument("--output", help="波形 CSV 或截图文件保存路径")
    p.add_argument("--interval", type=float, default=1.0, help="监控间隔秒数（默认 1）")
    p.add_argument("--duration", type=float, default=0, help="监控持续秒数（0=无限）")
    p.add_argument("--wav-format", choices=["ascii", "byte"], default="ascii", help="波形数据格式")
    p.add_argument("--wav-channel", default="CHAN1", help="波形通道（默认 CHAN1）")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.detect:
        if not HAS_PYVISA:
            print("❌ pyvisa 未安装（pip install pyvisa pyvisa-py）")
            return 1
        result = detect_env(args.backend)
        return 0

    if not HAS_PYVISA:
        print("❌ pyvisa 未安装，请运行: pip install pyvisa pyvisa-py")
        return 1

    has_mode = any([args.idn, args.query, args.write, args.read_register,
                    args.waveform, args.screenshot, args.monitor])
    if not has_mode:
        parser.print_help()
        return 1

    if not args.resource:
        print("❌ 需要 --resource 参数（如 --resource TCPIP::192.168.1.100::INSTR）")
        return 1

    inst, err = open_resource(args.resource, args.timeout, args.backend)
    if inst is None:
        print(f"❌ 连接失败: {args.resource}")
        if err:
            print(f"  {err}")
        return 1
    print(f"🔗 已连接: {args.resource}")

    try:
        if args.idn:
            result = query_idn(inst, args.resource)
        elif args.query:
            result = send_query(inst, args.query, args.resource)
        elif args.write:
            result = send_write(inst, args.write, args.resource)
        elif args.read_register:
            result = read_register(inst, args.read_register, args.resource)
        elif args.waveform:
            result = capture_waveform(inst, args.resource, args.output, args.wav_format, args.wav_channel)
        elif args.screenshot:
            result = capture_screenshot(inst, args.resource, args.output)
        elif args.monitor:
            result = monitor_scpi(inst, args.monitor, args.resource, args.interval, args.duration)
        else:
            return 1

        result.connection = args.resource
        print_report(result)
        return 0 if result.status == "success" else 1

    finally:
        inst.close()
        print("🔌 连接已关闭")


if __name__ == "__main__":
    sys.exit(main())
