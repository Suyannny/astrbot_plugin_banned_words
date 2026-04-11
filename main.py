import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

@register(
    "astrbot_plugin_banned_words",
    "Converted",
    "指令违禁词管理插件，支持添加、删除、列出违禁词，并自动拦截包含违禁词的消息",
    "1.4.0",
    "https://github.com/Suyannny/astrbot_plugin_banned_words"
)
class BannedWordsPlugin(Star):
    """违禁词管理插件主类"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        self.data_dir = StarTools.get_data_dir()
        self.data_file = self.data_dir / "banned_words_data.json"

        self.banned_words: Dict[str, List[str]] = {}
        self.global_banned_words: List[str] = []
        self.admin_users: Dict[str, List[str]] = {}
        self.master_users: List[str] = []
        
        self.force_master_id = str(self.config.get("force_master_id", "")).strip()
        self.command_prefix = str(self.config.get("command_prefix", "/")).strip()
        self.enable_prefix_trigger = self.config.get("enable_prefix_trigger", True)
        
        self.bypass_for_authorized = self.config.get("bypass_for_authorized", False)
        self.show_banned_warning = self.config.get("show_banned_warning", False)
        self.help_text = self.config.get("help_text", "")

        self._load_config()
        self._load_data()

    def _get_prefix_display(self) -> str:
        """获取用于显示的前缀（帮助文本中使用）"""
        return self.command_prefix if self.enable_prefix_trigger else ""

    def _parse_json_text(self, text) -> dict:
        if isinstance(text, dict): return text
        if not isinstance(text, str) or not text.strip(): return {}
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _load_config(self):
        if self.force_master_id:
            self.master_users = [self.force_master_id]
        self.global_banned_words = [str(w) for w in self.config.get("global_banned_words", []) if isinstance(w, str)]
        
        config_banned_words = self._parse_json_text(self.config.get("group_banned_words", "{}"))
        for group_id, words in config_banned_words.items():
            group_id = str(group_id)
            if group_id not in self.banned_words: self.banned_words[group_id] = []
            if not isinstance(words, list): continue
            for word in words:
                if word not in self.banned_words[group_id]: self.banned_words[group_id].append(word)
        logger.info(f"配置加载完成：主人 {len(self.master_users)} 人，全局违禁词 {len(self.global_banned_words)} 个")

    async def initialize(self):
        logger.info("违禁词管理插件已加载")
        trigger_mode = f"前缀触发({self.command_prefix})" if self.enable_prefix_trigger else "无前缀触发"
        logger.info(f"指令触发模式：{trigger_mode}")

    def _load_data(self):
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for group_id, words in data.get("banned_words", {}).items():
                    group_id = str(group_id)
                    if group_id not in self.banned_words: self.banned_words[group_id] = []
                    for word in words:
                        if word not in self.banned_words[group_id]: self.banned_words[group_id].append(word)
                for user_id, groups in data.get("admin_users", {}).items():
                    user_id = str(user_id)
                    if user_id not in self.admin_users: self.admin_users[user_id] = []
                    for group_id in groups:
                        group_id = str(group_id)
                        if group_id not in self.admin_users[user_id]: self.admin_users[user_id].append(group_id)
                for uid in data.get("master_users", []):
                    if str(uid) not in self.master_users: self.master_users.append(str(uid))
        except Exception as e:
            logger.error(f"加载运行时数据失败: {e}")

    def _save_data(self):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            data = {"banned_words": self.banned_words, "admin_users": self.admin_users, "master_users": self.master_users}
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存数据失败: {e}")

    def _is_master(self, user_id: str) -> bool:
        return str(user_id) in [str(uid) for uid in self.master_users]

    def _is_admin(self, user_id: str, group_id: str) -> bool:
        if self._is_master(user_id): return True
        return str(group_id) in [str(g) for g in self.admin_users.get(str(user_id), [])]

    def _check_banned_words(self, message: str, group_id: str) -> Optional[str]:
        for word in self.global_banned_words:
            if word.lower() in message.lower(): return word
        for word in self.banned_words.get(str(group_id), []):
            if word.lower() in message.lower(): return word
        return None

    def _get_at_users(self, event: AstrMessageEvent) -> List[str]:
        if hasattr(event, 'get_mentions') and callable(event.get_mentions):
            try:
                mentions = event.get_mentions()
                if mentions: return [str(m) for m in mentions]
            except Exception: pass
        if hasattr(event, 'message') and isinstance(event.message, list):
            for seg in event.message:
                try:
                    if isinstance(seg, dict) and seg.get("type") == "at":
                        qq = seg.get("data", {}).get("qq")
                        if qq: return [str(qq)]
                    elif hasattr(seg, 'type') and getattr(seg, 'type', '') == "at":
                        data = getattr(seg, 'data', None)
                        if isinstance(data, dict) and 'qq' in data: return [str(data['qq'])]
                        elif hasattr(seg, 'qq'): return [str(seg.qq)]
                except Exception: continue
        if hasattr(event, 'message_str'):
            match = re.search(r'\[CQ:at,qq=(\d+)\]', event.message_str)
            if match: return [match.group(1)]
        return []

    # ==================== 核心机制：消息拦截与指令分发 ====================

    COMMAND_LIST = [
        "添加违禁词", "删除违禁词", "违禁词列表", "清空违禁词",
        "授权管理员", "取消授权", "管理员列表",
        "添加主人", "删除主人", "主人列表", "违禁词帮助",
    ]

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=1)
    async def on_group_message(self, event: AstrMessageEvent):
        """最高优先级：拦截违禁词，但必须放行本插件的指令"""
        group_id = str(event.get_group_id())
        message = event.message_str.strip()
        sender_id = str(event.get_sender_id())

        if self.bypass_for_authorized and (self._is_master(sender_id) or self._is_admin(sender_id, group_id)): return

        check_msg = message
        if self.enable_prefix_trigger:
            if check_msg.startswith(self.command_prefix):
                check_msg = check_msg[len(self.command_prefix):].strip()
            else:
                check_msg = ""  # 开启了前缀但没带前缀，必定不是本插件指令，不用再匹配了

        if any(check_msg.startswith(cmd) for cmd in self.COMMAND_LIST): return

        banned_word = self._check_banned_words(message, group_id)
        if banned_word:
            logger.info(f"用户 {sender_id} 在群 {group_id} 发送违禁词: {banned_word}")
            event.message_str = ""
            if hasattr(event, 'message') and isinstance(event.message, list): event.message.clear()
            if self.show_banned_warning: yield event.plain_result(f"⚠️ 您的消息包含违禁词「{banned_word}」，已被拦截。")
            event.stop_event()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=10)
    async def command_dispatcher(self, event: AstrMessageEvent):
        """次高优先级：统一分发插件指令"""
        message = event.message_str.strip()
        check_msg = message

        if self.enable_prefix_trigger:
            if not check_msg.startswith(self.command_prefix): return
            check_msg = check_msg[len(self.command_prefix):].strip()

        matched_cmd = None
        for cmd in self.COMMAND_LIST:
            if check_msg.startswith(cmd):
                matched_cmd = cmd
                break

        if not matched_cmd: return
        param = check_msg[len(matched_cmd):].strip()

        handler_map = {
            "添加违禁词": self._handle_add_banned_word,
            "删除违禁词": self._handle_remove_banned_word,
            "违禁词列表": self._handle_list_banned_words,
            "清空违禁词": self._handle_clear_banned_words,
            "授权管理员": self._handle_authorize_admin,
            "取消授权": self._handle_revoke_admin,
            "管理员列表": self._handle_list_admins,
            "添加主人": self._handle_add_master,
            "删除主人": self._handle_remove_master,
            "主人列表": self._handle_list_masters,
            "违禁词帮助": self._handle_show_help,
        }

        handler = handler_map.get(matched_cmd)
        if handler:
            async for result in handler(event, param):
                yield result

    # ==================== 指令处理逻辑 ====================

    async def _handle_add_banned_word(self, event: AstrMessageEvent, param: str):
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        if not group_id: yield event.plain_result("❌ 此指令只能在群聊中使用"); return
        if not self._is_admin(user_id, group_id): yield event.plain_result("❌ 您没有权限执行此操作"); return
        word = param.strip()
        if not word: yield event.plain_result(f"❌ 请提供要添加的违禁词\n用法：{self._get_prefix_display()}添加违禁词 <违禁词>"); return
        if group_id not in self.banned_words: self.banned_words[group_id] = []
        if word in self.banned_words[group_id]: yield event.plain_result(f"❌ 违禁词「{word}」已存在"); return
        self.banned_words[group_id].append(word)
        self._save_data()
        yield event.plain_result(f"✅ 已添加违禁词「{word}」\n当前群聊共有 {len(self.banned_words[group_id])} 个违禁词")

    async def _handle_remove_banned_word(self, event: AstrMessageEvent, param: str):
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        if not group_id: yield event.plain_result("❌ 此指令只能在群聊中使用"); return
        if not self._is_admin(user_id, group_id): yield event.plain_result("❌ 您没有权限执行此操作"); return
        word = param.strip()
        if not word: yield event.plain_result(f"❌ 请提供要删除的违禁词\n用法：{self._get_prefix_display()}删除违禁词 <违禁词>"); return
        if group_id not in self.banned_words or word not in self.banned_words[group_id]: yield event.plain_result(f"❌ 违禁词「{word}」不存在"); return
        self.banned_words[group_id].remove(word)
        if not self.banned_words[group_id]: del self.banned_words[group_id]
        self._save_data()
        yield event.plain_result(f"✅ 已删除违禁词「{word}」")

    async def _handle_list_banned_words(self, event: AstrMessageEvent, param: str):
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        if not group_id: yield event.plain_result("❌ 此指令只能在群聊中使用"); return
        words = self.banned_words.get(group_id, [])
        result_lines = []
        if self.global_banned_words:
            result_lines.append("🌍 【全局预设违禁词】：")
            result_lines.extend([f" {i+1}. {w}" for i, w in enumerate(self.global_banned_words)])
        if words:
            if result_lines: result_lines.append("")
            result_lines.append("💡 【本群专属违禁词】：")
            result_lines.extend([f" {i+1}. {w}" for i, w in enumerate(words)])
        if not result_lines: yield event.plain_result("📋 当前群聊没有设置违禁词"); return
        yield event.plain_result(f"📋 违禁词列表：\n" + "\n".join(result_lines))

    async def _handle_clear_banned_words(self, event: AstrMessageEvent, param: str):
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        if not group_id: yield event.plain_result("❌ 此指令只能在群聊中使用"); return
        if not self._is_admin(user_id, group_id): yield event.plain_result("❌ 您没有权限执行此操作"); return
        if group_id in self.banned_words:
            count = len(self.banned_words[group_id])
            del self.banned_words[group_id]
            self._save_data()
            yield event.plain_result(f"✅ 已清空当前群聊的专属违禁词（共 {count} 个）\n提示：全局违禁词仍会生效")
        else: yield event.plain_result("📋 当前群聊没有设置专属违禁词")

    async def _handle_authorize_admin(self, event: AstrMessageEvent, param: str):
        user_id = str(event.get_sender_id())
        if not self._is_master(user_id): yield event.plain_result("❌ 只有主人可以执行此操作"); return
        current_group_id = str(event.get_group_id()) if event.get_group_id() else None
        target_user_id, group_id = None, None
        
        mentions = self._get_at_users(event)
        if mentions: target_user_id, group_id = mentions[0], current_group_id
        
        if not target_user_id:
            parts = param.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit(): group_id, target_user_id = parts[0], parts[1]
            elif len(parts) == 1 and parts[0].isdigit(): group_id, target_user_id = current_group_id, parts[0]
            
        if not group_id or not target_user_id: yield event.plain_result(f"❌ 请提供正确的参数\n用法：\n {self._get_prefix_display()}授权管理员 QQ号\n {self._get_prefix_display()}授权管理员 群号 QQ号"); return
        if self._is_master(target_user_id): yield event.plain_result("⚠️ 该用户已是主人，无需授权"); return
        if target_user_id not in self.admin_users: self.admin_users[target_user_id] = []
        if group_id in self.admin_users[target_user_id]: yield event.plain_result(f"⚠️ 用户 {target_user_id} 已是群 {group_id} 的管理员"); return
        self.admin_users[target_user_id].append(group_id)
        self._save_data()
        yield event.plain_result(f"✅ 已授权用户 {target_user_id} 为群 {group_id} 的管理员")

    async def _handle_revoke_admin(self, event: AstrMessageEvent, param: str):
        user_id = str(event.get_sender_id())
        if not self._is_master(user_id): yield event.plain_result("❌ 只有主人可以执行此操作"); return
        current_group_id = str(event.get_group_id()) if event.get_group_id() else None
        target_user_id, group_id = None, None
        
        mentions = self._get_at_users(event)
        if mentions: target_user_id, group_id = mentions[0], current_group_id
        
        if not target_user_id:
            parts = param.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit(): group_id, target_user_id = parts[0], parts[1]
            elif len(parts) == 1 and parts[0].isdigit(): group_id, target_user_id = current_group_id, parts[0]
            
        if not group_id or not target_user_id: yield event.plain_result(f"❌ 请提供正确的参数\n用法：\n {self._get_prefix_display()}取消授权 QQ号\n {self._get_prefix_display()}取消授权 群号 QQ号"); return
        if target_user_id not in self.admin_users or group_id not in self.admin_users[target_user_id]: yield event.plain_result(f"⚠️ 用户 {target_user_id} 不是群 {group_id} 的管理员"); return
        self.admin_users[target_user_id].remove(group_id)
        if not self.admin_users[target_user_id]: del self.admin_users[target_user_id]
        self._save_data()
        yield event.plain_result(f"✅ 已取消用户 {target_user_id} 在群 {group_id} 的管理员授权")

    async def _handle_list_admins(self, event: AstrMessageEvent, param: str):
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        if not group_id: yield event.plain_result("❌ 此指令只能在群聊中使用"); return
        admins = [admin_id for admin_id, groups in self.admin_users.items() if group_id in [str(g) for g in groups]]
        masters_in_group = self.master_users.copy()
        if not admins and not masters_in_group: yield event.plain_result("📋 当前群聊没有授权管理员\n提示：主人默认拥有所有群聊的管理权限"); return
        result_lines = [f"📋 当前群聊管理员列表："]
        if masters_in_group:
            result_lines.append("\n👑 主人（拥有全部权限）：")
            for i, master_id in enumerate(masters_in_group): result_lines.append(f" {i+1}. {master_id}")
        if admins:
            result_lines.append(f"\n🔧 授权管理员（共 {len(admins)} 人）：")
            for i, admin_id in enumerate(admins): result_lines.append(f" {i+1}. {admin_id}")
        yield event.plain_result("\n".join(result_lines))

    async def _handle_add_master(self, event: AstrMessageEvent, param: str):
        user_id = str(event.get_sender_id())
        if not self._is_master(user_id): yield event.plain_result("❌ 只有主人可以执行此操作"); return
        parts = param.split()
        target_user_id = parts[0] if len(parts) >= 1 and parts[0].isdigit() else None
        if not target_user_id: yield event.plain_result(f"❌ 请提供要添加的主人QQ号\n用法：{self._get_prefix_display()}添加主人 QQ号"); return
        if self._is_master(target_user_id): yield event.plain_result(f"⚠️ 用户 {target_user_id} 已是主人"); return
        self.master_users.append(target_user_id)
        self._save_data()
        yield event.plain_result(f"✅ 已添加用户 {target_user_id} 为主人")

    async def _handle_remove_master(self, event: AstrMessageEvent, param: str):
        user_id = str(event.get_sender_id())
        if not self._is_master(user_id): yield event.plain_result("❌ 只有主人可以执行此操作"); return
        parts = param.split()
        target_user_id = parts[0] if len(parts) >= 1 and parts[0].isdigit() else None
        if not target_user_id: yield event.plain_result(f"❌ 请提供要删除的主人QQ号\n用法：{self._get_prefix_display()}删除主人 QQ号"); return
        if not self._is_master(target_user_id): yield event.plain_result(f"⚠️ 用户 {target_user_id} 不是主人"); return
        if self.force_master_id and str(target_user_id) == self.force_master_id: 
            yield event.plain_result("❌ 无法删除在配置中强制设定的最高权限主人！请去WebUI配置中修改。"); return
        if len(self.master_users) <= 1: yield event.plain_result("❌ 至少需要保留一个主人"); return
        self.master_users = [uid for uid in self.master_users if str(uid) != target_user_id]
        self._save_data()
        yield event.plain_result(f"✅ 已删除主人 {target_user_id}")

    async def _handle_list_masters(self, event: AstrMessageEvent, param: str):
        if not self.master_users: yield event.plain_result("📋 当前没有设置主人"); return
        master_list = "\n".join([f" {i+1}. {master_id}" for i, master_id in enumerate(self.master_users)])
        yield event.plain_result(f"👑 主人列表（共 {len(self.master_users)} 人）：\n{master_list}")

    async def _handle_show_help(self, event: AstrMessageEvent, param: str):
        prefix = self._get_prefix_display()
        if self.help_text:
            yield event.plain_result(self.help_text.replace("{prefix}", prefix))
            return
        bypass_status = "不会被拦截" if self.bypass_for_authorized else "同样会被拦截"
        warning_status = "会发送提示" if self.show_banned_warning else "静默拦截"
        help_text = f"""📖 违禁词管理插件帮助

