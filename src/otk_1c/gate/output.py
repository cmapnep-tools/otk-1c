"""Файлы сводки в каталоге прогона: JSON, Markdown, HTML, JUnit, Problems, SARIF."""
import json
from pathlib import Path

from . import report, report_html


def write(summary, run_dir, repo):
    run_dir = Path(run_dir)
    summary = report.public_summary(summary)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (run_dir / "summary.md").write_text(report.markdown(summary), encoding="utf-8")
    (run_dir / "summary.html").write_text(report_html.html(summary), encoding="utf-8")
    (run_dir / "junit.xml").write_text(report.junit(summary), encoding="utf-8")
    (run_dir / "problems.txt").write_text("\n".join(report.problems(summary, repo)) + "\n", encoding="utf-8")
    (run_dir / "summary.sarif").write_text(json.dumps(report.sarif(summary, repo), ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")
