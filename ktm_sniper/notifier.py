import os
from typing import Dict, Any, Optional

class TelegramTicketNotifier:
    """
    Formats and sends rich-text notifications and screenshot alerts via Telegram Bot API.
    Ensures sensitive passenger data is masked before transmission.
    """
    def __init__(self, bot_token: str, chat_id: str, session=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
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
        payment_url = f"https://online.ktmb.com.my/v2/payment/checkout?bookingId={booking_id}"
        
        if passengers and len(passengers) > 1:
            lines = []
            for idx, p in enumerate(passengers, 1):
                p_name = getattr(p, "name", p.get("name") if isinstance(p, dict) else str(p))
                p_id = getattr(p, "id_number", p.get("id_number") if isinstance(p, dict) else "")
                lines.append(f"  {idx}. {p_name} ({self.mask_sensitive_data(str(p_id))})")
            passenger_block = f"• *Passengers* ({len(passengers)}):\n" + "\n".join(lines)
        else:
            masked_id = self.mask_sensitive_data(raw_id)
            passenger_block = f"• *Passenger*: {passenger_name} ({masked_id})"

        message = (
            f"🚨 *KTM Ticket Sniper Alert* 🚨\n\n"
            f"✅ *Seat Successfully Reserved!*\n"
            f"• *Booking ID*: `{booking_id}`\n"
            f"• *Train*: {trip_details.get('train_no', 'N/A')} ({trip_details.get('class', trip_details.get('train_class', 'N/A'))})\n"
            f"• *Route*: {trip_details.get('origin', 'N/A')} ➡️ {trip_details.get('destination', 'N/A')}\n"
            f"• *Departure*: {trip_details.get('departure_time', 'N/A')}\n"
            f"{passenger_block}\n\n"
            f"⏳ *Action Required*: You have 15 minutes to complete the payment.\n"
            f"🔗 [Click Here to Pay via KITS Gateway]({payment_url})"
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
        text = self.format_message(booking_id, trip_details, passenger_name, raw_id, passengers=passengers)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        if self.session is None:
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = self.session.post(url, json=payload)
        return response.status_code == 200

    def send_photo_alert(self, photo_path: str, caption: str = "") -> bool:
        """
        Sends a visual screenshot alert to the user's Telegram chat.
        """
        if not os.path.exists(photo_path):
            return False

        if self.session is None:
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        with open(photo_path, "rb") as photo_file:
            files = {"photo": photo_file}
            data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "Markdown"}
            response = self.session.post(url, data=data, files=files)
            return response.status_code == 200
