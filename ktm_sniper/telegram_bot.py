import logging
import threading
import time
import dataclasses
import shlex
from typing import Optional, Dict, Any
import requests

from ktm_sniper.models import SniperTaskConfig, Passenger, normalize_date
from ktm_sniper.stations import KTMStationRegistry

logger = logging.getLogger(__name__)

class TelegramCommandHandler:
    """
    Parses and executes remote management commands received via Telegram.
    Strictly verifies sender identity against the authorized chat_id.
    """
    def __init__(self, engine, authorized_chat_id: str):
        self.engine = engine
        self.authorized_chat_id = str(authorized_chat_id).strip()

    def handle_message(self, chat_id: Any, text: str) -> Optional[str]:
        # 1. Strict identity validation
        if str(chat_id).strip() != self.authorized_chat_id:
            logger.warning(f"收到未授权的 Telegram 访问 (chat_id: {chat_id}): {text[:20]}")
            if hasattr(self.engine, "repository") and self.engine.repository:
                try:
                    self.engine.repository.log_bot_interaction(
                        chat_id=str(chat_id),
                        command=text.split()[0] if text.strip() else "",
                        raw_text=text,
                        response="UNAUTHORIZED_DROPPED",
                        status="UNAUTHORIZED"
                    )
                except Exception:
                    pass
            return None

        clean_text = text.strip()
        if not clean_text:
            return None

        parts = clean_text.split()
        command = parts[0].lower()

        response = None
        # Handle command routing
        if command in ("/status", "/info", "状态", "info"):
            response = self._handle_status()
        elif command in ("/pause", "暂停"):
            response = self._handle_pause()
        elif command in ("/resume", "恢复"):
            response = self._handle_resume()
        elif command in ("/help", "/start", "帮助"):
            response = self._handle_help()
        elif command in ("/add_passenger", "/add_passengers", "/addpassenger", "/addpassengers", "/passenger", "添加乘客", "增加乘客", "加人"):
            response = self._handle_add_passenger(clean_text)
        elif command in ("/passengers", "/passenger_list", "/list_passengers", "乘客列表", "乘客", "查人"):
            response = self._handle_list_passengers()
        elif command in ("/clear_passengers", "清空乘客"):
            response = self._handle_clear_passengers()
        elif command == "/set":
            response = self._handle_set(parts[1:])
        else:
            response = (
                "💡 未知指令。您可以发送：\n"
                "• /status - 查看当前抢票与监听状态\n"
                "• /set <字段> <值> - 动态修改行程\n"
                "• /add_passenger <姓名> <证件号> - 增加乘车人\n"
                "• /passengers - 查看当前乘车人列表\n"
                "• /pause - 暂停监控\n"
                "• /resume - 恢复监控\n"
                "• /help - 查看完整使用帮助"
            )

        # Persist interaction into SQLite audit log (fully masked PII)
        if hasattr(self.engine, "repository") and self.engine.repository:
            try:
                self.engine.repository.log_bot_interaction(
                    chat_id=str(chat_id),
                    command=command,
                    raw_text=clean_text,
                    response=response or "",
                    status="SUCCESS" if response else "NO_REPLY"
                )
            except Exception as e:
                logger.warning(f"Failed to log bot interaction to SQLite: {e}")

        return response


    def _handle_status(self) -> str:
        s = self.engine.get_status_summary()
        status_icon = "⏸️" if s["is_paused"] else "🟢"
        if s["is_paused"]:
            status_desc = "已暂停 (PAUSED)"
        elif s["status"] in ("PENDING", "MONITORING"):
            status_desc = "正在实时监控抢票中 (监测余票放票)"
        else:
            status_desc = s["status"]

        rt_info = "否"
        if s["is_round_trip"]:
            rt_info = f"是 (返程 {s['return_date']}, {s['return_time_window']})"

        passengers_list = []
        for p in self.engine.task.passengers:
            passengers_list.append(f"{p.name} (`{p.masked_id}`)")
        passengers_str = ", ".join(passengers_list) if passengers_list else "未指定"

        return (
            f"🚄 *KTMB 抢票守护状态报告* 🚄\n\n"
            f"• *运行状态*: {status_icon} {status_desc}\n"
            f"• *去程路线*: *{s['origin']}* ➡️ *{s['destination']}*\n"
            f"• *出发日期*: `{s['date']}`\n"
            f"• *出发时段*: {s['time_window']}\n"
            f"• *双程往返*: {rt_info}\n"
            f"• *已轮询周期*: {s['total_cycles']} 次\n"
            f"• *乘车人*: {passengers_str}\n"
            f"• *数据存储*: SQLite (脱敏与加密守护已开启)\n\n"
            f"💡 发送 `/set` 可实时更改行程信息。"
        )

    def _handle_pause(self) -> str:
        self.engine.is_paused = True
        return "⏸️ *抢票守护已暂停*。\n后台已挂起轮询，发送 /resume 可随时恢复。"

    def _handle_resume(self) -> str:
        self.engine.is_paused = False
        return "▶️ *抢票守护已恢复*！\n后台正在继续实时嗅探余票..."

    def _handle_set(self, args: list) -> str:
        if len(args) < 2:
            return (
                "⚠️ `/set` 格式错误。正确用法示例：\n"
                "• `/set origin BM` (修改出发地)\n"
                "• `/set dest Ipoh` (修改目的地)\n"
                "• `/set date 2027-02-05` (修改出发日期)\n"
                "• `/set time 08:00-14:00` (修改出发时段)\n"
                "• `/set return_date 2027-02-08` (设置返程日期)"
            )

        field = args[0].lower()
        value = " ".join(args[1:]).strip()

        try:
            current_task = self.engine.task

            if field in ("origin", "from", "出发地"):
                code, name = KTMStationRegistry.resolve_station(value)
                new_task = dataclasses.replace(current_task, origin=name)
                self.engine.update_task(new_task)
                return f"✓ 出发地已更新为: *{name}* [{code}]"

            elif field in ("dest", "destination", "to", "目的地"):
                code, name = KTMStationRegistry.resolve_station(value)
                new_task = dataclasses.replace(current_task, destination=name)
                self.engine.update_task(new_task)
                return f"✓ 目的地已更新为: *{name}* [{code}]"

            elif field in ("date", "出发日期", "日期"):
                norm_date = normalize_date(value)
                new_task = dataclasses.replace(current_task, date=norm_date)
                self.engine.update_task(new_task)
                return f"✓ 出发日期已更新为: *{norm_date}*"

            elif field in ("time", "时段"):
                times = value.replace(" ", "").split("-")
                if len(times) != 2:
                    return "⚠️ 时段格式错误，请输入例如 `08:00-14:00`。"
                new_task = dataclasses.replace(current_task, time_from=times[0], time_to=times[1])
                self.engine.update_task(new_task)
                return f"✓ 出发时段已更新为: *{times[0]} - {times[1]}*"

            elif field in ("return_date", "ret_date", "返程日期"):
                norm_ret_date = normalize_date(value)
                new_task = dataclasses.replace(
                    current_task,
                    is_round_trip=True,
                    return_date=norm_ret_date
                )
                self.engine.update_task(new_task)
                return f"✓ 往返模式已激活，返程日期已设为: *{norm_ret_date}*"

            elif field in ("seats", "seat_count", "人数", "席位数"):
                seats = int(value)
                if seats < 1:
                    return "⚠️ 席位数必须大于等于 1。"
                new_task = dataclasses.replace(current_task, required_seats=seats)
                self.engine.update_task(new_task)
                return f"✓ 锁定席位数已更新为: *{seats}*"

            else:
                return f"⚠️ 未知设置项 '{field}'。支持的字段：origin, dest, date, time, return_date, seats。"
        except Exception as e:
            return f"❌ 设置失败: {e}"

    def _handle_add_passenger(self, text: str) -> str:
        try:
            tokens = shlex.split(text)
            args = tokens[1:]
            if len(args) < 2:
                return (
                    "⚠️ `/add_passenger` 参数不足。正确用法：\n"
                    "• `/add_passenger <姓名> <身份证/护照> [电话] [性别]`\n"
                    "• 示例：`/add_passenger \"Siti Nurhaliza\" 950202-10-5566 0198765432 Female`\n"
                    "• 简写：`/add_passenger Tan 900101-14-5566 0123456789`"
                )

            name = args[0].strip()
            id_number = args[1].strip()
            phone = args[2].strip() if len(args) > 2 else "0123456789"
            gender = args[3].strip() if len(args) > 3 else "Male"

            new_passenger = Passenger(
                name=name,
                id_number=id_number,
                gender=gender,
                phone=phone
            )

            current_task = self.engine.task
            new_passengers = list(current_task.passengers) + [new_passenger]
            new_task = dataclasses.replace(
                current_task,
                passengers=new_passengers,
                required_seats=len(new_passengers)
            )
            self.engine.update_task(new_task)

            return (
                f"✓ 已成功添加乘车人: *{new_passenger.name}* (`{new_passenger.masked_id}`)！\n"
                f"当前共 {len(new_passengers)} 位乘车人，锁定席位数已自动同步为 {len(new_passengers)}。"
            )
        except Exception as e:
            return f"❌ 添加乘客失败: {e}"

    def _handle_list_passengers(self) -> str:
        passengers = self.engine.task.passengers
        if not passengers:
            return "👥 当前暂无预填乘车人。发送 `/add_passenger <姓名> <证件号> [电话] [性别]` 可随时添加。"

        lines = [f"👥 *当前预填乘车人列表 (共 {len(passengers)} 位)*:\n"]
        for idx, p in enumerate(passengers, start=1):
            gender_label = "男 (Male)" if p.gender.lower() in ("male", "m") else "女 (Female)"
            lines.append(f"  {idx}. *{p.name}* (`{p.masked_id}`) | {gender_label} | `{p.masked_phone}`")
        lines.append(f"\n💡 当前锁定席位数: `{self.engine.task.required_seats}`。发送 `/clear_passengers` 可一键清空。")
        return "\n".join(lines)

    def _handle_clear_passengers(self) -> str:
        current_task = self.engine.task
        new_task = dataclasses.replace(current_task, passengers=[], required_seats=1)
        self.engine.update_task(new_task)
        return "✓ 已清空全部乘车人预填列表。如需添加，发送 `/add_passenger <姓名> <证件号>`。"

    def _handle_help(self) -> str:
        return (
            "📱 *KTM Sniper Telegram 手机远程控制菜单* 📱\n\n"
            "🔍 *查看状态*:\n"
            "• `/status` - 查看当前监听路线、时段与轮询次数\n"
            "• `/passengers` - 查看当前预填乘车人列表\n\n"
            "👤 *乘车人管理*:\n"
            "• `/add_passenger <姓名> <证件号> [电话] [性别]` - 增加乘车人 (自动同步席位数)\n"
            "• `/clear_passengers` - 一键清空乘车人列表\n"
            "• `/set seats <数量>` - 手动设置锁定席位总数\n\n"
            "✏️ *动态修改行程*:\n"
            "• `/set origin <车站>` - 修改出发站 (如 `/set origin BM` 或 `/set origin KL Sentral`)\n"
            "• `/set dest <车站>` - 修改目的站 (如 `/set dest Butterworth`)\n"
            "• `/set date <YYYY-MM-DD>` - 修改出发日期 (如 `/set date 2027-02-05`)\n"
            "• `/set time <起始>-<截止>` - 修改时段 (如 `/set time 08:00-14:00`)\n"
            "• `/set return_date <日期>` - 设置返程日期激活往返 (如 `/set return_date 2027-02-08`)\n\n"
            "⏸️ *运行控制*:\n"
            "• `/pause` - 暂停监听 (节省服务器请求与流量)\n"
            "• `/resume` - 恢复监听\n"
            "• `/help` - 呼出本帮助菜单"
        )



