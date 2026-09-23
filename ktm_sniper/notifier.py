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
        passengers: Optional[Any] = None
    ) -> str:
        checkout_url = f"https://online.ktmb.com.my/v2/payment/checkout?bookingId={booking_id}"
        history_url = "https://online.ktmb.com.my/Ticket/BookingHistory"

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
            f"👉 *点击下方链接立即还款付款*:\n"
            f"🔗 [立即前往 KTMB 官方结账付款]({checkout_url})\n"
            f"📋 [查看待支付订单列表]({history_url})\n\n"
            f"💡 *提示*: 您也可以直接打开手机【KTMB App】➡️ 点击底部【My Tickets】直接拉起 FPX / 银行卡支付！"
        )
        return message

    def send_alert(
        self,
        booking_id: str,
        trip_details: Dict[str, Any],
        passenger_name: str,
        raw_id: str,
        passengers: Optional[Any] = None
    ) -> bool:
        message = self.format_message(booking_id, trip_details, passenger_name, raw_id, passengers)
        
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
