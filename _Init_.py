"""
like_me - QQ名片点赞插件 for NekroAgent

QQ点赞规则（参考astrbot实现）：
- 每次调用 send_like(times=10) 尝试点10个赞
- 如果是好友：成功点10个（VIP可尝试20个）
- 如果不是好友：QQ会限制，可能只成功1个或失败
- 通过循环多次调用来尽可能多点
- 腾讯限制：每天最多给50个不同的人点赞

兼容：NapCat / OneBot v11 协议
版本：3.2.0
"""
import json
import time
import asyncio
from datetime import datetime, date
from typing import Optional
import httpx

from nekro_agent.api.core import logger
from nekro_agent.api.plugin import NekroPlugin
from nekro_agent.api.command import CommandExecutionContext, CommandPermission
from nekro_agent.api.timer import recurring_timer
from nekro_agent.core.agents.ctx import AgentCtx
from nekro_agent.schemas.moderation import SandboxMethodType

# ==================== 插件实例 ====================
plugin = NekroPlugin(
    name="like_me",
    module_name="_Init_",
    description="QQ名片点赞插件，支持手动点赞、每日自动点赞和VIP智能识别",
    author="Lingma",
    version="3.2.0"
)


# ==================== 配置管理 ====================
class LikeConfig:
    """点赞插件配置"""

    # NapCat API 配置
    NAPCAT_HOST = "127.0.0.1"
    NAPCAT_PORT = 9999
    NAPCAT_TOKEN = ""

    # 点赞策略（根据腾讯官方限制）
    # 规则：对同一个人每天最多点10次
    LIKE_TIMES_PER_CALL = 10  # 每次API调用点的次数（最大10）
    VIP_TIMES_PER_CALL = 10   # VIP也是10（腾讯限制，无法突破）

    # 每日限制（NapCat限制，非好友每天最多50人）
    MAX_DAILY_USERS = 50      # 每天最多给50个不同的人点赞

    # 自动点赞配置
    AUTO_LIKE_TIME = "09:00"
    ENABLE_AUTO_LIKE = True
    SEND_NOTIFICATION = True


config = LikeConfig()


# ==================== 数据管理 ====================
class DataManager:
    """用户数据管理"""

    @staticmethod
    def get_today_key() -> str:
        return date.today().isoformat()

    @staticmethod
    def get_user_data(user_id: str) -> dict:
        """获取用户数据"""
        all_data = plugin.store.get("like_me_users", "{}")
        try:
            data_dict = json.loads(all_data)
            user_data = data_dict.get(user_id)

            if not user_data:
                return {
                    "total_likes": 0,
                    "last_date": DataManager.get_today_key(),
                    "daily_users": [],  # 今日已点赞的用户列表
                    "is_vip": False
                }

            # 检查是否需要重置
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
        """记录已点赞的用户"""
        user_data = DataManager.get_user_data(requester_id)
        if target_id not in user_data["daily_users"]:
            user_data["daily_users"].append(target_id)
        DataManager.save_user_data(requester_id, user_data)

    @staticmethod
    def get_remaining_users(requester_id: str) -> int:
        """获取今日剩余可点赞人数"""
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
    """
    调用 NapCat 点赞API
    
    Returns:
        (success, message, actual_likes)
    """
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
                # 检查是否是非好友限制
                if "权限" in error_msg or "好友" in error_msg:
                    return False, "非好友，无法点赞", 0
                return False, f"点赞失败: {error_msg}", 0

    except httpx.TimeoutException:
        return False, "请求超时", 0
    except httpx.ConnectError:
        return False, f"无法连接NapCat", 0
    except Exception as e:
        logger.error(f"点赞异常: {e}")
        return False, f"请求失败: {str(e)}", 0


async def perform_like(target_id: str, requester_id: str) -> tuple[int, str]:
    """
    执行点赞（单次调用，腾讯限制每人每天最多10次）
    
    Returns:
        (total_likes, message)
    """
    # 检查剩余名额
    remaining = data.get_remaining_users(requester_id)
    if remaining <= 0:
        return 0, f"今日已给{config.MAX_DAILY_USERS}人点赞，达到限制"

    # 确定点赞次数（腾讯限制最大10次）
    user_data = data.get_user_data(requester_id)
    times = config.LIKE_TIMES_PER_CALL  # 固定10次，VIP也无法突破

    # 单次调用
    success, msg, actual = await send_like_api(target_id, times)
    
    if success:
        # 记录数据
        data.add_liked_user(requester_id, target_id)
        data.add_total_likes(requester_id, actual)
        return actual, f"成功点赞{actual}次"
    else:
        return 0, msg


