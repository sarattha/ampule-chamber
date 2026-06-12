"""CLI for rendering Ampule Chamber markdown reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from chamber.report import load_report_input, render_markdown_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an Ampule Chamber reliability report.")
    parser.add_argument(
        "--fixture",
        required=True,
        help="Path to a JSON report input fixture.",
    )
    parser.add_argument(
        "--output",
        help="Path to write the rendered markdown report. Defaults to stdout.",
    )
    args = parser.parse_args()

    report_input = load_report_input(args.fixture)
    markdown = render_markdown_report(report_input)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
