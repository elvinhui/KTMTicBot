import os
import sys

# Ensure UTF-8 output on Windows consoles to prevent UnicodeEncodeError with non-ASCII characters
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import json
import argparse
import logging
from typing import List, Optional

# Core exports for backward compatibility with existing tests
from ktm_sniper.stations import KTMStationRegistry
from ktm_sniper.network.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException
from ktm_sniper.checker import KTMTicketChecker
from ktm_sniper.poller import AdaptivePoller
from ktm_sniper.reserver import KTMSeatReserver
from ktm_sniper.notifier import TelegramTicketNotifier
from ktm_sniper.models import SniperTaskConfig, Passenger, TripInfo, TaskStatus
from ktm_sniper.browser.manager import BrowserManager
from ktm_sniper.browser.driver import KTMBrowserDriver
from ktm_sniper.storage import TaskRepository
from ktm_sniper.engine import KTMSniperEngine
from ktm_sniper.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ktm_sniper")

def prompt_station(label: str, exclude_station: Optional[str] = None) -> str:
    """
    Interactive station picker with fuzzy searching, aliases, popular hubs, and disambiguation menu.
    """
    while True:
        raw_input = input(f"👉 请输入{label} (支持中英文/简写如 BM/PG/KLS，或输入 '?' 查看热门车站): ").strip()
        if not raw_input:
            print("⚠️ 输入不能为空，请重新输入。")
            continue

        if raw_input in ["?", "？", "help", "list", "热门"]:
            popular = KTMStationRegistry.get_popular_stations()
            print("\n🌟 --- KTMB 10 大核心热门枢纽车站 ---")
            for idx, p in enumerate(popular, start=1):
                print(f"  [{idx:2d}] {p['name']:<22} (代码: {p['code']})")
            print("---------------------------------------")
            choice = input(f"👉 请选择对应序号 (1-{len(popular)}) 或输入其他车站名称: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(popular):
                selected = popular[int(choice) - 1]["name"]
                if exclude_station and selected.upper() == exclude_station.upper():
                    print(f"⚠️ {label}不能与已选车站 ({exclude_station}) 相同，请重新选择！")
                    continue
                print(f"   ✓ 已选择: {selected} [{popular[int(choice) - 1]['code']}]")
                return selected
            elif choice:
                raw_input = choice
            else:
                continue

        norm = raw_input.strip().upper()

        # 1. Exact match with station code or full station name
        for s_name, s_code in KTMStationRegistry._STATIONS.items():
            if norm == s_code or norm == s_name:
                if exclude_station and s_name.upper() == exclude_station.upper():
                    print(f"⚠️ {label}不能与已选车站 ({exclude_station}) 相同，请重新选择！")
                    continue
                print(f"   ✓ 已选择: {s_name} [{s_code}]")
                return s_name

        # 2. Exact match with known alias (e.g. BM, PG, 大山脚, 槟城)
        if norm in KTMStationRegistry._ALIASES:
            canon = KTMStationRegistry._ALIASES[norm]
            code = KTMStationRegistry._STATIONS.get(canon, "")
            if exclude_station and canon.upper() == exclude_station.upper():
                print(f"⚠️ {label}不能与已选车站 ({exclude_station}) 相同，请重新选择！")
                continue
            print(f"   ✓ 已选择: {canon} [{code}]")
            return canon


        # 2. Fuzzy search across stations and aliases
        matches = KTMStationRegistry.search_stations(raw_input)
        if not matches:
            print(f"⚠️ 未找到与 '{raw_input}' 匹配的车站。输入 '?' 可查看全马热门车站列表。")
            continue

        if len(matches) == 1:
            name = matches[0]["name"]
            code = matches[0]["code"]
            if exclude_station and name.upper() == exclude_station.upper():
                print(f"⚠️ {label}不能与已选车站 ({exclude_station}) 相同，请重新选择！")
                continue
            print(f"   ✓ 自动匹配: {name} [{code}]")
            return name

        # Multiple matches: show numbered menu
        print(f"\n🔍 找到 {len(matches)} 个相关车站:")
        display_matches = matches[:10]
        for idx, m in enumerate(display_matches, start=1):
            print(f"  [{idx}] {m['name']} ({m['code']})")

        while True:
            sel = input(f"👉 请输入序号 (1-{len(display_matches)}) 或直接回车重新搜索: ").strip()
            if not sel:
                break
            if sel.isdigit() and 1 <= int(sel) <= len(display_matches):
                chosen = display_matches[int(sel) - 1]["name"]
                chosen_code = display_matches[int(sel) - 1]["code"]
                if exclude_station and chosen.upper() == exclude_station.upper():
                    print(f"⚠️ {label}不能与已选车站 ({exclude_station}) 相同，请重新选择！")
                    break
                print(f"   ✓ 已选择: {chosen} [{chosen_code}]")
                return chosen
            else:
                print(f"⚠️ 无效序号，请输入 1 到 {len(display_matches)}。")

def run_wizard() -> SniperTaskConfig:
    """
    Interactive CLI wizard to pre-select origin, destination, date, time window, and passenger details.
    """
    print("\n🚄 ===========================================")
    print("      KTMB (KITS) 抢票机器人 - 行程预选向导")
    print("===========================================\n")

    origin = prompt_station("出发地")
    dest = prompt_station("目的地", exclude_station=origin)

    from ktm_sniper.models import normalize_date
    while True:
        raw_date = input("👉 请输入出发日期 (如 2027-02-04 或 2027-2-4): ").strip()
        try:
            date = normalize_date(raw_date)
            break
        except ValueError:
            print(f"⚠️ 日期格式 '{raw_date}' 无效，请输入例如 2027-02-04 或 2027-2-4。")

    time_from = input("👉 出发时间范围起始 (默认 00:00): ").strip() or "00:00"
    time_to = input("👉 出发时间范围截止 (默认 23:59): ").strip() or "23:59"

    # --- 往返车票 (Round Trip) 选项 ---
    is_round_trip = False
    return_date = None
    return_time_from = "00:00"
    return_time_to = "23:59"

    rt_input = input("\n👉 是否预订往返车票 (Round Trip / 双程)? (y/N): ").strip().lower()
    if rt_input in ["y", "yes", "1", "true", "是"]:
        is_round_trip = True
        while True:
            raw_ret_date = input(f"👉 请输入返程日期 (必须晚于或等于去程 {date}): ").strip()
            try:
                ret_norm = normalize_date(raw_ret_date)
                if ret_norm < date:
                    print(f"⚠️ 返程日期 '{ret_norm}' 不能早于去程日期 '{date}'，请重新输入。")
                    continue
                return_date = ret_norm
                break
            except ValueError:
                print(f"⚠️ 日期格式 '{raw_ret_date}' 无效，请输入例如 2027-02-08。")

        return_time_from = input("👉 返程时间范围起始 (默认 00:00): ").strip() or "00:00"
        return_time_to = input("👉 返程时间范围截止 (默认 23:59): ").strip() or "23:59"

    train_pref = input("\n👉 指定车次编号 (逗号分隔，留空表示任意车次): ").strip()
    preferred_trains = [t.strip() for t in train_pref.split(",") if t.strip()]

    class_pref = input("👉 指定车型级别 (如 ETS Platinum, ETS Gold，留空表示任意): ").strip()
    preferred_classes = [c.strip() for c in class_pref.split(",") if c.strip()]

    seat_pref = input("👉 席位偏好 (Window / Aisle / Any, 默认 Window): ").strip() or "Window"

    print("\n👤 --- 乘车人预填 (敏感信息隐私保护) ---")
    passengers = []
    p_num = 1
    import getpass
    while True:
        prompt_name = f"👉 乘客 #{p_num} 真实姓名: " if p_num > 1 else "👉 乘客真实姓名 (留空跳过乘车人录入): "
        p_name = input(prompt_name).strip()
        if not p_name:
            break

        env_ic = os.getenv("KTM_PASSENGER_IC", "").strip() if p_num == 1 else ""
        prompt_ic = "👉 身份证/护照号 (输入将隐藏保护，直接回车使用环境变量): " if env_ic else f"👉 乘客 #{p_num} 身份证/护照号 (输入将隐藏保护): "
        try:
            p_id = getpass.getpass(prompt_ic).strip()
        except Exception:
            p_id = input(prompt_ic).strip()

        if not p_id and env_ic and p_num == 1:
            p_id = env_ic

        p_gender = input(f"👉 乘客 #{p_num} 性别 (Male/Female, 默认 Male): ").strip() or "Male"

        env_phone = os.getenv("KTM_PASSENGER_PHONE", "").strip() if p_num == 1 else ""
        prompt_phone = "👉 联系电话 (如 +60123456789，直接回车使用环境变量): " if env_phone else f"👉 乘客 #{p_num} 联系电话 (如 +60123456789): "
        p_phone = input(prompt_phone).strip()
        if not p_phone and env_phone and p_num == 1:
            p_phone = env_phone
        if not p_phone:
            p_phone = "0123456789"

        if p_id:
            passengers.append(Passenger(
                name=p_name,
                id_number=p_id,
                gender=p_gender,
                phone=p_phone
            ))
            print(f"   ✓ 乘客 #{p_num} [{p_name}] 已录入！")
            p_num += 1

        more = input("👉 是否继续添加下一位乘车人? (y/N): ").strip().lower()
        if more not in ["y", "yes", "1", "true", "是"]:
            break

    config = SniperTaskConfig(
        origin=origin,
        destination=dest,
        date=date,
        time_from=time_from,
        time_to=time_to,
        preferred_trains=preferred_trains,
        preferred_classes=preferred_classes,
        seat_preference=seat_pref,
        required_seats=len(passengers) if passengers else 1,
        passengers=passengers,
        auto_reserve=True,
        is_round_trip=is_round_trip,
        return_date=return_date,
        return_time_from=return_time_from,
        return_time_to=return_time_to
    )
    return config

def load_from_json(file_path: str) -> List[SniperTaskConfig]:
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tasks: List[SniperTaskConfig] = []
    for item in data.get("tasks", []):
        passengers = [
            Passenger(
                name=p["name"],
                id_number=p["id_number"],
                gender=p["gender"],
                phone=p["phone"],
                email=p.get("email", "")
            )
            for p in item.get("passengers", [])
        ]
        task = SniperTaskConfig(
            origin=item["origin"],
            destination=item["destination"],
            date=item["date"],
            time_from=item.get("time_from", "00:00"),
            time_to=item.get("time_to", "23:59"),
            preferred_trains=item.get("preferred_trains", []),
            preferred_classes=item.get("preferred_classes", []),
            seat_preference=item.get("seat_preference", "Window"),
            required_seats=item.get("required_seats", len(passengers) or 1),
            passengers=passengers,
            auto_reserve=item.get("auto_reserve", True),
            is_round_trip=item.get("is_round_trip", False),
            return_date=item.get("return_date"),
            return_time_from=item.get("return_time_from", "00:00"),
            return_time_to=item.get("return_time_to", "23:59")
        )
        tasks.append(task)
    return tasks

def main():
    parser = argparse.ArgumentParser(description="KTMB (KITS) 抢票与风控对抗机器人 (KTM-Ticket-Sniper)")
    parser.add_argument("--origin", help="出发站 (如 'KL Sentral' 或 'KLS')")
    parser.add_argument("--dest", help="目的站 (如 'Butterworth' 或 'BTW')")
    parser.add_argument("--date", help="出发日期 (YYYY-MM-DD)")
    parser.add_argument("--time-from", default="00:00", help="出发时间下限 (HH:MM)")
    parser.add_argument("--time-to", default="23:59", help="出发时间上限 (HH:MM)")
    parser.add_argument("--round-trip", action="store_true", help="是否预订往返双程车票")
    parser.add_argument("--return-date", help="返程日期 (YYYY-MM-DD)")
    parser.add_argument("--return-time-from", default="00:00", help="返程时间下限 (HH:MM)")
    parser.add_argument("--return-time-to", default="23:59", help="返程时间上限 (HH:MM)")
    parser.add_argument("--train", action="append", default=[], help="指定车次编号 (可多次指定)")
    parser.add_argument("--class-name", action="append", default=[], help="指定车次等级 (如 'ETS Platinum')")
    parser.add_argument("--seat", default="Window", help="席位偏好 (Window/Aisle)")
    parser.add_argument("--name", help="乘客姓名")
    parser.add_argument("--ic", help="乘客身份证或护照")
    parser.add_argument("--gender", default="Male", help="乘客性别 (Male/Female)")
    parser.add_argument("--phone", default="0123456789", help="乘客电话")
    parser.add_argument("--config", help="从 JSON 配置文件加载抢票任务")
    parser.add_argument("--headless", action="store_true", default=True, help="是否使用无头浏览器 (默认 True)")
    parser.add_argument("--no-browser", action="store_true", help="禁用无头浏览器，仅使用 API 模式")
    parser.add_argument("--wizard", action="store_true", help="启动交互式预选向导")
    parser.add_argument("--max-cycles", type=int, default=None, help="最大轮询周期数 (留空为无限监听)")

    args = parser.parse_args()

    task_config: Optional[SniperTaskConfig] = None

    if args.config:
        tasks = load_from_json(args.config)
        if tasks:
            task_config = tasks[0]
            logger.info(f"已从 {args.config} 加载任务: {task_config.origin} -> {task_config.destination} ({task_config.date})")
    elif args.wizard:
        task_config = run_wizard()
    elif args.origin and args.dest and args.date:
        is_rt = args.round_trip or bool(args.return_date)
        if is_rt and not args.return_date:
            logger.error("启用往返模式 (--round-trip) 时必须通过 --return-date 指定返程日期！")
            return

        passengers = []
        ic_val = args.ic or os.getenv("KTM_PASSENGER_IC", "").strip()
        phone_val = args.phone or os.getenv("KTM_PASSENGER_PHONE", "").strip() or "0123456789"
        if args.name and ic_val:
            passengers.append(Passenger(
                name=args.name,
                id_number=ic_val,
                gender=args.gender,
                phone=phone_val
            ))
        task_config = SniperTaskConfig(
            origin=args.origin,
            destination=args.dest,
            date=args.date,
            time_from=args.time_from,
            time_to=args.time_to,
            preferred_trains=args.train,
            preferred_classes=args.class_name,
            seat_preference=args.seat,
            passengers=passengers,
            is_round_trip=is_rt,
            return_date=args.return_date,
            return_time_from=args.return_time_from,
            return_time_to=args.return_time_to
        )
    elif os.path.exists("tasks.json"):
        tasks = load_from_json("tasks.json")
        if tasks:
            task_config = tasks[0]
            logger.info(f"已从 tasks.json 加载任务: {task_config.origin} -> {task_config.destination} ({task_config.date})")

    if not task_config:
        print("💡 未提供完整行程预选参数。请使用 --wizard 进入交互向导，或指定 --origin, --dest, --date 参数。")
        print("例如: python main.py --origin \"KL Sentral\" --dest \"Butterworth\" --date \"2026-09-20\" --wizard")
        return

    repo = TaskRepository()
    repo.save_task(task_config)

    notifier = None
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        notifier = TelegramTicketNotifier(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID
        )

    browser_driver = None
    browser_manager = None
    if not args.no_browser:
        try:
            logger.info("正在初始化 Playwright Stealth 无头浏览器...")
            browser_manager = BrowserManager(headless=args.headless)
            page = browser_manager.start()
            browser_driver = KTMBrowserDriver(page=page)
            logger.info("无头浏览器已就绪，正在预热加载 KITS 页面...")
            browser_driver.navigate_to_booking()
            browser_driver.fill_search_criteria(task_config)
        except Exception as e:
            logger.warning(f"启动无头浏览器失败: {e}。将自动降级至高拟态 API 模式。")
            browser_driver = None

    engine = KTMSniperEngine(
        task=task_config,
        notifier=notifier,
        repository=repo,
        browser_driver=browser_driver,
        max_cycles=args.max_cycles
    )

    tg_listener = None
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        try:
            from ktm_sniper.telegram_bot import TelegramCommandListener
            tg_listener = TelegramCommandListener(
                bot_token=settings.TELEGRAM_BOT_TOKEN,
                authorized_chat_id=settings.TELEGRAM_CHAT_ID,
                engine=engine
            )
            tg_listener.start()
            logger.info("📱 Telegram 远程指令监听器已就绪 (可在手机随时发送 /status, /set, /pause, /resume)！")
        except Exception as e:
            logger.warning(f"启动 Telegram 指令监听器失败: {e}")

    try:
        logger.info(f"🚀 开始抢票守护: {task_config.origin} ➡️ {task_config.destination} on {task_config.date}")
        logger.info(f"⏰ 时段筛选: {task_config.time_from} - {task_config.time_to}")
        if task_config.is_round_trip:
            logger.info(f"🔄 往返双程模式已启用: 返程 {task_config.destination} ➡️ {task_config.origin} on {task_config.return_date} ({task_config.return_time_from} - {task_config.return_time_to})")
        engine.run()
    finally:
        if tg_listener:
            tg_listener.stop()
        if browser_manager:
            browser_manager.close()

if __name__ == "__main__":
    main()