# ==================== 命令处理 ====================
async def do_like(ctx: CommandExecutionContext):
    """执行点赞"""
    user_id = ctx.db_user.user_id if ctx.db_user else ctx.chat_key

    remaining = data.get_remaining_users(user_id)
    if remaining <= 0:
        await ctx.reply(
            f"今日已给{config.MAX_DAILY_USERS}人点赞\n"
            f"达到腾讯官方限制，请明天再试~"
        )
        return

    await ctx.reply("正在点赞...")
    
    total_likes, msg = await perform_like(user_id, user_id)

    if total_likes > 0:
        user_data = data.get_user_data(user_id)
        remaining_after = data.get_remaining_users(user_id)
        vip_tag = "[VIP]" if user_data.get("is_vip") else "普通"

        await ctx.reply(
            f"{msg}\n"
            f"━━━━━━━━━━━━━━\n"
            f"用户类型: {vip_tag}\n"
            f"今日已赞: {len(user_data['daily_users'])}/{config.MAX_DAILY_USERS}人\n"
            f"剩余名额: {remaining_after}人\n"
            f"累计点赞: {user_data['total_likes']}次"
        )
    else:
        await ctx.reply(msg)


async def do_subscribe(ctx: CommandExecutionContext):
    user_id = ctx.db_user.user_id if ctx.db_user else ctx.chat_key
    user_name = ctx.db_user.nickname if ctx.db_user else "未知用户"

    if data.is_subscribed(user_id):
        await ctx.reply("你已经订阅了每日自动点赞功能~")
        return

    data.subscribe(user_id, user_name)
    await ctx.reply(
        f"订阅成功\n"
        f"━━━━━━━━━━━━━━\n"
        f"每天 {config.AUTO_LIKE_TIME} 自动为你点赞\n"
        f"取消订阅: /like_me 取消订阅\n"
        f"查看状态: /like_me 状态"
    )


async def do_unsubscribe(ctx: CommandExecutionContext):
    user_id = ctx.db_user.user_id if ctx.db_user else ctx.chat_key

    if not data.is_subscribed(user_id):
        await ctx.reply("你还没有订阅每日自动点赞~")
        return

    data.unsubscribe(user_id)
    await ctx.reply("✅ 已取消订阅每日自动点赞")


async def do_status(ctx: CommandExecutionContext):
    user_id = ctx.db_user.user_id if ctx.db_user else ctx.chat_key
    user_data = data.get_user_data(user_id)
    remaining = data.get_remaining_users(user_id)
    is_sub = data.is_subscribed(user_id)

    vip_tag = "👑 VIP用户" if user_data.get("is_vip") else "普通用户"
    liked_count = len(user_data["daily_users"])

    await ctx.reply(
        f"📊 点赞状态\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 用户: {data.get_user_name(user_id)}\n"
        f"🎯 类型: {vip_tag}\n"
        f"📅 今日已赞: {liked_count}/{config.MAX_DAILY_USERS}人\n"
        f"⏳ 剩余名额: {remaining}人\n"
        f"💯 累计点赞: {user_data['total_likes']}次\n"
        f"🔔 自动: {'✅ 已订阅' if is_sub else '❌ 未订阅'}\n"
        f"⏰ 时间: {config.AUTO_LIKE_TIME}"
    )


# ==================== 主命令 ====================
@plugin.mount_command(
    name="like_me",
    description="QQ点赞功能",
    permission=CommandPermission.MEMBER,
    usage="/like_me [订阅|取消订阅|状态]"
)
async def cmd_like_me(ctx: CommandExecutionContext):
    args = ctx.args.strip() if ctx.args else ""

    if not args or args in ["点赞", "赞我"]:
        await do_like(ctx)
    elif args in ["订阅", "订阅点赞", "自动点赞"]:
        await do_subscribe(ctx)
    elif args in ["取消订阅", "退订"]:
        await do_unsubscribe(ctx)
    elif args in ["状态", "我的点赞"]:
        await do_status(ctx)
    else:
        await ctx.reply(
            "❓ 未知命令\n\n"
            "可用命令:\n"
            "  /like_me          - 立即点赞\n"
            "  /like_me 订阅      - 订阅自动点赞\n"
            "  /like_me 取消订阅  - 取消订阅\n"
            "  /like_me 状态      - 查看状态\n\n"
            "快捷方式:\n"
            "  赞我              - 立即点赞\n"
            "  订阅点赞          - 订阅\n"
            "  我的点赞          - 查看状态"
        )


