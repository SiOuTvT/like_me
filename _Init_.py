"""
like_me - QQ名片点赞插件 for NekroAgent

QQ点赞规则：
- 每次调用 send_like(times=10) 尝试点10个赞
- 腾讯限制：每天最多给50个不同的人点赞

兼容：NapCat / OneBot v11 协议
版本：4.0.0
"""
import json
from datetime import date
import httpx

from nekro_agent.core import logger
from nekro_agent.api.plugin import NekroPlugin, SandboxMethodType
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.api import recurring_timer


# ==================== 插件实例 ====================
plugin = NekroPlugin(
    name="like_me",
    module_name="like_me",
    description="QQ名片点赞插件",
    author="Lingma",
    version="4.0.0",
    url=""
)


# ==================== 配置 ====================
class LikeConfig:
    NAPCAT_HOST = "127.0.0.1"
    NAPCAT_PORT = 9999
    NAPCAT_TOKEN = ""
    LIKE_TIMES_PER_CALL = 10
    MAX_DAILY_USERS = 50
    AUTO_LIKE_TIME = "09:00"
    ENABLE_AUTO_LIKE = True


config = LikeConfig()


# ==================== 数据管理 ====================
class DataManager:
    @staticmethod
    def get_today_key() -> str:
        return date.today().isoformat()

    @staticmethod
    def get_user_data(user_id: str) -> dict:
        all_data = plugin.store.get("like_me_users", "{}")
        try:
            data_dict = json.loads(all_data)
            user_data = data_dict.get(user_id)

            if not user_data:
                return {
                    "total_likes": 0,
                    "last_date": DataManager.get_today_key(),
                    "daily_users": [],
                    "is_vip": False
                }

            today = DataManager.get_today_key()
            if user_data.get("last_date") != today:
                user_data["daily_users"] = []
                user_data["last_date"] = today

            return user_data
        except Exception as e:
            logger.error(f"获取用户数据失败: {e}")
            return {
                "total_likes": 0,
                "last_date": DataManager.get_today_key(),
                "daily_users": [],
                "is_vip": False
            }

    @staticmethod
    def save_user_data(user_id: str, data: dict):
        all_data = plugin.store.get("like_me_users", "{}")
        try:
            data_dict = json.loads(all_data)
        except:
            data_dict = {}
        data_dict[user_id] = data
        plugin.store.set("like_me_users", json.dumps(data_dict))

    @staticmethod
    def add_liked_user(requester_id: str, target_id: str):
        user_data = DataManager.get_user_data(requester_id)
        if target_id not in user_data["daily_users"]:
            user_data["daily_users"].append(target_id)
        DataManager.save_user_data(requester_id, user_data)

    @staticmethod
    def get_remaining_users(requester_id: str) -> int:
        user_data = DataManager.get_user_data(requester_id)
        return config.MAX_DAILY_USERS - len(user_data["daily_users"])

    @staticmethod
    def add_total_likes(user_id: str, count: int):
        user_data = DataManager.get_user_data(user_id)
        user_data["total_likes"] += count
        DataManager.save_user_data(user_id, user_data)

    @staticmethod
    def is_subscribed(user_id: str) -> bool:
        subscribed = plugin.store.get("like_me_subscribed", "[]")
        try:
            return user_id in json.loads(subscribed)
        except:
            return False

    @staticmethod
    def subscribe(user_id: str, user_name: str = ""):
        subscribed = plugin.store.get("like_me_subscribed", "[]")
        try:
            users = json.loads(subscribed)
        except:
            users = []
        if user_id not in users:
            users.append(user_id)
            plugin.store.set("like_me_subscribed", json.dumps(users))
        if user_name:
            names = plugin.store.get("like_me_names", "{}")
            try:
                name_dict = json.loads(names)
            except:
                name_dict = {}
            name_dict[user_id] = user_name
            plugin.store.set("like_me_names", json.dumps(name_dict))

    @staticmethod
    def unsubscribe(user_id: str) -> bool:
        subscribed = plugin.store.get("like_me_subscribed", "[]")
        try:
            users = json.loads(subscribed)
        except:
            users = []
        if user_id in users:
            users.remove(user_id)
            plugin.store.set("like_me_subscribed", json.dumps(users))
            return True
        return False

    @staticmethod
    def get_subscribed_users() -> list:
        subscribed = plugin.store.get("like_me_subscribed", "[]")
        try:
            return json.loads(subscribed)
        except:
            return []

    @staticmethod
    def get_user_name(user_id: str) -> str:
        names = plugin.store.get("like_me_names", "{}")
        try:
            name_dict = json.loads(names)
            return name_dict.get(user_id, f"用户{user_id[-4:]}")
        except:
            return f"用户{user_id[-4:]}"


