import asyncio
import json
from datetime import datetime
import httpx
from nekro_agent import NekroPlugin, sandboxmethod, AgentCtx

async def napcat_like(user_id: int, config: dict) -> dict:
    """调用NapCat点赞API"""
    url = f"http://{config.get('host', '127.0.0.1')}:{config.get('port', 9999)}/send_like"
    headers = {"Content-Type": "application/json"}
    if token := config.get("token"):
        headers["Authorization"] = token
    
    payload = {"user_id": user_id, "times": 10}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            data = resp.json()
            
            if data.get("status") == "ok" or data.get("retcode") == 0:
                return {"success": True, "count": 10, "message": "点赞成功", "raw": data}
            else:
                return {"success": False, "count": 0, "message": data.get("message", "点赞失败"), "raw": data}
    except Exception as e:
        return {"success": False, "count": 0, "message": f"请求失败: {str(e)}", "raw": None}

class QQLikePlugin(NekroPlugin):
    name = "qq_like"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        self.user_data = {}
        self.config = {}
        
    async def load(self):
        await super().load()
        self.config = await self.get_config()
        data = await self.plugin_storage.get("like_data")
        if data:
            self.user_data = data
            
    async def unload(self):
        await self.plugin_storage.set("like_data", self.user_data)
        await super().unload()
    
    @sandboxmethod(description="用户请求点赞时调用，回复需通过LLM生成")
    async def like_me(self, ctx: AgentCtx) -> dict:
        user_id = str(ctx.session.details.get("user_id", ""))
        if not user_id:
            return {"result": "error", "need_llm_reply": True, "llm_context": "无法获取用户ID"}
        
        # 检查NapCat限制
        today = datetime.now().strftime("%Y-%m-%d")
        user_info = self.user_data.get(user_id, {"date": today, "tried": 0, "total": 0})
        
        if user_info["date"] != today:
            user_info = {"date": today, "tried": 0, "total": 0}
        
        if user_info["tried"] >= 50:
            return {
                "result": "limit",
                "need_llm_reply": True,
                "llm_context": "达到NapCat每日50次陌生人点赞上限",
                "user_id": user_id,
                "tried_today": 50,
                "limit_type": "napcat"
            }
        
        # 调用API
        napcat_conf = self.config.get("napcat", {})
        api_result = await napcat_like(int(user_id), napcat_conf)
        
        # 更新记录
        user_info["tried"] += 1
        if api_result["success"]:
            user_info["total"] += api_result["count"]
        
        self.user_data[user_id] = user_info
        asyncio.create_task(self.plugin_storage.set("like_data", self.user_data))
        
        # 返回结果（LLM根据此生成回复）
        if api_result["success"]:
            return {
                "result": "success",
                "need_llm_reply": True,
                "llm_context": "QQ点赞操作执行完成",
                "user_id": user_id,
                "action": "点赞",
                "count": api_result["count"],
                "message": api_result["message"],
                "user_stats": {
                    "tried_today": user_info["tried"],
                    "total_likes": user_info["total"],
                    "remaining_napcat": 50 - user_info["tried"]
                },
                "note": "QQ系统会自动处理普通用户10次/VIP用户20次的限制"
            }
        else:
            return {
                "result": "failed",
                "need_llm_reply": True,
                "llm_context": "QQ点赞操作失败",
                "user_id": user_id,
                "action": "点赞",
                "error_message": api_result["message"],
                "user_stats": {
                    "tried_today": user_info["tried"],
                    "total_likes": user_info["total"]
                }
            }
    
    @sandboxmethod(description="查询点赞统计，回复需通过LLM生成")
    async def my_like_stats(self, ctx: AgentCtx) -> dict:
        user_id = str(ctx.session.details.get("user_id", ""))
        if not user_id:
            return {"result": "error", "need_llm_reply": True, "llm_context": "无法获取用户ID"}
        
        today = datetime.now().strftime("%Y-%m-%d")
        user_info = self.user_data.get(user_id, {"date": today, "tried": 0, "total": 0})
        
        tried_today = user_info["tried"] if user_info["date"] == today else 0
        
        return {
            "result": "success",
            "need_llm_reply": True,
            "llm_context": "用户查询点赞统计信息",
            "user_id": user_id,
            "action": "查询统计",
            "stats": {
                "total_likes": user_info["total"],
                "tried_today": tried_today,
                "napcat_remaining": max(0, 50 - tried_today),
                "is_vip": "未知（由QQ系统自动判断）",
                "qq_daily_limit": "普通10次/VIP20次（系统自动处理）"
            }
        }
    
    def prompt_injection(self) -> str:
        return """
        QQ点赞插件说明：
        
        触发点赞：用户说"赞我"、"/赞我"、@机器人说"赞我"
        触发查询：用户说"我的点赞记录"、"点赞统计"
        
        重要规则：
        1. 插件返回的结果中 need_llm_reply=true 时，你必须基于结果生成回复
        2. 回复要使用你在NekroAgent中配置的全局人设和语气
        3. 结合返回数据中的 llm_context、message、stats 等信息生成自然回复
        4. QQ系统的10/20次限制由QQ自动处理，插件只防止NapCat的50次限制
        
        示例回复方式：
        - 成功时："好的主人，已为你点赞{count}次哦~"
        - 失败时："呜呜，点赞失败了：{error_message}"
        - 达到限制时："今天已经点过{tried_today}次啦，NapCat限制每天最多50次哦"
        - 查询时："主人，你今天尝试了{tried_today}次，总共点了{total_likes}次赞呢"
        """