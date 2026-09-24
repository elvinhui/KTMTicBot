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

        # Support spaceless /book1, /book2, etc.
        book_match = re.match(r"^/book(\d+)$", command)
        if book_match:
            parts = ["/book", book_match.group(1)]
            command = "/book"

        # Support spaceless /seat3A, /seat3a, etc.
        seat_match = re.match(r"^/seat(\w+)$", command)
        if seat_match:
            parts = ["/seat", seat_match.group(1)]
            command = "/seat"

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
        elif command in ("/book", "订票", "选车"):
            response = self._handle_book(parts[1:])
        elif command in ("/seat", "/seats", "选座", "订座", "/sear"):
            response = self._handle_seat(parts[1:])
        elif command in ("/cancel", "取消", "放弃"):
            response = self._handle_cancel_selection()
        elif command in ("/cookie", "/set_cookie", "更新cookie"):
            response = self._handle_set_cookie(clean_text)
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

        rt_info = "否"
        if s["is_round_trip"]:
            rt_info = f"是 (返程 {s['return_date']}, {s['return_time_window']})"

        passengers_list = []
        for p in self.engine.task.passengers:
            passengers_list.append(f"{p.name} (`{p.masked_id}`)")
        passengers_str = ", ".join(passengers_list) if passengers_list else "未指定"

        trains_filter = ", ".join(self.engine.task.preferred_trains) if self.engine.task.preferred_trains else "不限 (全时段车次)"
        classes_filter = ", ".join(self.engine.task.preferred_classes) if self.engine.task.preferred_classes else "不限 (全席别)"

        return (
            f"🚄 *KTMB 抢票守护状态报告* 🚄\n\n"
            f"• *运行状态*: {status_icon} {status_desc}\n"
            f"• *去程路线*: *{s['origin']}* ➡️ *{s['destination']}*\n"
            f"• *出发日期*: `{s['date']}`\n"
            f"• *出发时段*: {s['time_window']}\n"
            f"• *车次偏好*: `{trains_filter}`\n"
            f"• *车型席别*: `{classes_filter}`\n"
            f"• *双程往返*: {rt_info}\n"
            f"• *已轮询周期*: {s['total_cycles']} 次\n"
            f"• *乘车人*: {passengers_str}\n"
            f"• *数据存储*: SQLite (脱敏与加密守护已开启)\n\n"
            f"💡 发送 `/set trains any` 可取消车次限定；发送 `/set time 00:00-23:59` 监控全天车次。"
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
                new_task = dataclasses.replace(current_task, date=norm_date)
                self.engine.update_task(new_task)
                return f"✓ 出发日期已更新并写入 SQLite: *{norm_date}*"

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

            else:
                return f"⚠️ 未知设置项 '{field}'。支持的字段：origin, dest, date, time, return_date, seats, trains, class。"
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
                if target.upper() == str(t_no).upper():
                    found_trip = t
                    break

        if not found_trip:
            if pending:
                return f"⚠️ 未找到指定班次 '{target}'。请回复 `/book 1` 到 `/book {len(pending)}` 之间的序号。"
            return "💡 当前没有等待确认的车次。守护引擎发现余票时会主动向您推送候选清单！"

        self.engine.selected_trip = found_trip

        # Fetch seat layout
        train_no = getattr(found_trip, "train_no", found_trip.get("train_no") if isinstance(found_trip, dict) else "9044")
        dep_time = getattr(found_trip, "departure_time", found_trip.get("departure_time") if isinstance(found_trip, dict) else "00:00")
        coaches = self.engine.fetch_seats_layout(train_no)
        self.engine.available_seats_cache = coaches

        if hasattr(self.engine, "notifier") and self.engine.notifier:
            return self.engine.notifier.format_seat_options_message(
                train_no=train_no,
                depart_time=dep_time,
                coaches=coaches
            )
        return f"✅ 已选定车次 {train_no}！请回复 `/seat <座位号>` (例如: `/seat 3A` 或 `/seat auto`) 确认座位！"

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

        seat_choice = args[0].strip().upper() if args else "AUTO"
        trip = self.engine.selected_trip

        try:
            res = self.engine.execute_real_booking(trip=trip, seat_no=seat_choice)
            t_no = getattr(trip, "train_no", trip.get("train_no") if isinstance(trip, dict) else "ETS")
            t_cls = getattr(trip, "train_class", trip.get("train_class") if isinstance(trip, dict) else "ETS Gold")
            dep_time = getattr(trip, "departure_time", trip.get("departure_time") if isinstance(trip, dict) else "")
            booking_id = res.get("booking_id", f"KITS-{t_no}")
            pay_url = res.get("payment_url", f"https://online.ktmb.com.my/Payment/Checkout?bookingId={booking_id}")

            # Reset selection state
            self.engine.selected_trip = None
            self.engine.last_found_trips = []

            p_name = "Passenger"
            if self.engine.task.passengers:
                p_name = self.engine.task.passengers[0].name

            return (
                f"🎉 *【KTMB 官方订单生成成功！】* 🎉\n\n"
                f"• *官方订单号*: `{booking_id}`\n"
                f"• *选定座位*: `{seat_choice}`\n"
                f"• *车次等级*: *{t_no}* ({t_cls})\n"
                f"• *发车时间*: `{dep_time}`\n"
                f"• *乘车人*: {p_name}\n"
                f"• *支付时限*: 官方倒计时 *15 分钟*\n\n"
                f"👉 [立即前往 KTMB 官方结账付款]({pay_url})\n\n"
                f"💡 您也可以打开手机【KTMB App】在【My Tickets】直接完成 FPX 支付！"
            )
        except Exception as e:
            return f"❌ 预订下单失败: {e}\n建议回复 `/seat auto` 重试或回复 `/cancel` 继续监控。"

    def _handle_cancel_selection(self) -> str:
        self.engine.selected_trip = None
        self.engine.last_found_trips = []
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
                page = getattr(self.engine.browser_driver, "page", None)
                if page and page.context:
                    page.context.add_cookies(cookies_list)

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
