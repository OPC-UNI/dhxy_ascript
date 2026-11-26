# 授权服务（雪松 License Manager）部署与运维

> 2026-11-19 上线。服务端使用开源项目
> [cedar-v/license-manager](https://github.com/cedar-v/license-manager) v1.2.0，
> 部署在阿里云 ECS `47.120.52.139`，HTTPS 域名 `https://license.vizquant.top`
> （Let's Encrypt 证书自动续期，80 端口 301 跳转）。客户端接入见
> `src/core/license_client.py`，服务地址/产品名见 `src/app/license_config.py`。

## 1. 架构与路径

```
用户电脑（Windows exe）
  └─ Nginx :443（https://license.vizquant.top；:80 → 301 跳转）
       ├─ /            → /opt/license-manager/frontend-dist（Vue 管理后台）
       └─ /api/        → 127.0.0.1:18888（Go 后端）
                              └─ MySQL 127.0.0.1:3306（license_manager 库）
```

| 项 | 路径/命令 |
|---|---|
| 后端二进制 | `/opt/license-manager/bin/license-manager` |
| 后端配置 | `/opt/license-manager/backend/cmd/config.yaml` |
| RSA 私钥/公钥 | `/opt/license-manager/backend/configs/rsa_private_key.pem` / `rsa_public_key.pem` |
| 前端静态文件 | `/opt/license-manager/frontend-dist/` |
| 数据库 | MySQL 8，库 `license_manager`，用户 `license_user`（仅 127.0.0.1） |
| 后端服务 | `systemctl status/restart license-manager` |
| 管理后台 | `https://license.vizquant.top/`（用户名 `admin`） |
| HTTPS 证书 | `/etc/letsencrypt/live/license.vizquant.top/`（自动续期，`certbot renew --dry-run` 验证） |
| 管理员改密 | `sudo /opt/license-manager/scripts/set-admin-password.sh` |
| Nginx 配置 | `/etc/nginx/conf.d/license-manager.conf` |
| 内存/磁盘 | 2C1.8G + 2G swap；后端约 30MB，MySQL 约 160MB |

## 2. 首次登录与改密

- 部署时已把默认 `admin/admin@123` 改为随机强密码（见交付消息），
  登录后请立即保存到密码管理器。
- 修改密码（无网页入口，使用脚本）：
  ```bash
  sudo /opt/license-manager/scripts/set-admin-password.sh
  ```
  脚本用 bcrypt 生成哈希并写入 `users` 表，同时清空登录失败锁定。

## 3. 生成卡密

管理后台 →「授权码管理」→ 新增：

- **有效天数**：如 30 / 90 / 365（最大 365000 = 永久）。
- **最大激活设备数**：每张卡允许同时绑定的设备数（一机一码填 `1`）。
- **功能配置/使用限制/自定义参数**：可选，当前客户端未使用；留空即可。

生成后复制卡密发给用户。用户首次在面板「系统 → 授权信息」输入卡密，
客户端采集本机 `MachineGuid` 的 SHA-256 作为硬件指纹，服务端绑定该设备。

## 4. 换机 / 解绑

客户端「解绑激活码」只清除本机许可证，**不会释放服务端设备名额**。换机流程：

1. 管理后台 →「许可证管理」→ 按设备码（面板「设备码」）或卡密找到旧设备的许可证。
2. 先「撤销」，再「删除」（服务端只允许删除已撤销的许可证）——删除后释放一个激活名额。
3. 用户在新电脑输入同一卡密激活。

> 社区版没有用户自助解绑门户，解绑由作者在后台操作；企业版才带用户自助端。

## 5. 客户端配置与公钥

- `src/app/license_config.py`：
  - `LICENSE_SERVER_URL`：服务地址（`https://license.vizquant.top`）。
  - `LICENSE_PRODUCT_NAME`：面板展示名（啄木鸟）。
- 产品 RSA 公钥：**内嵌在 `src/app/license_config.py` 的
  `LICENSE_PUBLIC_KEY_PEM`**（编译进 exe），不再读安装目录下的 pem 文件
  ——外置文件可被替换后自签许可证。**服务端更换 RSA 密钥必须同步更新该
  常量并发版**，否则老客户端验签失败。
- 本地许可证：`data/_license/license.json`（机器全局，升级/回滚保留）。
  **验签固定使用随包内置公钥**：记录文件里的 `public_key` 字段与激活响应
  中的公钥都不参与验证——该文件是明文可改的，若把它当信任锚，换一把自签
  公钥即可伪造远期许可证（2026-11-20 实测）。换密钥只能随客户端发版。
- 网络异常策略：本地许可证有效时放行（日志标注“使用本地许可证”）；
  服务端明确返回撤销/过期/设备不匹配时拦截。
- 运行期心跳：主进程后台线程按服务端下发的 `heartbeat_interval`
  （默认 300 秒、下限 30 秒）周期调用 `POST /api/v1/heartbeat`，程序
  运行期间服务端可持续看到在线状态并下发撤销/续期；网络失败静默重试
  （连续 3 次才记一条 ERROR），撤销/过期记 ERROR 但不中止已运行任务
  （下次点「启动」仍被 `check()` 拦截）；心跳在途时面板解绑/换卡不会
  把旧许可证记录写回。
- 离线宽限：距上次成功心跳超过 **7 天**（`MAX_OFFLINE_SECONDS`）必须联网
  校验成功一次，网络失败直接拦截；`last_heartbeat` 落在未来视为记录被改/
  时钟回拨，同样强制联网——防止改本地时间戳把撤销/过期检查永久冻结。

## 6. 备份与恢复

```bash
# 备份数据库
mysqldump -uroot license_manager > /root/license_manager_$(date +%F).sql
# 备份配置与密钥（私钥必须保密）
tar czf /root/license_manager_files_$(date +%F).tar.gz \
  /opt/license-manager/backend/cmd/config.yaml \
  /opt/license-manager/backend/configs
```

恢复：先 `systemctl stop license-manager`，导入 SQL、恢复配置/密钥，
再 `systemctl start license-manager`。RSA 私钥丢失将导致所有已签发许可证
无法再签发新许可证（已发许可证仍可用旧公钥验签，但无法续期/换机）。

## 7. 安全注意事项

- 管理后台与激活接口已走 **HTTPS**（Let's Encrypt，证书自动续期；
  `http://license.vizquant.top` 301 跳转 HTTPS）。如更换域名/证书，需同步
  更新 `src/app/license_config.py` 并重新发版。
- 管理后台暴露在公网，已启用 5 次失败锁 15 分钟；请使用强密码。
- **Nginx 限流**：`/api/v1/activate` 每 IP 30 次/分钟（burst 20），
  `/api/v1/login` 每 IP 5 次/分钟（burst 5）；超限返回 HTTP 429 +
  `{"code":"429","message":"请求过于频繁，请稍后再试"}`。心跳不限流，
  避免多用户共用 NAT 时误伤。修改阈值见 `license-manager.conf` 顶部
  `limit_req_zone`。
- MySQL 只监听 127.0.0.1，不对公网开放；root 使用 `auth_socket`
  （仅 root 系统用户可登录，无空密码），业务账号 `license_user` 仅本机。
  后端也只监听 127.0.0.1，由 Nginx 反代。
- 服务器重启时 Nginx 可能因 `wecom.conf` 的 `qyapi.weixin.qq.com` DNS
  未就绪而启动失败（历史问题）；如遇 80 端口不通，执行
  `systemctl start nginx` 即可。

## 8. 常见错误码

| 码 | 含义 | 处理 |
|---|---|---|
| `300001` | 授权码不存在/已过期 | 检查卡密与有效期 |
| `300003` | 授权码已锁定 | 后台解锁 |
| `300004` | 激活数量已达上限 | 后台撤销并删除旧设备许可证 |
| `300006` | 许可证不存在 | 重新激活 |
| `300007` | 许可证已被撤销 | 联系作者重新发卡 |
| `300008` | 硬件指纹不匹配 | 后台解绑旧设备后重新激活 |
| `429` | 请求过于频繁（Nginx 限流） | 稍等 1 分钟再试；不要连续点击激活 |
| 网络异常 | 无法连接授权服务器 | 检查网络；本地许可证有效则仍可启动 |

## 9. 升级服务端

1. 下载新版本源码（GitHub release），保留 `backend/configs/`（配置与 RSA 密钥）和数据库。
2. 重新编译后端：`cd backend && GOPROXY=https://goproxy.cn,direct go build -o /opt/license-manager/bin/license-manager ./cmd`
3. 前端本地构建（`npm ci && npx vite build --mode production`，`VITE_ADDRESS_BASE_URL='/'`），
   上传替换 `frontend-dist/`。
4. `systemctl restart license-manager && nginx -t && systemctl reload nginx`。
5. 升级前备份数据库与配置/密钥。
