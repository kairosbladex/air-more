"""命令行入口。

示例：
    python -m airmore.cli generate --data data/sample --out out/ --reports A,B,G

默认使用确定性模板写作，不调用任何外部模型或 GPU 服务器；
仅当显式传 --use-ollama（或 AIRMORE_USE_OLLAMA=1）时才尝试 Ollama，
且地址走环境变量 AIRMORE_OLLAMA_BASE_URL，失败自动回退模板写作。
"""

from __future__ import annotations

import argparse
import sys
import subprocess
from pathlib import Path

from airmore.pipeline import DEFAULT_THRESHOLDS, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="airmore", description="无人机低空环境监测报告生成系统（演示）")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="运行完整流水线生成报告")
    gen.add_argument("--data", required=True, help="CSV 数据目录")
    gen.add_argument("--out", required=True, help="输出目录")
    gen.add_argument("--reports", default="A,B,G",
                     help="报告类型，逗号分隔（当前实现 A/B/G）")
    gen.add_argument("--thresholds", default=str(DEFAULT_THRESHOLDS),
                     help="筛查阈值配置（默认 config/thresholds_demo.json）")
    gen.add_argument("--use-ollama", action="store_true",
                     help="启用 Ollama 受限写作（默认关闭，离线模板写作）")
    gen.add_argument("--profile", default="drone",
                     choices=["drone", "fixed_point"],
                     help="站点形态：drone 无人机巡测（默认）/ fixed_point 固定网格点")
    gen.add_argument("--date", default=None,
                     help="报告日期 YYYY-MM-DD（默认取数据起始日；周报场景取最近完整日）")

    regional = sub.add_parser("generate-regional", help="导入业务结果生成区域空气质量研判日报")
    regional.add_argument("--bundle", required=True, help="区域日报 manifest.json")
    regional.add_argument("--out", required=True, help="输出目录（同名报告拒绝覆盖）")

    args = parser.parse_args(argv)
    if args.command == "generate-regional":
        from airmore.regional.pipeline import generate
        try:
            result = generate(args.bundle, args.out)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"区域日报生成失败：{exc}", file=sys.stderr)
            return 2
        print(f"生成状态：{result['status']}")
        for kind in ("docx", "pdf", "json"):
            print(f"{kind}: {result[kind]}")
        return 0
    if args.command == "generate":
        from airmore.domain.profile import PROFILES
        reports = [r.strip() for r in args.reports.split(",") if r.strip()]
        result = run(data_dir=args.data, out_dir=args.out, reports=reports,
                     thresholds_path=args.thresholds,
                     use_ollama=args.use_ollama,
                     profile=PROFILES[args.profile],
                     report_date=args.date)
        print(f"观测时段: {result['period']['start']} ~ {result['period']['end']}")
        print(f"筛查事件: {result['events']} 个")
        for issue in result["ingest_issues"]:
            print(f"接入提示: {issue}")
        for rtype, art in result["artifacts"].items():
            print(f"[{rtype}] json: {art['json']}")
            print(f"[{rtype}] docx: {art['docx']}")
            print(f"[{rtype}] pdf : {art['pdf']}（{art['pdf_pages']} 页）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