data = DataManager()


# ==================== NapCat API ====================
async def send_like_api(user_id: str, times: int) -> tuple[bool, str, int]:
    url = f"http://{config.NAPCAT_HOST}:{config.NAPCAT_PORT}/send_like"
    headers = {"Content-Type": "application/json"}
    if config.NAPCAT_TOKEN:
        headers["Authorization"] = f"Bearer {config.NAPCAT_TOKEN}"

    payload = {"user_id": int(user_id), "times": times}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            result = resp.json()

            if result.get("status") == "ok" or result.get("retcode") == 0:
                return True, "点赞成功", times
            else:
                error_msg = result.get("message", "未知错误")
                if "权限" in error_msg or "好友" in error_msg:
                    return False, "非好友，无法点赞", 0
                return False, f"点赞失败: {error_msg}", 0

    except httpx.TimeoutException:
        return False, "请求超时", 0
    except httpx.ConnectError:
        return False, "无法连接NapCat", 0
    except Exception as e:
        logger.error(f"点赞异常: {e}")
        return False, f"请求失败: {str(e)}", 0


async def perform_like(target_id: str, requester_id: str) -> tuple[int, str]:
    remaining = data.get_remaining_users(requester_id)
    if remaining <= 0:
        return 0, f"今日已给{config.MAX_DAILY_USERS}人点赞，达到限制"

    user_data = data.get_user_data(requester_id)
    times = config.LIKE_TIMES_PER_CALL

    success, msg, actual = await send_like_api(target_id, times)

    if success:
        data.add_liked_user(requester_id, target_id)
        data.add_total_likes(requester_id, actual)
        return actual, f"成功点赞{actual}次"
    else:
        return 0, msg


# ==================== AI 工具 ====================
@plugin.mount_sandbox_method(
    method_type=SandboxMethodType.TOOL,
    name="like_user",
    description="为用户点赞"
)
async def tool_like_user(_ctx: AgentCtx, user_id: str = "") -> dict:
    if not user_id:
        user_id = _ctx.db_user.user_id if _ctx.db_user else _ctx.chat_key

    requester_id = user_id

    remaining = data.get_remaining_users(requester_id)
    if remaining <= 0:
        return {
            "success": False,
            "reason": "daily_limit_reached",
            "message": f"今日已给{config.MAX_DAILY_USERS}人点赞，达到限制",
            "data": {
                "max_daily_users": config.MAX_DAILY_USERS,
                "remaining": 0
            }
        }

    success, msg, actual = await send_like_api(user_id, config.LIKE_TIMES_PER_CALL)

    if success:
        data.add_liked_user(requester_id, user_id)
        data.add_total_likes(requester_id, actual)

        user_data = data.get_user_data(requester_id)
        remaining_after = data.get_remaining_users(requester_id)

        return {
            "success": True,
            "reason": "success",
            "message": msg,
            "data": {
                "likes_added": actual,
                "total_likes": user_data["total_likes"],
                "daily_liked_users": len(user_data["daily_users"]),
                "max_daily_users": config.MAX_DAILY_USERS,
                "remaining_users": remaining_after,
                "is_vip": user_data.get("is_vip", False)
            }
        }
    else:
        return {
            "success": False,
            "reason": "like_failed",
            "message": msg,
            "data": {}
        }


@plugin.mount_sandbox_method(
    method_type=SandboxMethodType.TOOL,
    name="get_like_status",
    description="获取用户点赞状态"
)
async def tool_get_status(_ctx: AgentCtx, user_id: str = "") -> dict:
    if not user_id:
        user_id = _ctx.db_user.user_id if _ctx.db_user else _ctx.chat_key

    user_data = data.get_user_data(user_id)
    remaining = data.get_remaining_users(user_id)
    is_sub = data.is_subscribed(user_id)

    return {
        "success": True,
        "data": {
            "daily_liked_users": len(user_data["daily_users"]),
            "max_daily_users": config.MAX_DAILY_USERS,
            "remaining_users": remaining,
            "total_likes": user_data["total_likes"],
            "is_vip": user_data.get("is_vip", False),
            "is_subscribed": is_sub,
            "auto_like_time": config.AUTO_LIKE_TIME
        }
    }