# ==================== 快捷命令 ====================
@plugin.mount_command(
    name="赞我",
    description="立即点赞（快捷命令）",
    permission=CommandPermission.MEMBER
)
async def cmd_zanwo(ctx: CommandExecutionContext):
    await do_like(ctx)


@plugin.mount_command(
    name="订阅点赞",
    description="订阅自动点赞（快捷命令）",
    permission=CommandPermission.MEMBER
)
async def cmd_subscribe_shortcut(ctx: CommandExecutionContext):
    await do_subscribe(ctx)


@plugin.mount_command(
    name="我的点赞",
    description="查看点赞状态（快捷命令）",
    permission=CommandPermission.MEMBER
)
async def cmd_my_status(ctx: CommandExecutionContext):
    await do_status(ctx)


# ==================== 情绪系统 ====================
class MoodSystem:
    """简单的情绪系统"""
    
    MOODS = {
        "happy": {"name": "开心", "level": 3, "willing_to_like": True},
        "normal": {"name": "普通", "level": 2, "willing_to_like": True},
        "sad": {"name": "难过", "level": 1, "willing_to_like": False},
        "angry": {"name": "生气", "level": 0, "willing_to_like": False}
    }
    
    @staticmethod
    def get_mood() -> str:
        """获取当前情绪"""
        mood_data = plugin.store.get("like_me_mood", '{"mood": "normal"}')
        try:
            data = json.loads(mood_data)
            return data.get("mood", "normal")
        except:
            return "normal"
    
    @staticmethod
    def is_willing_to_like() -> bool:
        """检查是否愿意点赞"""
        mood = MoodSystem.get_mood()
        return MoodSystem.MOODS.get(mood, {}).get("willing_to_like", True)
    
    @staticmethod
    def get_mood_hint() -> str:
        """获取情绪提示（给AI的）"""
        mood = MoodSystem.get_mood()
        mood_info = MoodSystem.MOODS.get(mood, MoodSystem.MOODS["normal"])
        
        if mood == "happy":
            return "你现在心情很好，很乐意帮用户点赞。用开心、积极的语气回复。"
        elif mood == "normal":
            return "你心情一般，正常帮用户点赞。用平和的语气回复。"
        elif mood == "sad":
            return "你今天心情不太好，不太想点赞。可以表达一下犹豫或抱怨，但还是会帮忙。"
        elif mood == "angry":
            return "你今天心情很不好，不想点赞。可以拒绝用户，或者抱怨一下。"
        else:
            return ""


mood_system = MoodSystem()