═══════════════════════════
【违禁词管理】
═══════════════════════════
{prefix}添加违禁词 <词> - 添加当前群违禁词
{prefix}删除违禁词 <词> - 删除当前群违禁词
{prefix}违禁词列表 - 查看违禁词列表(含全局)
{prefix}清空违禁词 - 清空当前群专属违禁词

═══════════════════════════
【管理员授权】（仅主人可用）
═══════════════════════════
{prefix}授权管理员 群号 QQ号 - 授权指定群管理员
{prefix}取消授权 群号 QQ号 - 取消指定群授权
{prefix}管理员列表 - 查看管理员列表

═══════════════════════════
【主人管理】（仅主人可用）
═══════════════════════════
{prefix}添加主人 QQ号 - 添加新主人
{prefix}删除主人 QQ号 - 删除普通主人
{prefix}主人列表 - 查看主人列表

═══════════════════════════
【权限与机制说明】
═══════════════════════════
• 违禁词分为“全局预设”和“单群专属”，均会生效
• 管理员只能管理被授权群聊的违禁词
• 主人和管理员发送的消息{bypass_status}
• 当前触发模式：{'需要前缀 ' + prefix if self.enable_prefix_trigger else '无前缀直接触发'}

═══════════════════════════
【当前配置状态】
═══════════════════════════
• 豁免开关：{'已开启' if self.bypass_for_authorized else '已关闭'}
• 拦截提示：{'已开启' if self.show_banned_warning else '已关闭'}（{warning_status}）"""
        yield event.plain_result(help_text)

    async def terminate(self):
        self._save_data()
        logger.info("违禁词管理插件已卸载")