@plugin.mount_sandbox_method(
    method_type=SandboxMethodType.TOOL,
    name="subscribe_like",
    description="订阅每日自动点赞"
)
async def tool_subscribe(_ctx: AgentCtx, user_id: str = "") -> dict:
    if not user_id:
        user_id = _ctx.db_user.user_id if _ctx.db_user else _ctx.chat_key

    user_name = _ctx.db_user.nickname if _ctx.db_user else "用户"

    if data.is_subscribed(user_id):
        return {
            "success": False,
            "reason": "already_subscribed",
            "message": "已经订阅了每日自动点赞"
        }

    data.subscribe(user_id, user_name)

    return {
        "success": True,
        "reason": "subscribed",
        "message": "订阅成功",
        "data": {
            "auto_like_time": config.AUTO_LIKE_TIME
        }
    }


@plugin.mount_sandbox_method(
    method_type=SandboxMethodType.TOOL,
    name="unsubscribe_like",
    description="取消订阅每日自动点赞"
)
async def tool_unsubscribe(_ctx: AgentCtx, user_id: str = "") -> dict:
    if not user_id:
        user_id = _ctx.db_user.user_id if _ctx.db_user else _ctx.chat_key

    if not data.is_subscribed(user_id):
        return {
            "success": False,
            "reason": "not_subscribed",
            "message": "还没有订阅每日自动点赞"
        }

    data.unsubscribe(user_id)

    return {
        "success": True,
        "reason": "unsubscribed",
        "message": "已取消订阅"
    }


# ==================== 定时任务 ====================
_job_id = None

async def auto_like_job():
    if not config.ENABLE_AUTO_LIKE:
        return

    logger.info("开始执行每日自动点赞")

    subscribers = data.get_subscribed_users()
    if not subscribers:
        logger.info("没有订阅用户")
        return

    success_count = 0
    fail_count = 0

    for user_id in subscribers:
        try:
            remaining = data.get_remaining_users(user_id)
            if remaining <= 0:
                logger.warning(f"用户 {user_id} 今日名额已用完")
                continue

            total_likes, msg = await perform_like(user_id, user_id)

            if total_likes > 0:
                success_count += 1
            else:
                fail_count += 1
                logger.warning(f"自动点赞失败 {user_id}: {msg}")

        except Exception as e:
            fail_count += 1
            logger.error(f"自动点赞异常 {user_id}: {e}")

    logger.info(f"自动点赞完成: 成功{success_count}, 失败{fail_count}")


# ==================== 生命周期 ====================
@plugin.mount_init_method()
async def on_init():
    global _job_id

    logger.info("=" * 50)
    logger.info("like_me v4.0.0 已加载")
    logger.info(f"NapCat: {config.NAPCAT_HOST}:{config.NAPCAT_PORT}")
    logger.info(f"点赞策略: 每次{config.LIKE_TIMES_PER_CALL}次")
    logger.info(f"每日限制: {config.MAX_DAILY_USERS}人")
    logger.info(f"自动点赞: {config.AUTO_LIKE_TIME} ({'启用' if config.ENABLE_AUTO_LIKE else '禁用'})")
    logger.info("=" * 50)

    if config.ENABLE_AUTO_LIKE:
        try:
            hour, minute = map(int, config.AUTO_LIKE_TIME.split(":"))
            cron = f"{minute} {hour} * * *"
            _job_id = recurring_timer.create_cron_job(
                name="like_me_auto",
                cron_expression=cron,
                callback=auto_like_job,
                description="like_me 每日自动点赞"
            )
            logger.info(f"定时任务已注册: {cron} (id={_job_id})")
        except Exception as e:
            logger.error(f"定时任务注册失败: {e}")


@plugin.mount_cleanup_method()
async def on_cleanup():
    global _job_id
    if _job_id:
        try:
            recurring_timer.delete_job(_job_id)
        except:
            pass
    logger.info("like_me 已卸载")
