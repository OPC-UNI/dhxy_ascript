"""授权服务配置（雪松 License Manager，cedar-v/license-manager）。

部署：阿里云 ECS `47.120.52.139`，HTTPS 域名 `https://license.vizquant.top`
（Nginx 443 → 后端 127.0.0.1:18888；80 端口 301 跳转 HTTPS，
Let's Encrypt 证书自动续期）。
产品公钥**内置于本模块**（编译进 exe）：信任锚不能是安装目录里的明文文件
——外置 pem 可被随意替换，换掉即可自签许可证（2026-11-20 实测）。内嵌后
篡改需要改二进制；服务端更换 RSA 密钥仍须同步发版。
"""
LICENSE_SERVER_URL = "https://license.vizquant.top"
LICENSE_PRODUCT_NAME = "啄木鸟"
LICENSE_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2xVc6aGq1He/DFXZkx4w
ufTu5t6FIYTnqj792iPdYnP4M6nBj9tnMVDtYCcvcuDUAwjVvRxpDVtesI8s+Wcy
3oXTZk87vTNXvriE1fxrKiio2a/UuGG6LFk1Q9BfcM5rddm3lASPQkGj6wX5/E4r
/c76VBmY3AyGJWrqE4UJJ7V2fAipPW2CrIX/Wu9Hvu2Yza2Q0AWxvqQT7u/9Ffnr
UOmObJFFe8/Gci0fyY+tlASYuqXizr5C7YNvo6wwBvlIkU6Swyf1DHwfQmZRdSmS
vDW0PFkJYeSeETVRQCO1d4Q6NqCCMPlpf5qXggPIZG+/DKmPMcHo6LuEgtpFo6iM
3QIDAQAB
-----END PUBLIC KEY-----
"""
