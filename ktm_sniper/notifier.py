import os
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class TelegramTicketNotifier:
    """
    Formats and sends rich-text notifications and screenshot alerts via Telegram Bot API.
    Ensures sensitive passenger data is masked before transmission.
    """
    def __init__(self, bot_token: str, chat_id: str, session=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        if session is None and bot_token:
            import requests
            self.session = requests.Session()
        else:
            self.session = session

    def mask_sensitive_data(self, id_number: str) -> str:
        if len(id_number) <= 4:
            return "****"
        return id_number[:-4] + "****"

    def format_message(
        self,
        booking_id: str,
        trip_details: Dict[str, Any],
        passenger_name: str,
        raw_id: str,
        passengers: Optional[Any] = None,
        payment_url: Optional[str] = None
    ) -> str:
        upcoming_url = "https://online.ktmb.com.my/Booking/UpcomingList"
        portal_url = "https://online.ktmb.com.my/"

        if passengers and len(passengers) > 1:
            lines = []
            for idx, p in enumerate(passengers, 1):
                p_name = getattr(p, "name", p.get("name") if isinstance(p, dict) else str(p))
                p_id = getattr(p, "id_number", p.get("id_number") if isinstance(p, dict) else "")
                lines.append(f"  {idx}. {p_name} ({self.mask_sensitive_data(str(p_id))})")
            passenger_block = f"• *乘车人名单* ({len(passengers)}人):\n" + "\n".join(lines)
        else:
            masked_id = self.mask_sensitive_data(raw_id)
            passenger_block = f"• *乘车人*: {passenger_name} (`{masked_id}`)"

        train_label = trip_details.get('train_no', 'N/A')
        class_label = trip_details.get('class', trip_details.get('train_class', 'N/A'))

        message = (
            f"🚨 *【KTMB 抢票成功通知】* 🚨\n\n"
            f"🎉 *席位已成功锁定！请在 15 分钟内完成支付* 🎉\n\n"
            f"• *订单编号*: `{booking_id}`\n"
            f"• *车次等级*: *{train_label}* ({class_label})\n"
            f"• *车程路线*: *{trip_details.get('origin', 'N/A')}* ➡️ *{trip_details.get('destination', 'N/A')}*\n"
            f"• *发车时间*: `{trip_details.get('departure_time', 'N/A')}`\n"
            f"{passenger_block}\n\n"
            f"⏳ *支付时限*: 官方倒计时 *15 分钟*（超时席位将被自动释放）\n\n"
            f"👉 *完成付款方式（任选其一）*:\n"
            f"📱 *方式一【手机 KTMB App (最推荐)】*:\n"
            f"打开手机【KTMB Mobile App】➡️ 登录同账号 ➡️ 进入底部【My Tickets】或【Upcoming Trips】直接拉起 FPX 银行转账、Touch 'n Go 或信用卡完成支付！\n\n"
            f"💻 *方式二【电脑/手机浏览器官网】*:\n"
            f"登录 KTMB 官网后访问待出行与订单列表:\n"
            f"🔗 [点击直达 KTMB 官网行程与订单列表]({upcoming_url})\n"
            f"🌐 [KTMB 官网首页]({portal_url})\n\n"
            f"💡 *提示*: KTMB 为单设备单会话安全架构，请直接在您手机的 KTMB App 或已登录官网中完成支付！"
        )
        return message

    def format_trip_options_message(
        self,
        trips: List[Any],
        origin: str,
        destination: str,
        date: str
    ) -> str:
        options = []
        for idx, t in enumerate(trips[:5], start=1):
            t_no = getattr(t, "train_no", t.get("train_no") if isinstance(t, dict) else "ETS")
            t_cls = getattr(t, "train_class", t.get("train_class", t.get("class")) if isinstance(t, dict) else "ETS Gold")
            dep = getattr(t, "departure_time", t.get("departure_time") if isinstance(t, dict) else "")
            arr = getattr(t, "arrival_time", t.get("arrival_time") if isinstance(t, dict) else "")
            seats = getattr(t, "available_seats", t.get("available_seats") if isinstance(t, dict) else 0)
            fare = getattr(t, "fare", t.get("fare") if isinstance(t, dict) else 0.0)
            options.append(
                f"[{idx}] *{dep}* ➡️ *{arr}* | {t_cls} (*{t_no}*)\n"
                f"     剩余 *{seats}* 席 | 票价: MYR {fare:.2f}\n"
                f"     👉 发送: `/book {idx}` 选定该班次"
            )

        first_t = getattr(trips[0], "train_no", "1") if trips else "1"
        msg = (
            f"🎫 *【KTMB 发现可用车次，请选择出发时间】* 🎫\n\n"
            f"• *路线*: *{origin}* ➡️ *{destination}*\n"
            f"• *出发日期*: `{date}`\n"
            f"• *在售车次*:\n\n" + "\n\n".join(options) + "\n\n"
            f"💡 *操作提示*: 在手机上直接回复例如 `/book 1` 或 `/book {first_t}` 选定班次；回复 `/cancel` 可忽略本次继续监控。"
        )
        return msg

    def format_seat_options_message(
        self,
        train_no: str,
        depart_time: str,
        coaches: Any
    ) -> str:
        coach_blocks = []
        if isinstance(coaches, dict):
            for coach_name, seat_types in list(coaches.items())[:3]:
                if isinstance(seat_types, dict):
                    window_seats = ", ".join(seat_types.get("window", [])[:8]) or "暂无"
                    aisle_seats = ", ".join(seat_types.get("aisle", [])[:8]) or "暂无"
                else:
                    window_seats = ", ".join(str(s) for s in seat_types[:8]) or "暂无"
                    aisle_seats = "暂无"
                coach_blocks.append(
                    f"🚆 *Coach {coach_name}*:\n"
                    f"  • 靠窗 (Window): `{window_seats}`\n"
                    f"  • 走道 (Aisle):  `{aisle_seats}`"
                )
        elif isinstance(coaches, list):
            for c in coaches[:3]:
                c_name = c.get("coach", "Coach")
                seats = c.get("seats", [])
                win = [s.get("seat_no") for s in seats if "win" in str(s.get("type", "")).lower()]
                ais = [s.get("seat_no") for s in seats if "ais" in str(s.get("type", "")).lower()]
                if not win and not ais and seats:
                    win = [s.get("seat_no") for s in seats[:4]]
                coach_blocks.append(
                    f"🚆 *{c_name}*:\n"
                    f"  • 靠窗 (Window): `{', '.join(filter(None, win)) or '暂无'}`\n"
                    f"  • 走道 (Aisle):  `{', '.join(filter(None, ais)) or '暂无'}`"
                )

        msg = (
            f"💺 *【请选择车厢与座位】* 💺\n\n"
            f"• *车次*: *{train_no}* (出发时间: `{depart_time}`)\n\n"
            + "\n\n".join(coach_blocks) + "\n\n"
            f"👉 *请回复指定座位*:\n"
            f"• 回复 `/seat <座位号>` (例如: `/seat 3A` 锁定指定座)\n"
            f"• 回复 `/seat auto` (由系统自动挑选最优靠窗位)\n"
            f"• 回复 `/cancel` 放弃并继续监控"
        )
        return msg

    def send_trip_options(self, trips: List[Any], origin: str, destination: str, date: str) -> bool:
        text = self.format_trip_options_message(trips, origin, destination, date)
        return self.send_raw_message(text)

    def send_seat_options(self, train_no: str, depart_time: str, coaches: Dict[str, Dict[str, List[str]]]) -> bool:
        text = self.format_seat_options_message(train_no, depart_time, coaches)
        return self.send_raw_message(text)

    def send_alert(
        self,
        booking_id: str,
        trip_details: Dict[str, Any],
        passenger_name: str,
        raw_id: str,
        passengers: Optional[Any] = None,
        payment_url: Optional[str] = None
    ) -> bool:
        message = self.format_message(booking_id, trip_details, passenger_name, raw_id, passengers, payment_url=payment_url)
        
        if not self.bot_token or not self.chat_id or self.session is None:
            logger.info(f"Notification alert (dry-run):\n{message}")
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            response = self.session.post(url, json=payload)
            if response.status_code != 200:
                logger.error(f"Telegram send_alert failed: HTTP {response.status_code} - {response.text}")
                # Fallback without markdown if markdown parsing failed
                payload["parse_mode"] = ""
                response = self.session.post(url, json=payload)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send_alert exception: {e}")
            return False

    def send_photo_alert(self, photo_path: str, caption: str) -> bool:
        if self.session is None:
            logger.info(f"Photo alert (dry-run): {photo_path}\nCaption: {caption}")
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            with open(photo_path, "rb") as photo:
                files = {"photo": photo}
                data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "Markdown"}
                response = self.session.post(url, data=data, files=files)
                if response.status_code != 200:
                    logger.error(f"Telegram send_photo_alert failed: HTTP {response.status_code} - {response.text}")
                    # Fallback to plain text message if photo markdown failed
                    self.send_raw_message(caption, parse_mode="")
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send_photo_alert exception: {e}")
            return False

    def send_raw_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        Sends an arbitrary text message to Telegram (used for login alerts, system events, etc.).
        """
        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        if self.session is None:
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            response = self.session.post(url, json=payload)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send_raw_message exception: {e}")
            return False
