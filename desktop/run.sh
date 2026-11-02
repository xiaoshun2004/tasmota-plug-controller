#!/usr/bin/env bash
# 启动 Tasmota 插头控制台
#
# 必须用「带 Xft 的 Python」——系统自带的 /usr/bin/python3 就是这个。
# 原因：某些独立安装的 Python（例如 pyenv / 托管版本）编译 Tk 时没带 Xft 支持，
# 只能看到 20 个 X 核心字体，一个中文字体都没有，界面会整片显示成方块。
# 系统 python3 的 Tk 8.6 走 Xft，能看到 Noto CJK 等 200+ 款字体。
set -u
cd "$(dirname "$0")" || exit 1

for py in /usr/bin/python3 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c "import tkinter" >/dev/null 2>&1; then
        exec "$py" app.py "$@"
    fi
done

echo "错误：找不到带 tkinter 的 python3。" >&2
echo "Ubuntu/Debian 安装：sudo apt install python3-tk" >&2
echo "Fedora：sudo dnf install python3-tkinter" >&2
exit 1