class TelegramCommandListener(threading.Thread):
    """
    Background daemon thread polling Telegram getUpdates via long-polling.
    No inbound ports needed on EC2.
    """
    def __init__(self, bot_token: str, authorized_chat_id: str, engine, session=None):
        super().__init__(daemon=True, name="TelegramCommandListener")
        self.bot_token = bot_token
        self.authorized_chat_id = str(authorized_chat_id).strip()
        self.engine = engine
        self.session = session or requests.Session()
        self.handler = TelegramCommandHandler(engine=engine, authorized_chat_id=self.authorized_chat_id)
        self.offset = 0
        self.is_running = True
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def poll_once(self):
        try:
            url = f"{self.base_url}/getUpdates"
            params = {"offset": self.offset, "timeout": 10}
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                return

            data = resp.json()
            if not data.get("ok"):
                return

            for update in data.get("result", []):
                update_id = update["update_id"]
                self.offset = update_id + 1

                msg = update.get("message")
                if not msg:
                    continue

                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                text = msg.get("text", "")

                reply = self.handler.handle_message(chat_id=chat_id, text=text)
                if reply and chat_id:
                    self._send_reply(chat_id, reply)
        except Exception as e:
            logger.debug(f"Telegram poll tick error: {e}")

    def _send_reply(self, chat_id: Any, text: str):
        try:
            send_url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            self.session.post(send_url, json=payload, timeout=10)
        except Exception as e:
            logger.warning(f"Failed to send Telegram reply: {e}")

    def run(self):
        logger.info(f"TelegramCommandListener 已启动 (监听来自 chat_id: {self.authorized_chat_id} 的控制指令)...")
        while self.is_running:
            self.poll_once()
            time.sleep(0.5)

    def stop(self):
        self.is_running = False
