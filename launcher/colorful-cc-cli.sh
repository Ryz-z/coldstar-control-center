#!/bin/bash
# 隐星控制中心 - 命令行状态查看
# Usage: colorful-cc-cli [status|fan <0-100>|auto|kbd-on|kbd-off|report]
PORT=8632
BASE="http://127.0.0.1:${PORT}"
C() { curl -s --max-time 3 "$@"; }

case "${1:-status}" in
  status)
    C "$BASE/api/status" | /usr/bin/python3 -m json.tool --no-ensure-ascii 2>/dev/null \
      || echo "控制服务未运行（sudo systemctl start colorful-control-center）"
    ;;
  report)
    C "$BASE/api/diag" | /usr/bin/python3 -c "import json,sys; print(json.load(sys.stdin).get('report',''))" 2>/dev/null \
      || echo "控制服务未运行"
    ;;
  fan)
    V="${2:-50}"
    C -X POST "$BASE/api/fan" -d "{\"mode\":\"manual\",\"manual\":[$V,$V,$V]}" >/dev/null \
      && echo "已设为手动 ${V}%" || echo "操作失败"
    ;;
  auto)
    C -X POST "$BASE/api/fan" -d '{"mode":"auto"}' >/dev/null \
      && echo "风扇已交还 EC 自动控制" || echo "操作失败"
    ;;
  kbd-on)
    C -X POST "$BASE/api/kbd" -d '{"on":true}' >/dev/null && echo "键盘背光已开" || echo "操作失败"
    ;;
  kbd-off)
    C -X POST "$BASE/api/kbd" -d '{"on":false}' >/dev/null && echo "键盘背光已关" || echo "操作失败"
    ;;
  *)
    echo "用法: colorful-cc-cli [status|report|fan <0-100>|auto|kbd-on|kbd-off]"
    ;;
esac
