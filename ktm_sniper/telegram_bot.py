import os
import logging
import threading
import time
import dataclasses
import shlex
import re
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
    def __init__(self, engine, authorized_chat_id: str, expiry_timeout_seconds: int = 900):
        self.engine = engine
        self.authorized_chat_id = str(authorized_chat_id).strip()
        self.expiry_timeout_seconds = expiry_timeout_seconds
        self._payment_expiry_timer: Optional[threading.Timer] = None

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

        # Support spaceless /book1, /book2, etc.
        book_match = re.match(r"^/book(\d+)$", command)
        if book_match:
            parts = ["/book", book_match.group(1)] + parts[1:]
            command = "/book"

        # Support spaceless /ret_time1, /return_time1, /ret_book1, /rbook1
        ret_book_match = re.match(r"^/(?:ret_time|return_time|ret_book|return_book|rbook)(r?\d+)$", command)
        if ret_book_match:
            parts = ["/return_time", ret_book_match.group(1)] + parts[1:]
            command = "/return_time"

        # Support spaceless /seat3A, /seat3a, etc.
        seat_match = re.match(r"^/seat(\w+)$", command)
        if seat_match:
            parts = ["/seat", seat_match.group(1)]
            command = "/seat"

        # Support spaceless /ret_seat5A, /return_seat5A, /rseat5A
        ret_seat_match = re.match(r"^/(?:return_seat|ret_seat|rseat)(\w+)$", command)
        if ret_seat_match:
            parts = ["/return_seat", ret_seat_match.group(1)]
            command = "/return_seat"

        response = None
        # Handle command routing
        if command in ("/status", "/info", "状态", "info"):
            response = self._handle_status()
        elif command in ("/verify", "/check", "/tickets", "/ticket", "查票", "验票", "确认付款"):
            response = self._handle_verify()
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
        elif command in ("/insurance", "/takaful", "保险", "行程保险"):
            response = self._handle_insurance(parts[1:])
        elif command == "/set":
            response = self._handle_set(parts[1:])
        elif command in ("/return", "/return_date", "/ret_date", "返程", "返程日期"):
            response = self._handle_set(["return_date"] + parts[1:])
        elif command in ("/return_time", "/ret_time", "/ret_book", "/return_book", "/rbook", "返程时间", "返程时段", "选返程"):
            response = self._handle_return_time(parts[1:])
        elif command in ("/return_train", "/return_trains", "/ret_train", "返程车次"):
            response = self._handle_set(["return_trains"] + parts[1:])
        elif command in ("/return_seat", "/ret_seat", "/rseat", "返程座位", "选返程座位", "返程选座"):
            response = self._handle_return_seat(parts[1:])
        elif command in ("/date", "出发日期"):
            response = self._handle_set(["date"] + parts[1:])
        elif command in ("/book", "订票", "选车"):
            response = self._handle_book(parts[1:])
        elif command in ("/seat", "/seats", "选座", "订座", "/sear"):
            response = self._handle_seat(parts[1:])
        elif command in ("/cancel", "取消", "放弃"):
            response = self._handle_cancel_selection()
        elif command in ("/logout", "登出", "退出登录", "释放锁"):
            response = self._handle_logout()
        elif command in ("/cookie", "/set_cookie", "更新cookie"):
            response = self._handle_set_cookie(clean_text)
        elif re.match(r"^r\d+$", command) and getattr(self.engine, "last_found_return_trips", None):
            response = self._handle_return_time([command[1:]])
        elif command.isdigit() and getattr(self.engine, "last_found_trips", None):
            response = self._handle_book([command])
        elif getattr(self.engine, "selected_trip", None) and (
            re.match(r"^\d+[a-zA-Z]$", command) or command in ("auto", "靠窗", "走道", "自动")
        ):
            response = self._handle_seat([command])
        else:
            response = (
                "💡 未知指令。您可以发送：\n"
                "• `/status` - 查看当前抢票与监听状态\n"
                "• `/verify` - 核验 KTMB 官网是否已付款出票 (获取 K/T 官方票号)\n"
                "• `/book <序号>` - 选择并预订心仪车次 (例如: `/book 1` 或直接发 `1`)\n"
                "• `/seat <座位号>` - 指定座位 (例如: `/seat 3A` 或直接发 `3A` 或 `auto`)\n"
                "• `/set <字段> <值>` - 动态修改行程\n"
                "• `/add_passenger <姓名> <证件号>` - 增加乘车人\n"
                "• `/passengers` - 查看当前乘车人列表\n"
                "• `/pause` - 暂停监控\n"
                "• `/resume` - 恢复监控\n"
                "• `/help` - 查看完整使用帮助"
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

        rt_info = "否 (单程)"
        ret_date_line = ""
        if s.get("return_date"):
            ret_trains = getattr(self.engine.task, "return_preferred_trains", None) or []
            ret_trains_str = ", ".join(ret_trains) if ret_trains else "不限 (AUTO 自动匹配)"
            ret_seat_pref = getattr(self.engine.task, "return_seat_preference", None) or getattr(self.engine, "selected_return_seat", None) or "未指定 (AUTO 自动匹配)"
            rt_info = f"是 (返程 {s['return_date']}, {s['return_time_window']})"
            ret_date_line = (
                f"• *返程日期*: `{s['return_date']}`\n"
                f"• *返程时段*: {s['return_time_window']}\n"
                f"• *返程车次*: `{ret_trains_str}`\n"
                f"• *返程座位*: `{ret_seat_pref}`\n"
            )

        passengers_list = []
        for p in self.engine.task.passengers:
            passengers_list.append(f"{p.name} (`{p.masked_id}`)")
        passengers_str = ", ".join(passengers_list) if passengers_list else "未指定"

        trains_filter = ", ".join(self.engine.task.preferred_trains) if self.engine.task.preferred_trains else "不限 (全时段车次)"
        classes_filter = ", ".join(self.engine.task.preferred_classes) if self.engine.task.preferred_classes else "不限 (全席别)"
        ins_flag = bool(getattr(self.engine.task, "include_insurance", False))
        ins_desc = "已开启 (购买)" if ins_flag else "默认不购买 (免除额外保费)"
        ins_hint = "/insurance no" if ins_flag else "/insurance yes"

        return (
            f"🚄 *KTMB 抢票守护状态报告* 🚄\n\n"
            f"• *运行状态*: {status_icon} {status_desc}\n"
            f"• *去程路线*: *{s['origin']}* ➡️ *{s['destination']}*\n"
            f"• *出发日期*: `{s['date']}`\n"
            f"• *出发时段*: {s['time_window']}\n"
            f"• *去程座位偏好*: `{self.engine.task.seat_preference}`\n"
            f"{ret_date_line}"
            f"• *车次偏好*: `{trains_filter}`\n"
            f"• *车型席别*: `{classes_filter}`\n"
            f"• *双程往返*: {rt_info}\n"
            f"• *已轮询周期*: {s['total_cycles']} 次\n"
            f"• *乘车人*: {passengers_str}\n"
            f"• *行程保险 (Takaful)*: {ins_desc} (发 `{ins_hint}` 可切换)\n"
            f"• *数据存储*: SQLite (脱敏与加密守护已开启)\n\n"
            f"💡 发送 `/set trains any` 可取消车次限定；发送 `/set time 00:00-23:59` 监控全天车次。"
        )

    def _handle_insurance(self, args: list) -> str:
        current_task = self.engine.task
        cur_ins = bool(getattr(current_task, "include_insurance", False))

        if not args:
            status_txt = "🟢 *已开启 (自动加购)*" if cur_ins else "⚪ *未开启 (默认不购买，免除额外保费)*"
            return (
                f"🛡️ *【KTMB Takaful 行程保险设置】* 🛡️\n\n"
                f"• *当前设置*: {status_txt}\n"
                f"• *官方说明*: KTMB 提供每人每程约 RM 2.50 的 Takaful 旅程保险。\n\n"
                f"👉 *人工确认/修改保险偏好*:\n"
                f"• 若**需要**购买保险：回复 `/insurance yes` 或 `/insurance on`\n"
                f"• 若**不要**购买保险：回复 `/insurance no` 或 `/insurance off`\n\n"
                f"💡 设定后将持久保存在 SQLite，机器人在下单锁座时会自动为您处理官网保险弹窗！"
            )

        val = " ".join(args).strip().lower()
        if val in ("yes", "y", "on", "true", "1", "要", "购买", "开启", "买"):
            new_task = dataclasses.replace(current_task, include_insurance=True)
            self.engine.update_task(new_task)
            return (
                "✅ *已设置：购买 Takaful 行程保险 (YES)*！\n"
                "• 下单时将自动为您勾选官方旅程保险（每人每程约 RM 2.50）。\n"
                "• 如需改为不买，随时发送 `/insurance no`。"
            )
        elif val in ("no", "n", "off", "false", "0", "不要", "不买", "关闭", "免除", "取消"):
            new_task = dataclasses.replace(current_task, include_insurance=False)
            self.engine.update_task(new_task)
            return (
                "✅ *已设置：不购买 Takaful 行程保险 (NO)*！\n"
                "• 下单时将自动为您跳过保险弹窗并免除额外保费。\n"
                "• 如需开启购买，随时发送 `/insurance yes`。"
            )
        else:
            return (
                f"⚠️ 无法识别的保险设置参数 `{val}`。\n"
                f"• 购买保险请回复：`/insurance yes`\n"
                f"• 不买保险请回复：`/insurance no`"
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
                "• `/set return_date 2027-02-08` (设置返程日期)\n"
                "• `/set return_time 14:00-20:00` (设置返程时段)\n"
                "• `/set return_trains 9041` (设置返程车次)"
            )

        field = args[0].lower()
        value = " ".join(args[1:]).strip()

        try:
            current_task = self.engine.task

            if field in ("origin", "from", "出发地", "出发站", "始发站"):
                code, name = KTMStationRegistry.resolve_station(value)
                new_task = dataclasses.replace(current_task, origin=name)
                self.engine.update_task(new_task)
                return f"✓ 出发地已更新并写入 SQLite: *{name}* [{code}]"

            elif field in ("dest", "destination", "to", "目的地", "抵达地", "到达站", "终点站"):
                code, name = KTMStationRegistry.resolve_station(value)
                new_task = dataclasses.replace(current_task, destination=name)
                self.engine.update_task(new_task)
                return f"✓ 目的地已更新并写入 SQLite: *{name}* [{code}]"

            elif field in ("date", "出发日期", "日期"):
                norm_date = normalize_date(value)
                if current_task.return_date and current_task.return_date < norm_date:
                    new_task = dataclasses.replace(
                        current_task,
                        date=norm_date,
                        is_round_trip=False,
                        return_date=None
                    )
                    self.engine.update_task(new_task)
                    return f"✓ 出发日期已更新并写入 SQLite: *{norm_date}*（原返程日期早于新出发日期已自动重置，请重新设置 `/return <日期>`）"
                new_task = dataclasses.replace(current_task, date=norm_date)
                self.engine.update_task(new_task)
                ret_suffix = f"\n• 返程日期: *{new_task.return_date}* (往返双程)" if new_task.return_date else ""
                return f"✓ 出发日期已更新并写入 SQLite: *{norm_date}*{ret_suffix}"

            elif field in ("time", "时段", "出发时间", "时间"):
                if "-" in value:
                    times = value.replace(" ", "").split("-")
                    new_task = dataclasses.replace(current_task, time_from=times[0], time_to=times[1])
                    self.engine.update_task(new_task)
                    return f"✓ 出发时段已更新并写入 SQLite: *{times[0]} - {times[1]}*"
                else:
                    new_task = dataclasses.replace(current_task, time_from=value.strip())
                    self.engine.update_task(new_task)
                    return f"✓ 最早出发时间已更新并写入 SQLite: *{value.strip()}*"

            elif field in ("time_from", "depart_time", "最早出发"):
                new_task = dataclasses.replace(current_task, time_from=value.strip())
                self.engine.update_task(new_task)
                return f"✓ 最早出发时间已更新并写入 SQLite: *{value.strip()}*"

            elif field in ("time_to", "arrive_time", "抵达时间", "最晚出发", "结束时段"):
                new_task = dataclasses.replace(current_task, time_to=value.strip())
                self.engine.update_task(new_task)
                return f"✓ 最晚时段/抵达时间已更新并写入 SQLite: *{value.strip()}*"

            elif field in ("return_date", "ret_date", "返程日期"):
                if value.lower() in ("none", "clear", "off", "null", "0", "单程", "取消", "不限"):
                    new_task = dataclasses.replace(
                        current_task,
                        is_round_trip=False,
                        return_date=None,
                        leg_type="ONE_WAY"
                    )
                    self.engine.selected_return_trip = None
                    self.engine.last_found_return_trips = []
                    self.engine.update_task(new_task)
                    return f"✓ 已清除返程日期，切换为单程模式（出发日期: *{new_task.date}*）。"

                ret_parts = value.split()
                norm_ret_date = normalize_date(ret_parts[0])
                new_task = dataclasses.replace(
                    current_task,
                    is_round_trip=True,
                    return_date=norm_ret_date
                )
                self.engine.selected_return_trip = None
                self.engine.update_task(new_task)

                extra_time_msg = ""
                if len(ret_parts) > 1:
                    extra_time_msg = "\n\n" + self._handle_return_time(ret_parts[1:])
                    new_task = self.engine.task

                ret_trains_list = self._ensure_return_trips(ignore_time_filter=True)
                ret_choices_block = self._format_return_time_choices(ret_trains_list, norm_ret_date)

                ret_trains = ", ".join(new_task.return_preferred_trains) if new_task.return_preferred_trains else "未选择 (AUTO 自动匹配)"
                return (
                    f"✓ 往返模式已激活:\n"
                    f"• 出发日期: *{new_task.date}*\n"
                    f"• 返程日期: *{norm_ret_date}* (往返双程)\n"
                    f"• 返程时段: `{new_task.return_time_from} - {new_task.return_time_to}`\n"
                    f"• 返程车次: `{ret_trains}`\n"
                    f"• 返程座位: `未选择 (AUTO 自动分配)`"
                    f"{extra_time_msg}"
                    f"{ret_choices_block}"
                )

            elif field in ("return_time", "ret_time", "返程时间", "返程时段"):
                return self._handle_return_time(args[1:])

            elif field in ("return_train", "return_trains", "ret_train", "返程车次"):
                if value.lower() in ("any", "all", "none", "clear", "auto", "所有", "不限", "0"):
                    new_ret_trains = []
                    self.engine.selected_return_trip = None
                    msg = "✓ 返程车次已设为: *不限 (AUTO 自动匹配)*"
                else:
                    new_ret_trains = [t.strip().upper() for t in re.split(r"[, ]+", value) if t.strip()]
                    msg = f"✓ 返程目标车次已更新为: *{', '.join(new_ret_trains)}*"
                new_task = dataclasses.replace(current_task, return_preferred_trains=new_ret_trains)
                self.engine.update_task(new_task)
                return msg

            elif field in ("seats", "seat_count", "人数", "席位数"):
                seats = int(value)
                if seats < 1:
                    return "⚠️ 席位数必须大于等于 1。"
                new_task = dataclasses.replace(current_task, required_seats=seats)
                self.engine.update_task(new_task)
                return f"✓ 锁定席位数已更新为: *{seats}*"

            elif field in ("train", "trains", "车次", "车次偏好"):
                if value.lower() in ("any", "all", "none", "clear", "所有", "不限", "0"):
                    new_trains = []
                    msg = "✓ 车次筛选已清除，将监控时段内的*所有车次*！"
                else:
                    new_trains = [t.strip().upper() for t in re.split(r"[, ]+", value) if t.strip()]
                    msg = f"✓ 目标车次已更新为: *{', '.join(new_trains)}*"
                new_task = dataclasses.replace(current_task, preferred_trains=new_trains)
                self.engine.update_task(new_task)
                return msg

            elif field in ("class", "classes", "等级", "车型"):
                if value.lower() in ("any", "all", "none", "clear", "所有", "不限", "0"):
                    new_classes = []
                    msg = "✓ 车型席别已设为*不限*（任意 Platinum/Gold/Express 均可）。"
                else:
                    new_classes = [c.strip() for c in re.split(r"[,]+", value) if c.strip()]
                    msg = f"✓ 目标席别已更新为: *{', '.join(new_classes)}*"
                new_task = dataclasses.replace(current_task, preferred_classes=new_classes)
                self.engine.update_task(new_task)
                return msg

            elif field in ("insurance", "takaful", "ins", "保险", "行程保险"):
                return self._handle_insurance(args[1:])

            elif field in ("return_seat", "return_seats", "ret_seat", "返程座位", "返程座号"):
                return self._handle_return_seat(args[1:])

            elif field in ("seat", "seats_preference", "seat_pref", "座位偏好", "去程座位"):
                new_task = dataclasses.replace(current_task, seat_preference=value.strip())
                self.engine.update_task(new_task)
                return f"✓ 去程座位偏好已更新为: *{value.strip()}*"

            else:
                return f"⚠️ 未知设置项 '{field}'。支持的字段：origin, dest, date, time, return_date, seats, trains, class, insurance, return_seat。"
        except Exception as e:
            return f"❌ 设置失败: {e}"

    @staticmethod
    def parse_passenger_from_text(text: str) -> Passenger:
        tokens = shlex.split(text) if ('"' in text or "'" in text) else text.split()
        if tokens and tokens[0].startswith(("/", "添加", "增加", "加人")):
            tokens = tokens[1:]

        if not tokens:
            raise ValueError("缺少乘客信息，格式：`/add_passenger <姓名> <证件号> [电话] [性别]`")

        gender = None
        phone = None
        id_number = None

        # 1. Extract gender if present
        for i, tok in enumerate(tokens):
            t_clean = tok.strip().upper()
            if t_clean in ("MALE", "M", "MAN", "BOY", "男", "先生"):
                gender = "Male"
                tokens.pop(i)
                break
            elif t_clean in ("FEMALE", "F", "WOMAN", "GIRL", "女", "女士"):
                gender = "Female"
                tokens.pop(i)
                break

        # 2. Extract Malaysian IC (12 digits) or standard Passport
        for i, tok in enumerate(tokens):
            t_clean = re.sub(r"[^A-Za-z0-9]", "", tok)
            if len(t_clean) == 12 and t_clean.isdigit():
                id_number = tok.strip()
                if not gender:
                    gender = "Male" if int(t_clean[-1]) % 2 != 0 else "Female"
                tokens.pop(i)
                break
            elif re.match(r"^[A-Za-z]\d{7,9}$", t_clean):
                id_number = tok.strip()
                tokens.pop(i)
                break

        # 3. Extract Phone Number (10-11 digits or starting with 01/+60)
        for i, tok in enumerate(tokens):
            t_clean = re.sub(r"[^0-9+]", "", tok)
            if re.match(r"^(\+?601|01)\d{7,9}$", t_clean) or (len(t_clean) in (10, 11) and t_clean.startswith("0")):
                phone = tok.strip()
                tokens.pop(i)
                break

        # If ID still not matched, check for any token with digits >= 6
        if not id_number:
            for i, tok in enumerate(tokens):
                if any(c.isdigit() for c in tok) and len(tok) >= 6:
                    id_number = tok.strip()
                    tokens.pop(i)
                    break

        name = " ".join(tokens).strip()
        if not name:
            name = "Passenger"
        if not id_number:
            raise ValueError("未能识别有效证件号码 (大马身份证需12位数字，例如 `960217075045`)")
        if not phone:
            phone = "0123456789"
        if not gender:
            gender = "Male"

        return Passenger(name=name, id_number=id_number, gender=gender, phone=phone)

    def _handle_add_passenger(self, text: str) -> str:
        try:
            new_passenger = self.parse_passenger_from_text(text)

            current_task = self.engine.task
            new_passengers = list(current_task.passengers) + [new_passenger]
            new_task = dataclasses.replace(
                current_task,
                passengers=new_passengers,
                required_seats=len(new_passengers)
            )
            self.engine.update_task(new_task)

            gender_label = "男 (Male)" if new_passenger.gender.lower() in ("male", "m") else "女 (Female)"
            return (
                f"✓ 已成功添加乘车人:\n"
                f"• *姓名*: *{new_passenger.name}*\n"
                f"• *证件*: `{new_passenger.masked_id}` | {gender_label}\n"
                f"• *电话*: `{new_passenger.masked_phone}`\n\n"
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
        if hasattr(self.engine, "repository") and self.engine.repository:
            try:
                self.engine.repository.clear_saved_passengers()
            except Exception:
                pass
        return "✓ 已清空全部乘车人预填列表并同步清除 SQLite。如需添加，发送 `/add_passenger <姓名> <证件号>`。"

    def _handle_help(self) -> str:
        return (
            "📱 *KTM Sniper Telegram 手机远程控制菜单* 📱\n\n"
            "🔍 *查看与验票*:\n"
            "• `/status` - 查看当前监听路线、时段与轮询次数\n"
            "• `/verify` - 扫码付款后核验是否真实出票 (提取 K 开头订单号与 T 开头车票号)\n"
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
            "• `/set return_date <日期>` - 设置返程日期激活往返 (如 `/set return_date 2027-02-08`)\n"
            "• `/return_time [序号/时间点/时段]` - 查看或选择返程日期时间点 (如 `/return_time`、`/ret_time 1` 或 `/return_time 07:11`)\n"
            "• `/ret_seat <座位号>` - 单独选择返程座位 (如 `/ret_seat 5A` 或 `/ret_seat auto`)\n"
            "• `/seat <去程> / <返程>` - 一步指定双程座位 (如 `/seat 3A / 5A` 或 `/seat auto`)\n"
            "• `/insurance [yes/no]` - 开启或关闭 Takaful 旅程保险 (默认不买，免除额外保费)\n\n"
            "⏸️ *运行控制*:\n"
            "• `/pause` - 暂停监听 (节省服务器请求与流量)\n"
            "• `/resume` - 恢复监听\n"
            "• `/logout` - 释放 KTMB 官方登录锁 (方便手机 App 或其他设备随时登录)\n"
            "• `/help` - 呼出本帮助菜单"
        )

    def _ensure_return_trips(self, ignore_time_filter: bool = False) -> list:
        if not getattr(self.engine.task, "is_round_trip", False) and not getattr(self.engine.task, "return_date", None):
            return []
        cached = getattr(self.engine, "last_found_return_trips", None)
        if cached and not ignore_time_filter:
            return list(cached)
        if hasattr(self.engine, "fetch_return_trips"):
            try:
                trips = self.engine.fetch_return_trips(ignore_time_filter=ignore_time_filter)
                if trips:
                    return list(trips)
            except Exception as e:
                logger.warning(f"Fetch return trips error: {e}")
        return list(cached) if cached else []

    def _format_return_time_choices(self, return_trips: list, ret_date: str) -> str:
        if not return_trips:
            return (
                f"\n\n🛬 *返程时间点选择* (`{ret_date}`):\n"
                f"• 发送 `/return_time <时间点>` (如 `/return_time 07:11`) 指定精确返程时间点\n"
                f"• 发送 `/return_time <起始>-<截止>` (如 `/return_time 08:00-18:00`) 指定返程时段\n"
                f"• 发送 `/return_time any` 恢复全天自动匹配"
            )
        lines = [f"\n\n🛬 *【返程日期 ({ret_date}) 可选时间点】*:"]
        for idx, rt in enumerate(return_trips[:6], start=1):
            t_no = getattr(rt, "train_no", rt.get("train_no") if isinstance(rt, dict) else "ETS")
            t_cls = getattr(rt, "train_class", rt.get("train_class", rt.get("class")) if isinstance(rt, dict) else "ETS")
            dep = getattr(rt, "departure_time", rt.get("departure_time") if isinstance(rt, dict) else "")
            arr = getattr(rt, "arrival_time", rt.get("arrival_time") if isinstance(rt, dict) else "")
            seats = getattr(rt, "available_seats", rt.get("available_seats") if isinstance(rt, dict) else 0)
            fare = getattr(rt, "fare", rt.get("fare") if isinstance(rt, dict) else 0.0)
            lines.append(
                f"  `[R{idx}]` *{dep}* ➡️ *{arr}* | {t_cls} (*{t_no}*) | 余 *{seats}* 席 (MYR {fare:.2f})\n"
                f"       👉 发送 `/ret_time {idx}` 或 `/return_time {dep}` 选择此时间点"
            )
        lines.append("💡 也可发送 `/return_time 08:00-18:00` 设定时段，或 `/return_time any` 恢复全天自动匹配。")
        return "\n".join(lines)

    def _handle_return_time(self, args: list) -> str:
        current_task = self.engine.task
        ret_date = getattr(current_task, "return_date", None)
        if not args:
            if not ret_date:
                return (
                    "⚠️ 当前尚未设置返程日期（单程模式）。\n"
                    "请先发送 `/return <YYYY-MM-DD>`（例如 `/return 2026-10-27`）开启往返模式，即可查看并选择返程时间点！"
                )
            ret_trips = self._ensure_return_trips(ignore_time_filter=True)
            cur_tf = getattr(current_task, "return_time_from", "00:00") or "00:00"
            cur_tt = getattr(current_task, "return_time_to", "23:59") or "23:59"
            cur_trains = ", ".join(current_task.return_preferred_trains) if current_task.return_preferred_trains else "不限 (AUTO)"
            header = (
                f"🛬 *【返程时间点与车次选择】*\n"
                f"• *返程路线*: *{current_task.destination}* ➡️ *{current_task.origin}*\n"
                f"• *返程日期*: `{ret_date}`\n"
                f"• *当前返程时段*: `{cur_tf} - {cur_tt}`\n"
                f"• *当前返程车次*: `{cur_trains}`"
            )
            return header + self._format_return_time_choices(ret_trips, ret_date)

        value = " ".join(args).strip()
        # Clean up any trailing backslashes, slashes, or quotes (e.g. from /book 1 1 \)
        clean_val = re.sub(r"[\\/\"']+$", "", value).strip()
        if not clean_val:
            clean_val = value.strip()
        val_lower = clean_val.lower()

        # 1. Reset / clear return time filter
        if val_lower in ("any", "all", "none", "clear", "auto", "所有", "不限", "全天", "0"):
            new_task = dataclasses.replace(
                current_task,
                return_time_from="00:00",
                return_time_to="23:59",
                return_preferred_trains=[],
            )
            self.engine.selected_return_trip = None
            self.engine.update_task(new_task)
            return "✓ 返程时段与车次已重置为: *00:00 - 23:59 (AUTO 自动匹配)*"

        # 2. Time window range (e.g. 08:00-18:00)
        if "-" in clean_val:
            times = clean_val.replace(" ", "").split("-")
            t_from, t_to = times[0].strip(), times[1].strip()
            new_task = dataclasses.replace(
                current_task,
                return_time_from=t_from,
                return_time_to=t_to,
            )
            self.engine.selected_return_trip = None
            self.engine.update_task(new_task)
            return f"✓ 返程时段已更新为: *{t_from} - {t_to}*"

        # 3. Select by option index (e.g. /ret_time 1 or /ret_time R1)
        idx_match = re.match(r"^[rR]?(\d+)$", clean_val)
        ret_trips = self._ensure_return_trips(ignore_time_filter=True)
        if idx_match and len( idx_match.group(1) ) <= 2:
            idx = int(idx_match.group(1))
            if ret_trips and 1 <= idx <= len(ret_trips):
                chosen = ret_trips[idx - 1]
                t_no = str(getattr(chosen, "train_no", chosen.get("train_no") if isinstance(chosen, dict) else "")).strip()
                t_cls = str(getattr(chosen, "train_class", chosen.get("train_class", chosen.get("class")) if isinstance(chosen, dict) else "ETS")).strip()
                dep = str(getattr(chosen, "departure_time", chosen.get("departure_time") if isinstance(chosen, dict) else "")).strip()
                arr = str(getattr(chosen, "arrival_time", chosen.get("arrival_time") if isinstance(chosen, dict) else "")).strip()
                self.engine.selected_return_trip = chosen
                new_task = dataclasses.replace(
                    current_task,
                    return_time_from=dep or current_task.return_time_from,
                    return_time_to=dep or current_task.return_time_to,
                    return_preferred_trains=[t_no] if t_no else current_task.return_preferred_trains,
                )
                self.engine.update_task(new_task)
                arr_str = f" ➡️ *{arr}*" if arr else ""
                return (
                    f"✅ *已选定返程时间点与车次*:\n"
                    f"• *返程日期*: `{ret_date or '未设'}`\n"
                    f"• *返程时间点*: *{dep}*{arr_str}\n"
                    f"• *返程车次*: *{t_no}* ({t_cls})\n\n"
                    f"💡 接下来发送 `/seat auto`（或先 `/book 1` 再 `/seat auto`）将严格按此返程时间点为您锁定双程车票！"
                )

        # 4. Select by exact time point (e.g. 07:11) or train number (e.g. 9323)
        matched_trip = None
        for rt in ret_trips:
            t_no = str(getattr(rt, "train_no", rt.get("train_no") if isinstance(rt, dict) else "")).strip()
            dep = str(getattr(rt, "departure_time", rt.get("departure_time") if isinstance(rt, dict) else "")).strip()
            if clean_val == dep or clean_val.upper() == t_no.upper():
                matched_trip = rt
                break

        if matched_trip is not None:
            t_no = str(getattr(matched_trip, "train_no", matched_trip.get("train_no") if isinstance(matched_trip, dict) else "")).strip()
            t_cls = str(getattr(matched_trip, "train_class", matched_trip.get("train_class", matched_trip.get("class")) if isinstance(matched_trip, dict) else "ETS")).strip()
            dep = str(getattr(matched_trip, "departure_time", matched_trip.get("departure_time") if isinstance(matched_trip, dict) else "")).strip()
            arr = str(getattr(matched_trip, "arrival_time", matched_trip.get("arrival_time") if isinstance(matched_trip, dict) else "")).strip()
            self.engine.selected_return_trip = matched_trip
            new_task = dataclasses.replace(
                current_task,
                return_time_from=dep,
                return_time_to=dep,
                return_preferred_trains=[t_no] if t_no else current_task.return_preferred_trains,
            )
            self.engine.update_task(new_task)
            arr_str = f" ➡️ *{arr}*" if arr else ""
            return (
                f"✓ 返程时间点已选定为: *{dep}*{arr_str} | 车次 *{t_no}* ({t_cls})\n"
                f"• 返程时段已锁定: `{dep} - {dep}`"
            )

        # 5. Direct HH:MM time point when return trips aren't cached yet
        if re.match(r"^\d{1,2}:\d{2}$", clean_val):
            parts_hm = clean_val.split(":")
            norm_hm = f"{int(parts_hm[0]):02d}:{parts_hm[1]}"
            new_task = dataclasses.replace(
                current_task,
                return_time_from=norm_hm,
                return_time_to=norm_hm,
            )
            self.engine.update_task(new_task)
            return f"✓ 返程时间点已选定为: *{norm_hm}* (返程时段: `{norm_hm} - {norm_hm}`)"

        # Fallback only if clean_val matches HH:MM format
        if re.match(r"^\d{1,2}:\d{2}$", clean_val):
            new_task = dataclasses.replace(current_task, return_time_from=clean_val)
            self.engine.update_task(new_task)
            return f"✓ 返程最早出发时间已更新为: *{clean_val}*"
        return f"⚠️ 无法识别的返程时间设置: `{clean_val}`。请发送如 `/ret_time 1` 或 `/return_time 07:11`。"

    def _handle_return_seat(self, args: list) -> str:
        current_task = self.engine.task
        ret_date = getattr(current_task, "return_date", None)
        if not ret_date:
            return (
                "⚠️ 当前尚未设置返程日期（单程模式）。\n"
                "请先发送 `/return <YYYY-MM-DD>`（例如 `/return 2026-10-27`）开启往返模式，即可选择返程座位！"
            )

        if not args:
            cur_seat = getattr(current_task, "return_seat_preference", None) or getattr(self.engine, "selected_return_seat", None) or "AUTO (自动分配同行位)"
            ret_coaches = getattr(self.engine, "available_return_seats_cache", None)
            sel_ret_trip = getattr(self.engine, "selected_return_trip", None)
            ret_t_no = getattr(sel_ret_trip, "train_no", sel_ret_trip.get("train_no") if isinstance(sel_ret_trip, dict) else None) if sel_ret_trip else None
            if not ret_t_no and getattr(current_task, "return_preferred_trains", None):
                ret_t_no = current_task.return_preferred_trains[0]
            ret_trips = self._ensure_return_trips(ignore_time_filter=True)
            if not ret_t_no and ret_trips:
                ret_t_no = getattr(ret_trips[0], "train_no", ret_trips[0].get("train_no") if isinstance(ret_trips[0], dict) else None)

            if not ret_coaches and ret_t_no and hasattr(self.engine, "fetch_return_seats_layout"):
                try:
                    ret_coaches = self.engine.fetch_return_seats_layout(ret_t_no)
                    self.engine.available_return_seats_cache = ret_coaches
                except Exception as e:
                    logger.warning(f"Fetch return seats layout error: {e}")

            coach_summary = ""
            if ret_coaches and isinstance(ret_coaches, dict):
                lines = ["\n🛬 *【返程车次可选车厢与座位】*:"]
                for c_name, c_seats in list(ret_coaches.items())[:3]:
                    if isinstance(c_seats, dict):
                        win = ", ".join(c_seats.get("window", [])[:8]) or "暂无"
                        ais = ", ".join(c_seats.get("aisle", [])[:8]) or "暂无"
                    else:
                        win = ", ".join(str(s) for s in c_seats[:8]) or "暂无"
                        ais = "暂无"
                    lines.append(f"  • *Coach {c_name}*: 靠窗 `{win}` | 走道 `{ais}`")
                coach_summary = "\n".join(lines) + "\n"

            return (
                f"💺 *【返程座位选择】* 💺\n\n"
                f"• *返程日期*: `{ret_date}`\n"
                f"• *返程车次*: `{ret_t_no or 'AUTO 自动匹配'}`\n"
                f"• *当前返程座位设定*: *{cur_seat}*\n"
                f"{coach_summary}\n"
                f"👉 *如何设置返程座位*:\n"
                f"• 回复 `/ret_seat <座位号>` (例如: `/ret_seat 5A` 或 `/ret_seat Coach B - 05A`)\n"
                f"• 回复 `/ret_seat auto` (由系统自动优先安排同行相邻座位)\n"
                f"• 回复 `/ret_seat window` (指定靠窗) 或 `/ret_seat aisle` (指定走道)\n"
                f"• 也可在选座时一步合并指定双程座位：回复 `/seat 3A / 5A`"
            )

        seat_val = " ".join(args).strip().upper()
        if seat_val in ("AUTO", "DEFAULT", "ANY", "自动", "不限"):
            self.engine.selected_return_seat = "AUTO"
            new_task = dataclasses.replace(current_task, return_seat_preference=None)
            self.engine.update_task(new_task)
            return "✓ 返程座位已重置为: *AUTO (系统自动优选同行位)*"

        self.engine.selected_return_seat = seat_val
        new_task = dataclasses.replace(current_task, return_seat_preference=seat_val)
        self.engine.update_task(new_task)
        return (
            f"✅ *返程座位已选定*: *{seat_val}*！\n\n"
            f"• 返程座位偏好已保存至 SQLite。\n"
            f"💡 下一步：回复 `/seat <去程座位>`（例如 `/seat 3A` 或直接回复 `/seat auto`），系统将同时使用去程座位与已选返程座位（*{seat_val}*）锁定双程车票！"
        )

    def _handle_book(self, args: list) -> str:
        if not args:
            return "💡 请指定要预订的车次序号或车次号，例如: `/book 1` 或 `/book 9044`。"

        target = args[0].strip()
        found_trip = None

        pending = getattr(self.engine, "last_found_trips", [])
        if target.isdigit() and 1 <= int(target) <= len(pending):
            found_trip = pending[int(target) - 1]
        else:
            for t in pending:
                t_no = getattr(t, "train_no", t.get("train_no") if isinstance(t, dict) else "")
                dep_t = getattr(t, "departure_time", t.get("departure_time") if isinstance(t, dict) else "")
                if target.upper() == str(t_no).upper() or target == str(dep_t):
                    found_trip = t
                    break

        if not found_trip:
            if pending:
                return f"⚠️ 未找到指定班次 '{target}'。请回复 `/book 1` 到 `/book {len(pending)}` 之间的序号。"
            return "💡 当前没有等待确认的车次。守护引擎发现余票时会主动向您推送候选清单！"

        self.engine.selected_trip = found_trip

        # Optional 2nd argument: select return trip/time at the same time (e.g. /book 1 1 or /book 1 07:11)
        if len(args) > 1 and getattr(self.engine.task, "return_date", None):
            self._handle_return_time(args[1:])

        # Fetch seat layout
        train_no = getattr(found_trip, "train_no", found_trip.get("train_no") if isinstance(found_trip, dict) else "9044")
        dep_time = getattr(found_trip, "departure_time", found_trip.get("departure_time") if isinstance(found_trip, dict) else "00:00")
        dep_date = getattr(self.engine.task, "date", None)
        ret_date = getattr(self.engine.task, "return_date", None)
        coaches = self.engine.fetch_seats_layout(train_no)
        self.engine.available_seats_cache = coaches

        ret_trips = self._ensure_return_trips(ignore_time_filter=True) if ret_date else []
        sel_ret_trip = getattr(self.engine, "selected_return_trip", None)
        ret_tf = getattr(self.engine.task, "return_time_from", "00:00") or "00:00"
        ret_tt = getattr(self.engine.task, "return_time_to", "23:59") or "23:59"
        ret_win = f"{ret_tf} - {ret_tt}"

        # Fetch return seat layout if return train is selected or cached
        ret_coaches = None
        if ret_date:
            ret_t_no = getattr(sel_ret_trip, "train_no", sel_ret_trip.get("train_no") if isinstance(sel_ret_trip, dict) else None) if sel_ret_trip else None
            if not ret_t_no and getattr(self.engine.task, "return_preferred_trains", None):
                ret_t_no = self.engine.task.return_preferred_trains[0]
            if not ret_t_no and ret_trips:
                ret_t_no = getattr(ret_trips[0], "train_no", ret_trips[0].get("train_no") if isinstance(ret_trips[0], dict) else None)
            if ret_t_no and hasattr(self.engine, "fetch_return_seats_layout"):
                try:
                    ret_coaches = self.engine.fetch_return_seats_layout(ret_t_no)
                    self.engine.available_return_seats_cache = ret_coaches
                except Exception as e:
                    logger.warning(f"Fetch return seats layout error: {e}")

        if hasattr(self.engine, "notifier") and self.engine.notifier:
            inc_ins = bool(getattr(self.engine.task, "include_insurance", False))
            cur_ret_seat = getattr(self.engine, "selected_return_seat", None) or getattr(self.engine.task, "return_seat_preference", None)
            try:
                return self.engine.notifier.format_seat_options_message(
                    train_no=train_no,
                    depart_time=dep_time,
                    coaches=coaches,
                    date=dep_date,
                    return_date=ret_date,
                    return_trips=ret_trips,
                    selected_return_trip=sel_ret_trip,
                    return_time_window=ret_win,
                    include_insurance=inc_ins,
                    return_coaches=ret_coaches,
                    selected_return_seat=cur_ret_seat,
                )
            except TypeError:
                return self.engine.notifier.format_seat_options_message(
                    train_no=train_no,
                    depart_time=dep_time,
                    coaches=coaches,
                    date=dep_date,
                    return_date=ret_date,
                )
        return f"✅ 已选定车次 {train_no}！请回复 `/seat <座位号>` (例如: `/seat 3A` 或 `/seat auto`) 确认座位！"

    @staticmethod
    def _auto_select_seats_from_layout(coaches: Optional[dict], target_count: int, pref: str = "AUTO") -> str:
        import re

        if not isinstance(coaches, dict) or not coaches:
            coaches = {
                "B": {
                    "window": ["03A", "03D", "04A", "04D", "05A", "05D"],
                    "aisle": ["03B", "03C", "04B", "04C", "05B", "05C"],
                }
            }

        def _seat_sort_key(seat_str: str):
            m = re.match(r"^(\d+)([A-Z]*)$", seat_str.strip().upper())
            if m:
                return (int(m.group(1)), m.group(2))
            return (9999, seat_str.strip().upper())

        pref_upper = (pref or "AUTO").strip().upper()
        target_count = max(1, target_count)

        coach_pools = []
        for c_lbl, c_data in coaches.items():
            if not isinstance(c_data, dict):
                continue
            win_list = [str(s).strip() for s in (c_data.get("window") or []) if str(s).strip()]
            ais_list = [str(s).strip() for s in (c_data.get("aisle") or []) if str(s).strip()]
            all_sorted = sorted(list(dict.fromkeys(win_list + ais_list)), key=_seat_sort_key)
            if all_sorted:
                coach_pools.append((str(c_lbl).strip(), win_list, ais_list, all_sorted))

        if not coach_pools:
            return ""

        chosen_idx = 0
        for idx, (_, _, _, all_sorted) in enumerate(coach_pools):
            if len(all_sorted) >= target_count:
                chosen_idx = idx
                break
        ordered_coaches = [coach_pools[chosen_idx]] + [cp for i, cp in enumerate(coach_pools) if i != chosen_idx]

        selected = []
        for c_lbl, win_list, ais_list, all_sorted in ordered_coaches:
            need = target_count - len(selected)
            if need <= 0:
                break

            if pref_upper == "WINDOW":
                candidates = win_list + [s for s in ais_list if s not in win_list]
            elif pref_upper == "AISLE":
                candidates = ais_list + [s for s in win_list if s not in ais_list]
            elif target_count == 1:
                candidates = win_list + [s for s in ais_list if s not in win_list]
            else:
                row_groups: dict[int, list[str]] = {}
                for s in all_sorted:
                    m = re.match(r"^(\d+)", s)
                    r_num = int(m.group(1)) if m else 9999
                    row_groups.setdefault(r_num, []).append(s)

                same_row = None
                for r_num in sorted(row_groups.keys()):
                    if len(row_groups[r_num]) >= need:
                        same_row = row_groups[r_num][:need]
                        break
                candidates = same_row if same_row else all_sorted

            for s in candidates:
                if len(selected) >= target_count:
                    break
                formatted = s if "Coach" in s else f"Coach {c_lbl} - {s}"
                if formatted not in selected:
                    selected.append(formatted)

        return ", ".join(selected)

    def _handle_seat(self, args: list) -> str:
        if not hasattr(self.engine, "selected_trip") or not self.engine.selected_trip:
            pending = getattr(self.engine, "last_found_trips", [])
            if pending:
                self.engine.selected_trip = pending[0]
            else:
                return "⚠️ 当前没有等待订票的车次。守护引擎发现余票时会主动向您推送候选清单！"

        if not self.engine.task.passengers:
            return (
                "⚠️ 当前尚未添加乘车人信息！\n\n"
                "请先回复添加乘车人：\n"
                "👉 `/add_passenger TAN JIA HUI 960217075045 MALE`\n\n"
                "添加完成后，再次回复 `/seat 3A` 或 `/seat auto` 即可立即锁定生成订单！"
            )

        seat_raw = " ".join([a.strip().upper() for a in args]) if args else "AUTO"
        outbound_seat_choice = seat_raw
        return_seat_choice = getattr(self.engine, "selected_return_seat", None) or getattr(self.engine.task, "return_seat_preference", None) or "AUTO"

        # Check for combo delimiters like "/", "RET", "RETURN", "返程"
        if "/" in seat_raw:
            parts = [p.strip() for p in seat_raw.split("/", 1)]
            outbound_seat_choice = parts[0] or "AUTO"
            return_seat_choice = parts[1] or "AUTO"
        elif " RET " in seat_raw:
            parts = [p.strip() for p in seat_raw.split(" RET ", 1)]
            outbound_seat_choice = parts[0] or "AUTO"
            return_seat_choice = parts[1] or "AUTO"
        elif " RETURN " in seat_raw:
            parts = [p.strip() for p in seat_raw.split(" RETURN ", 1)]
            outbound_seat_choice = parts[0] or "AUTO"
            return_seat_choice = parts[1] or "AUTO"
        elif " 返程 " in seat_raw or " 返程:" in seat_raw:
            parts = [p.strip() for p in re.split(r" 返程:? ", seat_raw, maxsplit=1)]
            outbound_seat_choice = parts[0] or "AUTO"
            return_seat_choice = parts[1] or "AUTO"
        elif len(args) == 2 and getattr(self.engine.task, "return_date", None) and len(self.engine.task.passengers or []) <= 1:
            outbound_seat_choice = args[0].strip().upper()
            return_seat_choice = args[1].strip().upper()

        if return_seat_choice and return_seat_choice != "AUTO":
            self.engine.selected_return_seat = return_seat_choice

        trip = self.engine.selected_trip
        passengers = list(self.engine.task.passengers or [])
        pax_count = max(1, len(passengers))

        try:
            res = self.engine.execute_real_booking(
                trip=trip,
                seat_no=outbound_seat_choice,
                return_seat_no=return_seat_choice,
            )
            t_no = getattr(trip, "train_no", trip.get("train_no") if isinstance(trip, dict) else "ETS")
            t_cls = getattr(trip, "train_class", trip.get("train_class") if isinstance(trip, dict) else "ETS Gold")
            dep_time = getattr(trip, "departure_time", trip.get("departure_time") if isinstance(trip, dict) else "")
            dep_date = getattr(self.engine.task, "date", None) or (trip.get("date") if isinstance(trip, dict) else getattr(trip, "date", "")) or ""
            booking_id = res.get("booking_id", f"KITS-{t_no}")
            actual_seat = str(res.get("selected_seat") or "").strip()
            ret_date = getattr(self.engine.task, "return_date", None) or (trip.get("return_date") if isinstance(trip, dict) else getattr(trip, "return_date", None))
            is_rt = bool(ret_date)
            if not actual_seat and outbound_seat_choice.upper() in ("AUTO", "WINDOW", "AISLE", "ANY", "DEFAULT"):
                actual_seat = self._auto_select_seats_from_layout(
                    getattr(self.engine, "available_seats_cache", None),
                    target_count=pax_count,
                    pref=outbound_seat_choice.upper(),
                )
                if actual_seat and is_rt and "返程" not in actual_seat:
                    actual_seat = f"{actual_seat} (返程: {actual_seat})"
            if actual_seat:
                if outbound_seat_choice.upper() in ("AUTO", "WINDOW", "AISLE", "ANY", "DEFAULT") and actual_seat.upper() != outbound_seat_choice.upper():
                    seat_display = f"{actual_seat} ({outbound_seat_choice})"
                else:
                    seat_display = actual_seat
            else:
                seat_display = outbound_seat_choice
            pay_url = res.get("payment_url", "")
            upcoming_url = "https://online.ktmb.com.my/Booking/UpcomingList"
            qr_path = res.get("payment_qr")

            # Guard against invalid /Book 405 link
            if not pay_url or pay_url.rstrip("/").lower().endswith("/book"):
                if booking_id and booking_id != "PENDING_PAYMENT" and not booking_id.startswith("KITS-"):
                    pay_url = f"https://online.ktmb.com.my/Payment/Checkout?bookingId={booking_id}"
                else:
                    pay_url = upcoming_url

            payment_amount = str(res.get("payment_amount") or "").strip()
            payment_ref = str(res.get("payment_ref") or "").strip()
            if not booking_id or booking_id == "PENDING_PAYMENT":
                booking_id_line = "• *官方订单号*: `待付款后生成` (付款成功后发 `/verify` 获取 K 开头官方票号)\n"
            elif booking_id.upper().startswith("GHP"):
                booking_id_line = f"• *官方订单号*: `{booking_id}` (支付商户单号，付款后发 `/verify` 提取 K 开头车票号)\n"
            else:
                booking_id_line = f"• *官方订单号*: `{booking_id}`\n"
            if payment_ref:
                booking_id_line += f"• *网关流水号*: `{payment_ref}`\n"
            amount_line = f"• *应付总额*: `RM {payment_amount}`\n" if payment_amount else ""

            # Reset selection state
            sel_ret = getattr(self.engine, "selected_return_trip", None)
            self.engine.selected_trip = None
            self.engine.selected_return_trip = None
            self.engine.last_found_trips = []

            p_name = "Passenger"
            if len(passengers) > 1:
                p_name = ", ".join(p.name for p in passengers) + f" (共 {len(passengers)} 人)"
            elif len(passengers) == 1:
                p_name = passengers[0].name

            dep_date_line = f"• *出发日期*: `{dep_date}`\n" if dep_date else ""
            ret_date_line = ""
            if ret_date:
                sel_ret_no = getattr(sel_ret, "train_no", sel_ret.get("train_no") if isinstance(sel_ret, dict) else None) if sel_ret else None
                sel_ret_cls = getattr(sel_ret, "train_class", sel_ret.get("train_class") if isinstance(sel_ret, dict) else None) if sel_ret else None
                sel_ret_dep = getattr(sel_ret, "departure_time", sel_ret.get("departure_time") if isinstance(sel_ret, dict) else None) if sel_ret else None

                ret_t_no = res.get("return_train_no") or sel_ret_no or (trip.get("return_train_no") if isinstance(trip, dict) else getattr(trip, "return_train_no", None))
                ret_t_cls = res.get("return_train_class") or sel_ret_cls or (trip.get("return_train_class") if isinstance(trip, dict) else getattr(trip, "return_train_class", None)) or t_cls
                ret_dep_time = res.get("return_departure_time") or sel_ret_dep or (trip.get("return_departure_time") if isinstance(trip, dict) else getattr(trip, "return_departure_time", None))

                has_manual_ret_train = bool(getattr(self.engine.task, "return_preferred_trains", None) or sel_ret_no)
                if not ret_t_no and has_manual_ret_train:
                    ret_t_no = ", ".join(self.engine.task.return_preferred_trains)

                if ret_t_no:
                    ret_train_display = f"*{ret_t_no}* ({ret_t_cls})" + ("" if has_manual_ret_train else " (AUTO)")
                else:
                    ret_train_display = "未选择 (AUTO 自动匹配)"

                ret_tf = getattr(self.engine.task, "return_time_from", "00:00") or "00:00"
                ret_tt = getattr(self.engine.task, "return_time_to", "23:59") or "23:59"
                has_manual_ret_time = (ret_tf != "00:00" or ret_tt != "23:59" or bool(sel_ret_dep))
                if ret_dep_time:
                    ret_time_display = f"`{ret_dep_time}`" + ("" if has_manual_ret_time else " (AUTO)")
                elif has_manual_ret_time:
                    ret_time_display = f"`{ret_tf}`" if ret_tf == ret_tt else f"`{ret_tf} - {ret_tt}` (AUTO)"
                else:
                    ret_time_display = f"`{ret_tf} - {ret_tt}` (未选择 / AUTO)"

                ret_seat = str(res.get("return_seat") or "").strip()
                if not ret_seat and "返程:" in actual_seat:
                    m_ret_seat = re.search(r"\(返程:\s*([^)]+)\)", actual_seat)
                    if m_ret_seat:
                        ret_seat = m_ret_seat.group(1).strip()
                if ret_seat:
                    if return_seat_choice.upper() in ("AUTO", "WINDOW", "AISLE", "ANY", "DEFAULT") and "AUTO" not in ret_seat.upper():
                        ret_seat_display = f"{ret_seat} ({return_seat_choice})"
                    else:
                        ret_seat_display = ret_seat
                else:
                    ret_seat_display = f"未选择 ({return_seat_choice})"

                ret_date_line = (
                    f"• *返程日期*: `{ret_date}` (往返双程)\n"
                    f"• *返程时间*: {ret_time_display}\n"
                    f"• *返程车次*: {ret_train_display}\n"
                    f"• *返程座位*: `{ret_seat_display}`\n"
                )

            # Push DuitNow QR photo directly to Telegram if captured
            if qr_path and os.path.exists(qr_path) and hasattr(self.engine, "notifier") and self.engine.notifier:
                try:
                    self.engine.notifier.send_photo_alert(
                        photo_path=qr_path,
                        caption=(
                            f"📱 *【KTMB 官方支付二维码 (DuitNow)】* 📱\n\n"
                            f"• *车次*: *{t_no}* ({t_cls})\n"
                            f"{dep_date_line}"
                            f"• *出发*: `{dep_time}`\n"
                            f"• *席位*: `{seat_display}` ({p_name})\n"
                            f"{ret_date_line}"
                            f"{amount_line}"
                            f"• *二维码有效期*: *3 分钟 (180 秒)*（锁座保留 15 分钟）\n\n"
                            f"⚠️ *【金融安全防掉单守则】*:\n"
                            f"1. 请在收到此二维码 **2 分钟内** 打开 Touch 'n Go / Maybank MAE 扫码支付。\n"
                            f"2. **超过 2 分钟请勿再扫此旧码**（防止银行扣款但网关已超时），请重发 `/seat auto` 获取新码！\n"
                            f"3. 付款后回复 `/verify` 即可提取官方 `K` 开头订单号与 `T` 开头车票号。"
                        )
                    )
                except Exception as ex:
                    logger.warning(f"发送 DuitNow 二维码图片失败: {ex}")

            # Automatically logout on EC2 to release KTMB single-login lock immediately
            try:
                logger.info("🔓 已发送支付二维码至 Telegram，正在自动登出 EC2 会话以释放 KTMB 单点登录锁...")
                self.engine.logout()
            except Exception as ex:
                logger.warning(f"发送二维码后自动登出 EC2 异常: {ex}")

            # Start payment expiry & auto-verification timer if notifier is active
            if getattr(self.engine, "notifier", None) is not None and self.expiry_timeout_seconds > 0:
                self._start_payment_expiry_timer(
                    context_info={
                        "train_no": t_no,
                        "train_class": t_cls,
                        "dep_date": dep_date,
                        "dep_time": dep_time,
                        "ret_date": ret_date,
                        "seat_display": seat_display,
                        "payment_ref": payment_ref,
                        "payment_amount": payment_amount,
                    }
                )

            ins_bought = bool(getattr(self.engine.task, "include_insurance", False))
            ins_line = (
                "• *行程保险*: `已加购 Takaful 保险`\n"
                if ins_bought
                else "• *行程保险*: `不购买 (已自动为您免除额外保费)`\n"
            )

            return (
                f"🎉 *【KTMB 官方席位锁定成功！】* 🎉\n\n"
                f"{booking_id_line}"
                f"{amount_line}"
                f"• *选定座位*: `{seat_display}`\n"
                f"• *车次等级*: *{t_no}* ({t_cls})\n"
                f"{dep_date_line}"
                f"• *发车时间*: `{dep_time}`\n"
                f"{ret_date_line}"
                f"• *乘车人*: {p_name}\n"
                f"{ins_line}"
                f"• *二维码有效期*: *3 分钟 (180 秒)*（锁座保留 15 分钟，超时自动释放）\n\n"
                f"🛡️ *【防扣款不出票 · 金融安全守则】*:\n"
                f"1. **限时扫码**：Fiuu 银行 DuitNow 二维码有效期仅 **3 分钟**，请务必在收到图片后 **2 分钟内** 扫码付款。\n"
                f"2. **严禁扫旧码**：若超过 2 分钟未付，**请绝对不要再扫上方旧二维码**（以免银行扣款但网关超时掉单），直接重发 `/seat auto` 重新生成新码即可。\n"
                f"3. **实时验票**：付款完成后回复 `/verify`（或 `查票`），机器人会立即登录 KTMB 官网【Upcoming Trips】（也可在 **KTMB Mobile App** 查看）为您提取 **K 开头官方订单号**、**T 开头电子票号** 与 **PDF 车票下载链接**（只有拿到 `K`/`T` 票号才代表 100% 购票成功）！\n\n"
                f"💻 *网页备用链接*:\n"
                f"👉 [官网付款链接]({pay_url})"
            )
        except Exception as e:
            return f"❌ 预订下单失败: {e}\n建议回复 `/seat auto` 重试或回复 `/cancel` 继续监控。"

    def _cancel_payment_expiry_timer(self):
        timer = getattr(self, "_payment_expiry_timer", None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
            self._payment_expiry_timer = None

    def _start_payment_expiry_timer(self, context_info: Optional[Dict[str, Any]] = None, timeout_seconds: Optional[float] = None):
        self._cancel_payment_expiry_timer()
        wait_secs = timeout_seconds if timeout_seconds is not None else self.expiry_timeout_seconds
        if not wait_secs or wait_secs <= 0:
            return
        timer = threading.Timer(wait_secs, self._on_payment_expiry_check, kwargs={"context_info": context_info or {}})
        timer.daemon = True
        self._payment_expiry_timer = timer
        timer.start()

    def _on_payment_expiry_check(self, context_info: Optional[Dict[str, Any]] = None):
        self._payment_expiry_timer = None
        notifier = getattr(self.engine, "notifier", None)
        if not notifier:
            return
        info = context_info or {}
        try:
            bookings = (
                self.engine.verify_upcoming_bookings()
                if hasattr(self.engine, "verify_upcoming_bookings")
                else []
            )
            try:
                self.engine.logout()
            except Exception:
                pass

            if bookings:
                msg = (
                    "✅ *【KTMB 自动核验：已确认真实出票！】* ✅\n\n"
                    + self._format_verified_bookings(bookings)
                )
                notifier.send_raw_message(msg)
                return
        except Exception as e:
            logger.warning(f"自动核验出票状态异常: {e}")

        t_no = info.get("train_no", "")
        dep_date = info.get("dep_date", "")
        seat_display = info.get("seat_display", "")
        detail_str = f"（车次 `{t_no}` | 日期 `{dep_date}` | 座位 `{seat_display}`）" if t_no else ""
        expiry_msg = (
            f"⏰ *【KTMB 15 分钟锁座超时提醒 & 二维码作废通知】* ⏰\n\n"
            f"刚才锁定的席位{detail_str}未在 KTMB 官网【Upcoming Trips (待出行车票)】中检测到已付款出票记录。\n\n"
            f"🚫 **金融安全拦截：请切勿再扫描上方的旧 DuitNow 二维码付款！**\n"
            f"• 如果您**尚未付款**：该锁定席位已被 KTMB 官方自动释放，**并未扣款或购票**。如需重新购票，请发送 `/book1` -> `/seat auto` 生成全新的二维码。\n"
            f"• 如果您**刚刚完成付款**：请发送 `/verify` 再次查询官方出票列表（若银行已扣款但 `/verify` 无票，请保留网关流水号 `{info.get('payment_ref') or 'N/A'}` 联系 KTMB 客服 +603-9779 1200）。"
        )
        try:
            notifier.send_raw_message(expiry_msg)
        except Exception as e:
            logger.warning(f"发送 15 分钟超时提醒失败: {e}")

    def _format_verified_bookings(self, bookings: list) -> str:
        lines = []
        for idx, b in enumerate(bookings, start=1):
            b_no = b.get("booking_no") or "未知"
            route = b.get("route") or ""
            dep_d = b.get("depart_date") or ""
            ret_d = b.get("return_date") or ""
            print_url = b.get("print_url") or f"https://online.ktmb.com.my/Booking/PrintTicket?p={b_no}"

            lines.append(f"🎫 *订单 {idx} — 官方订单号 (Booking No)*: `{b_no}`")
            if route:
                lines.append(f"• *行程*: {route}")
            if dep_d:
                date_str = f"去程 `{dep_d}`" + (f" | 返程 `{ret_d}`" if ret_d else "")
                lines.append(f"• *日期*: {date_str}")

            for leg_idx, leg in enumerate(b.get("legs") or [], start=1):
                leg_route = leg.get("route") or f"行程段 {leg_idx}"
                train = leg.get("train") or ""
                dt = leg.get("datetime") or ""
                lines.append(f"  🚆 *{leg_route}* — `{train}` (`{dt}`)")
                for t in leg.get("tickets") or []:
                    seat = t.get("seat") or "-"
                    t_no = t.get("ticket_no") or "-"
                    pax = t.get("passenger") or ""
                    lines.append(f"    • 座位 `{seat}` | 票号 `{t_no}` | {pax}")

            if print_url:
                lines.append(f"• *官方电子票 PDF*: [点击查看/打印车票]({print_url})")
            lines.append("")
        return "\n".join(lines).strip()

    def _handle_verify(self) -> str:
        if not hasattr(self.engine, "verify_upcoming_bookings"):
            return "⚠️ 当前引擎模式不支持在线验票，请直接打开手机 KTMB App -> 【My Tickets】查看。"
        try:
            bookings = self.engine.verify_upcoming_bookings()
            try:
                self.engine.logout()
            except Exception:
                pass

            if bookings:
                self._cancel_payment_expiry_timer()
                return (
                    "✅ *【KTMB 官网核验结果：已真实付款出票！】* ✅\n\n"
                    "已在 KTMB 官方【Upcoming Trips (待出行车票)】中查到您的有效车票：\n\n"
                    + self._format_verified_bookings(bookings)
                )

            return (
                "⚠️ *【KTMB 官网核验结果：暂未查到已出票记录】*\n\n"
                "机器人刚刚登录 KTMB 官网【Upcoming Trips (待出行车票)】核验，当前待出行车票数量为 **0**。\n\n"
                "• **如果您还未扫码付款**：锁座仅保留 **15 分钟**，请尽快使用 Touch 'n Go / 银行 App 识别上方 DuitNow 二维码完成支付，付款后再发 `/verify` 核验。\n"
                "• **如果已超过 15 分钟未付款**：KTMB 已自动释放刚才锁定的座位，**没有买到票、也不会扣款**。您可以发送 `/book1` -> `/seat auto` 重新锁座。"
            )
        except Exception as e:
            return f"❌ 核验 KTMB 官方出票状态失败: {e}"

    def _handle_logout(self) -> str:
        try:
            ok = self.engine.logout()
            if ok:
                return "🔓 *已成功释放 KTMB 官方登录锁*！\n您现在可以在手机 KTMB App 或电脑端自由登录了。"
            return "💡 当前无活跃的官方登录会话或已处于登出状态。"
        except Exception as e:
            return f"❌ 释放登录锁异常: {e}"

    def _handle_cancel_selection(self) -> str:
        self.engine.selected_trip = None
        self.engine.selected_return_trip = None
        self.engine.last_found_trips = []
        self.engine.last_found_return_trips = []
        self.engine.is_paused = False
        return "✅ 已取消本次订座选择，守护引擎已恢复后台实时监控！"

    def _handle_set_cookie(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return (
                "💡 请提供 Cookie 字符串，例如：\n"
                "`/set_cookie .AspNetCore.Cookies=CfDJ8...`\n"
                "或包含键值对的 Cookie 串（以分号分隔）。"
            )
        cookie_data = parts[1].strip()
        try:
            import json, os
            cookies_list = []
            if cookie_data.startswith("[") and cookie_data.endswith("]"):
                cookies_list = json.loads(cookie_data)
            else:
                pairs = [p.strip() for p in cookie_data.split(";") if "=" in p]
                for p in pairs:
                    k, v = p.split("=", 1)
                    cookies_list.append({
                        "name": k.strip(),
                        "value": v.strip(),
                        "domain": ".ktmb.com.my",
                        "path": "/"
                    })

            state = {
                "cookies": cookies_list,
                "origins": []
            }
            os.makedirs("data", exist_ok=True)
            with open("data/auth_state.json", "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)

            if hasattr(self.engine, "browser_driver") and self.engine.browser_driver:
                def _inject():
                    page = getattr(self.engine.browser_driver, "page", None)
                    if page and page.context:
                        page.context.add_cookies(cookies_list)
                if hasattr(self.engine, "run_in_browser_thread"):
                    self.engine.run_in_browser_thread(_inject)
                else:
                    _inject()

            return f"✓ 已成功更新并固化 {len(cookies_list)} 个会话 Cookie 至 `data/auth_state.json`！"
        except Exception as e:
            return f"❌ 更新 Cookie 失败: {e}"



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

                logger.info(f"📩 收到 Telegram [chat_id={chat_id}] 消息: {text}")
                reply = self.handler.handle_message(chat_id=chat_id, text=text)
                if reply and chat_id:
                    logger.info(f"📤 正在回复 Telegram: {reply[:50]}...")
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
            resp = self.session.post(send_url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Telegram Markdown 发送失败 ({resp.status_code}): {resp.text}，正在降级为纯文本重发...")
                payload.pop("parse_mode", None)
                retry_resp = self.session.post(send_url, json=payload, timeout=10)
                if retry_resp.status_code == 200:
                    logger.info("✓ 纯文本降级已成功送达 Telegram！")
                else:
                    logger.error(f"❌ 纯文本重发也失败: {retry_resp.text}")
            else:
                logger.info("✓ 消息已成功送达 Telegram！")
        except Exception as e:
            logger.warning(f"Failed to send Telegram reply: {e}")

    def run(self):
        logger.info(f"TelegramCommandListener 已启动 (监听来自 chat_id: {self.authorized_chat_id} 的控制指令)...")
        while self.is_running:
            self.poll_once()
            time.sleep(0.5)

    def stop(self):
        self.is_running = False
