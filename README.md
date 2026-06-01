# like_me

QQ名片点赞插件 for NekroAgent

## 功能

- 手动点赞 / 每日自动点赞
- VIP智能识别
- 遵守腾讯官方限制（每天50人，每次10次）
- AI工具集成
- 状态查询

## 安装

```bash
# 创建插件目录
mkdir -p /path/to/nekro-agent/plugins/workdir/like_me

# 复制文件并重命名
cp _Init_.py /path/to/nekro-agent/plugins/workdir/like_me/__init__.py

# 重启 NekroAgent
```

## 使用

### 快捷命令（推荐）

```
赞我              # 立即点赞
订阅点赞          # 订阅自动点赞
我的点赞          # 查看状态
```

### 标准命令

```
/like_me          # 立即点赞
/like_me 订阅      # 订阅
/like_me 取消订阅  # 取消订阅
/like_me 状态      # 查看状态
```

## 配置

编辑 `_Init_.py` 中的 `LikeConfig` 类：

```python
class LikeConfig:
    NAPCAT_HOST = "127.0.0.1"    # NapCat 地址
    NAPCAT_PORT = 9999           # NapCat 端口
    AUTO_LIKE_TIME = "09:00"     # 自动点赞时间
    ENABLE_AUTO_LIKE = True      # 是否启用自动点赞
```

## 注意

- 腾讯限制：每天最多给50人点赞，每次最多10次
- 需要好友关系才能成功点赞
- 确保 NapCat 正常运行

## 更新日志

### v3.2.0 (2026-06-01)
- 新增情绪系统
- 优化AI工具

### v3.1.0
- 修正QQ点赞规则

---

MIT License
