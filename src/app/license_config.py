"""授权服务配置（雪松 License Manager，cedar-v/license-manager）。

部署：阿里云 ECS `47.120.52.139`，HTTPS 域名 `https://license.vizquant.top`
（Nginx 443 → 后端 127.0.0.1:18888；80 端口 301 跳转 HTTPS，
Let's Encrypt 证书自动续期）。
公钥随客户端 `res/license_public_key.pem` 分发；服务端更换密钥后需同步发版。
"""
LICENSE_SERVER_URL = "https://license.vizquant.top"
LICENSE_PRODUCT_NAME = "啄木鸟"
LICENSE_PUBLIC_KEY_FILE = "license_public_key.pem"