# ==================== 沙盒方法（供AI调用，带情绪和模板）====================
@plugin.mount_sandbox_method(
    method_type=SandboxMethodType.TOOL,
    name="like_user",
    description="为用户点赞，返回点赞结果数据供AI生成回复"
)
async def tool_like_user(_ctx: AgentCtx, user_id: str = "") -> dict:
    """
    AI工具：执行点赞并返回数据
    
    注意：AI需要根据当前情绪和返回的template生成个性化回复
    
    Returns:
        dict - 包含点赞结果、数据和回复模板
    """
    if not user_id:
        user_id = _ctx.db_user.user_id if _ctx.db_user else _ctx.chat_key
    
    requester_id = user_id
    
    # 检查情绪（心情不好可能拒绝）
    mood_hint = mood_system.get_mood_hint()
    willing = mood_system.is_willing_to_like()
    
    if not willing:
        return {
            "success": False,
            "reason": "mood_bad",
            "message": "心情不好，不想点赞",
            "mood": mood_system.get_mood(),
            "mood_hint": mood_hint,
            "template_failure": "你今天心情不好，不太想帮用户点赞。可以抱怨一下、撒撒娇，或者干脆拒绝。根据你的人设来决定怎么处理。",
            "data": {}
        }
    
    # 检查剩余名额
    remaining = data.get_remaining_users(requester_id)
    if remaining <= 0:
        return {
            "success": False,
            "reason": "daily_limit_reached",
            "message": f"今日已给{config.MAX_DAILY_USERS}人点赞，达到限制",
            "mood": mood_system.get_mood(),
            "mood_hint": mood_hint,
            "template_failure": "告诉用户今天已经达到点赞上限了，明天再来吧。可以表达遗憾或安慰。",
            "data": {
                "max_daily_users": config.MAX_DAILY_USERS,
                "remaining": 0
            }
        }
    
    # 执行点赞
    success, msg, actual = await send_like_api(user_id, config.LIKE_TIMES_PER_CALL)
    
    if success:
        # 记录数据
        data.add_liked_user(requester_id, user_id)
        data.add_total_likes(requester_id, actual)
        
        user_data = data.get_user_data(requester_id)
        remaining_after = data.get_remaining_users(requester_id)
        liked_count = len(user_data["daily_users"])
        
        current_mood = mood_system.get_mood()
        
        return {
            "success": True,
            "reason": "success",
            "message": msg,
            "mood": current_mood,
            "mood_hint": mood_hint,
            "template_success": f"你已经成功点了{actual}个赞！当前心情：{MoodSystem.MOODS[current_mood]['name']}。根据template和人设生成回复。可以提到今天还能给{remaining_after}人点赞。",
            "data": {
                "likes_added": actual,
                "total_likes": user_data["total_likes"],
                "daily_liked_users": liked_count,
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
            "mood": mood_system.get_mood(),
            "mood_hint": mood_hint,
            "template_failure": "点赞失败了，可能是非好友或其他原因。向用户道歉并解释情况。根据你的人设来决定语气。",
            "data": {}
        }


@plugin.mount_sandbox_method(
    method_type=SandboxMethodType.TOOL,
    name="get_like_status",
    description="获取用户点赞状态信息，返回数据供AI生成回复"
)
async def tool_get_status(_ctx: AgentCtx, user_id: str = "") -> dict:
    """AI工具：获取点赞状态"""
    if not user_id:
        user_id = _ctx.db_user.user_id if _ctx.db_user else _ctx.chat_key
    
    user_data = data.get_user_data(user_id)
    remaining = data.get_remaining_users(user_id)
    is_sub = data.is_subscribed(user_id)
    liked_count = len(user_data["daily_users"])
    
    return {
        "success": True,
        "data": {
            "user_name": data.get_user_name(user_id),
            "is_vip": user_data.get("is_vip", False),
            "daily_liked_users": liked_count,
            "max_daily_users": config.MAX_DAILY_USERS,
            "remaining_users": remaining,
            "total_likes": user_data["total_likes"],
            "is_subscribed": is_sub,
            "auto_like_time": config.AUTO_LIKE_TIME
        }
    }


@plugin.mount_sandbox_method(
    method_type=SandboxMethodType.TOOL,
    name="subscribe_like",
    description="订阅每日自动点赞，返回结果供AI生成回复"
)
async def tool_subscribe(_ctx: AgentCtx, user_id: str = "") -> dict:
    """AI工具：订阅自动点赞"""
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
    description="取消订阅每日自动点赞，返回结果供AI生成回复"
)
async def tool_unsubscribe(_ctx: AgentCtx, user_id: str = "") -> dict:
    """AI工具：取消订阅"""
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
async def auto_like_job():
    """每日自动点赞任务"""
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
                if config.SEND_NOTIFICATION:
                    try:
                        from nekro_agent.api.message import send_text
                        user_name = data.get_user_name(user_id)
                        await send_text(
                            f"✅ 每日自动点赞已完成！\n"
                            f"本次: {total_likes}次",
                            target_id=user_id,
                            target_type="private"
                        )
                    except Exception as e:
                        logger.warning(f"发送通知失败: {e}")
            else:
                fail_count += 1
                logger.warning(f"自动点赞失败 {user_id}: {msg}")

            await asyncio.sleep(2)

        except Exception as e:
            fail_count += 1
            logger.error(f"自动点赞异常 {user_id}: {e}")

    logger.info(f"自动点赞完成: 成功{success_count}, 失败{fail_count}")


# ==================== 生命周期 ====================
_job_id = None

@plugin.mount_init_method
async def on_init():
    global _job_id

    logger.info("=" * 50)
    logger.info("like_me v3.2.0 已加载")
    logger.info(f"NapCat: {config.NAPCAT_HOST}:{config.NAPCAT_PORT}")
    logger.info(f"点赞策略: 每次{config.LIKE_TIMES_PER_CALL}次, 最多{config.MAX_CALLS_PER_USER}次调用")
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
            logger.info(f"✓ 定时任务: {cron} (id={_job_id})")
        except Exception as e:
            logger.error(f"✗ 定时任务失败: {e}")


@plugin.mount_cleanup_method
async def on_cleanup():
    global _job_id
    if _job_id:
        try:
            recurring_timer.delete_job(_job_id)
        except:
            pass
    logger.info("like_me 已卸载")
