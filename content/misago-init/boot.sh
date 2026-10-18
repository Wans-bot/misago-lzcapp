#!/bin/sh
# Misago 容器启动脚本（等价原 setup_script 行为 + 启动 uwsgi）。
# 由 lzc-manifest.yml 以 entrypoint=/bin/sh command=本文件 的方式执行；
# 不使用 setup_script，避免 lzcinit 包裹层的 waitid 偶发 panic（详见 manifest 注释）。
set -e

cd /misago

# 0) uwsgi 连接复用修复（幂等；容器重建后自动重新应用）：
#    平台网关 (app-1) 按 HTTP/1.1 语义复用与 misago 的连接；uwsgi 默认的
#    http-socket 在每次响应后静默关闭连接且不发 Connection: close，复用中的
#    非幂等请求（管理后台 graphql 的 analytics POST）会偶发 connection reset
#    → 网关 502（实测复现：同连接第二请求必被 RST）。改用 http11-socket
#    （HTTP/1.1 keep-alive 协议）后连接被正确复用（四连验证零重置）。
if grep -q "^http-socket" /misago/uwsgi.ini; then
  sed -i 's|^http-socket|http11-socket|' /misago/uwsgi.ini
fi

# 1) 数据库迁移与静态文件收集（django 均幂等，每次启动重复执行安全）
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# 2) 幂等创建超级用户：已存在时 createsuperuser 报错，用 || true 吞掉
if [ -n "$MISAGO_ADMIN_USER" ] && [ -n "$MISAGO_ADMIN_PASSWORD" ]; then
  python manage.py createsuperuser \
    --username "$MISAGO_ADMIN_USER" \
    --email "$MISAGO_ADMIN_USER@misago.local" \
    --password "$MISAGO_ADMIN_PASSWORD" --noinput || true
fi

# 3) 演示内容与站点地址（幂等，仅首次完整执行；失败不阻塞启动）
python3 /lzcapp/pkg/content/misago-init/lzc-seed.py || echo "[boot] seed script failed"

# 4) 修正主题页时间戳英文残留：html lang 与 moment locale 对齐（幂等）
python3 /lzcapp/pkg/content/misago-init/lzc-patch-moment-locale.py || echo "[boot] moment patch failed"

# 5) 启动镜像原本的服务进程（Dockerfile CMD: uwsgi --ini uwsgi.ini）
exec uwsgi --ini uwsgi.ini
