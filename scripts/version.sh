#!/usr/bin/env bash
# version.sh — 版本号的唯一读取入口。被 hopen.sh / herdr-pane-switch.sh source。
#
# 版本号存在仓库根的 VERSION 文件里（单一事实来源），不硬编码在脚本里，
# 这样发版只改一处。scripts/ 在安装后是指向仓库的 symlink，
# 所以用 `pwd -P` 解析到仓库真实路径，../VERSION 才能读到。
#
# 读不到时返回 "unknown" 而不是报错 —— 版本号只用于显示，缺失不该让脚本挂掉。

# 打印版本号（不带换行外的任何修饰），供 --version 使用。
_hr_version() {
  local here version_file
  # pwd -P 解析掉 symlink：安装后 ~/.config/herdr/scripts 是指向仓库的软链，
  # 用逻辑路径的 ../VERSION 会落到 ~/.config/herdr/VERSION（不存在）。
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  version_file="$here/../VERSION"
  if [ -r "$version_file" ]; then
    # 只取第一行并去掉首尾空白，容忍文件末尾多余空行
    sed -n '1s/[[:space:]]*$//p' "$version_file"
  else
    echo "unknown"
  fi
}

# 打印 "<name> <version>"，供 -v/-V 统一输出格式。
_hr_print_version() {
  printf '%s %s\n' "${1:-herdr-recipes}" "$(_hr_version)"
}
