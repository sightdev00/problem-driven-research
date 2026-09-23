#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
required=(README.md docs/PRINCIPLES.md docs/ARCHITECTURE.md docs/DECISIONS.md research/dms-eye-context/WALKTHROUGH.md structure/visual-domain.example.yaml structure/physics-domain.example.yaml structure/sources.json structure/sources.example.yaml structure/experiment-capability.example.yaml)
for relative_path in "${required[@]}"; do
  if [[ ! -s "$project_dir/$relative_path" ]]; then
    printf '缺失或为空: %s\n' "$relative_path" >&2
    exit 1
  fi
done
printf '架构草案检查通过：%s 项文件齐全。\n' "${#required[@]}"
if [[ "${1:-}" == '--check' ]]; then exit 0; fi
if [[ "${1:-}" == '--start' ]]; then exec "$project_dir/research.sh"; fi
if [[ $# -gt 0 ]]; then printf '用法: ./run.sh [--check|--start]\n' >&2; exit 2; fi
printf '\n架构文档和研究流程入口已就绪。\n'
printf '阅读顺序：\n  docs/PRINCIPLES.md\n  docs/ARCHITECTURE.md\n  docs/DECISIONS.md\n  research/dms-eye-context/WALKTHROUGH.md\n'
if [[ ! -t 0 ]]; then
  printf '\n在交互终端运行 ./run.sh 可在阅读后启动研究流程。\n'
  exit 0
fi
read -r -p $'\n阅读完毕，启动研究流程？[y/N] ' start_answer
if [[ "$start_answer" == 'y' || "$start_answer" == 'Y' ]]; then
  exec "$project_dir/research.sh"
fi
