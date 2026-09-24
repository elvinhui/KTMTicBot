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
from ktm_sniper.auth import KTMAuthenticator

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

def run_login_gui():
    """
    Opens a visible browser on Windows, navigates to KITS login page,
    lets the user log in (or auto-fills credentials), and persists the session
    to data/auth_state.json upon success.
    """
    import time
    print("\n🌐 --- 启动 KTMB 官方可视化登录助手 --- 🌐")
    print("正在启动 Chrome 浏览器窗口，请稍候...")
    mgr = BrowserManager(headless=False)
    page = mgr.start()
    auth = KTMAuthenticator(
        email=settings.KTM_EMAIL,
        password=settings.KTM_PASSWORD
    )
    login_url = settings.BASE_URL.rstrip("/") + "/Account/Login"
    try:
        page.goto(login_url)
    except Exception as e:
        print(f"⚠️ 页面导航提示: {e}")

    # Try auto-fill if credentials exist
    if settings.KTM_EMAIL and settings.KTM_PASSWORD:
        try:
            print(f"👉 检测到配置账号: {settings.KTM_EMAIL}，正在尝试自动填入...")
            auth._dismiss_modals(page)
            if page.locator("#Email").is_visible(timeout=3000):
                page.fill("#Email", settings.KTM_EMAIL)
                page.fill("#Password", settings.KTM_PASSWORD)
                page.click("#LoginButton")
        except Exception as e:
            print(f"⚠️ 自动填入未完成 ({e})，请在弹出的浏览器窗口中直接手动输入完成登录。")

    print("\n💡 请在弹出的浏览器窗口中完成登录：")
    print("• 如果页面提示验证码或多设备登录，请按提示完成操作。")
    print("• 登录成功后，助手会自动捕获 Cookies 并保存，无需其他操作！")
    print("• 正在等待登录完成（最长等待 180 秒）...")

    start_time = time.time()
    logged_in = False
    while time.time() - start_time < 180:
        time.sleep(2.0)
        try:
            if auth.verify_login(page):
                logged_in = True
                break
        except Exception:
            pass

    if logged_in:
        saved_path = mgr.save_storage_state("data/auth_state.json")
        print(f"\n🎉 官方登录成功！会话已永久固化保存至: {saved_path}")
        print("💡 以后无论是本地还是 EC2 部署，都将直接使用该已认证状态，彻底免除密码登录与多设备冲突！\n")
    else:
        print("\n⏳ 等待超时或未完成登录。请重新运行重试。\n")

    mgr.close()

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
    parser.add_argument("--confirm", action="store_true", default=True, help="发现余票后先发送 Telegram 交互列表确认车次与座位 (默认 True)")
    parser.add_argument("--auto-lock", action="store_true", help="发现余票后直接自动盲锁第一班车，无需确认")
    parser.add_argument("--login-gui", action="store_true", help="启动可视化浏览器完成一次性登录并将认证会话固化保存至 data/auth_state.json")

    args = parser.parse_args()

    if args.login_gui:
        run_login_gui()
        return

    repo = TaskRepository()
    saved_task = repo.get_latest_task()
    saved_passengers = repo.get_saved_passengers()

    task_config: Optional[SniperTaskConfig] = None

    if args.config:
        tasks = load_from_json(args.config)
        if tasks:
            task_config = tasks[0]
            logger.info(f"已从 {args.config} 加载任务: {task_config.origin} -> {task_config.destination} ({task_config.date})")
            repo.save_task(task_config)
    elif args.wizard:
        task_config = run_wizard()
        if task_config:
            repo.save_task(task_config)
    elif args.origin or args.dest or args.date:
        # CLI parameters provided: merge with SQLite saved configuration if partial
        base_origin = args.origin or (saved_task.origin if saved_task else None)
        base_dest = args.dest or (saved_task.destination if saved_task else None)
        base_date = args.date or (saved_task.date if saved_task else None)

        if not (base_origin and base_dest and base_date):
            print("💡 请提供完整的行程参数: --origin, --dest, --date (或使用 --wizard 交互向导)")
            return

        is_rt = args.round_trip or bool(args.return_date) or (saved_task.is_round_trip if saved_task else False)
        ret_date = args.return_date or (saved_task.return_date if saved_task else None)
        if is_rt and not ret_date:
            logger.error("启用往返模式 (--round-trip) 时必须通过 --return-date 指定返程日期！")
            return

        # Passenger resolution: strictly CLI -> SQLite (no env or json)
        passengers = []
        if args.name and args.ic:
            new_p = Passenger(
                name=args.name.strip(),
                id_number=args.ic.strip(),
                gender=args.gender or "Male",
                phone=args.phone or "0123456789"
            )
            passengers.append(new_p)
            repo.save_passenger(new_p)
        elif saved_task and saved_task.passengers:
            passengers = list(saved_task.passengers)
        elif saved_passengers:
            passengers = list(saved_passengers)

        task_config = SniperTaskConfig(
            task_id=saved_task.task_id if saved_task else None,
            origin=base_origin,
            destination=base_dest,
            date=base_date,
            time_from=args.time_from if args.time_from != "00:00" else (saved_task.time_from if saved_task else "00:00"),
            time_to=args.time_to if args.time_to != "23:59" else (saved_task.time_to if saved_task else "23:59"),
            preferred_trains=args.train or (saved_task.preferred_trains if saved_task else []),
            preferred_classes=args.class_name or (saved_task.preferred_classes if saved_task else []),
            seat_preference=args.seat or (saved_task.seat_preference if saved_task else "Window"),
            required_seats=len(passengers) or (saved_task.required_seats if saved_task else 1),
            passengers=passengers,
            is_round_trip=is_rt,
            return_date=ret_date,
            return_time_from=args.return_time_from if args.return_time_from != "00:00" else (saved_task.return_time_from if saved_task else "00:00"),
            return_time_to=args.return_time_to if args.return_time_to != "23:59" else (saved_task.return_time_to if saved_task else "23:59"),
            status=TaskStatus.MONITORING
        )
        task_config.require_confirmation = not args.auto_lock
        repo.save_task(task_config)
        logger.info(f"💾 新设定的行程与乘客已同步持久化保存至 SQLite [{repo.db_path}]")

    elif saved_task:
        # Default run: extract directly from SQLite! (No JSON, no .ENV)
        task_config = saved_task
        task_config.status = TaskStatus.MONITORING
        task_config.require_confirmation = not args.auto_lock
        if not task_config.passengers and saved_passengers:
            task_config.passengers = list(saved_passengers)
            task_config.required_seats = len(saved_passengers)

        passengers_desc = ", ".join([f"{p.name} ({p.masked_id})" for p in task_config.passengers]) if task_config.passengers else "未指定"
        msg = (
            f"📦 --- [SQLite 数据源] 已成功提取最近配置的抢票任务 ---\n"
            f"• 数据库路径: {repo.db_path}\n"
            f"• 出发地: {task_config.origin}\n"
            f"• 目的地: {task_config.destination}\n"
            f"• 出发日期: {task_config.date} ({task_config.time_from} - {task_config.time_to})\n"
        )
        if task_config.is_round_trip:
            msg += f"• 返程日期: {task_config.return_date} ({task_config.return_time_from} - {task_config.return_time_to})\n"
        msg += f"• 乘车人员: {passengers_desc}\n• 席位总数: {task_config.required_seats} 席 (偏好: {task_config.seat_preference})\n"
        print(f"\n{msg}")
        logger.info(f"📦 已从 SQLite 加载任务与乘客: {task_config.origin} -> {task_config.destination} ({task_config.date}) [{passengers_desc}]")

    if not task_config:
        print("💡 SQLite 数据库中暂未发现已存储的抢票行程。")
        print("首次使用请指定参数（例如: python main.py --origin \"KL Sentral\" --dest \"Ipoh\" --date \"2026-10-05\" --name \"TAN JIA HUI\" --ic \"960217075045\"）")
        print("或运行向导: python main.py --wizard")
        print("设定完成后将自动永久固化保存在 SQLite 中，后续再次运行无需输入任何参数即可直接启动！\n")
        return

    task_config.require_confirmation = not args.auto_lock
    repo.save_task(task_config)

    notifier = None
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        notifier = TelegramTicketNotifier(
            bot_token=settings.TELEGRAM_BOT_TOKEN,
            chat_id=settings.TELEGRAM_CHAT_ID
        )

    browser_driver = None
    browser_manager = None
    authenticator = None
    if not args.no_browser:
        try:
            logger.info("正在初始化 Playwright Stealth 无头浏览器...")
            browser_manager = BrowserManager(headless=args.headless)
            page = browser_manager.start()
            browser_driver = KTMBrowserDriver(page=page)

            # --- 登录认证 ---
            if settings.KTM_EMAIL and settings.KTM_PASSWORD:
                authenticator = KTMAuthenticator(
                    email=settings.KTM_EMAIL,
                    password=settings.KTM_PASSWORD,
                )
                login_success = authenticator.ensure_authenticated(
                    page=page,
                    notifier=notifier,
                )
                if login_success:
                    logger.info("✅ KITS 账号登录成功，已获取认证 Session！")
                else:
                    logger.warning("⚠️ KITS 登录失败，将以游客模式继续运行（仅能搜索，无法预定）。")
            else:
                logger.warning("⚠️ 未配置 KTM_EMAIL/KTM_PASSWORD，将以游客模式运行（仅能搜索，无法预定）。")

            logger.info("无头浏览器已就绪，正在预热加载 KITS 页面并检索车次列表...")
            browser_driver.navigate_to_booking()
            browser_driver.fill_search_criteria(task_config)
            browser_driver.trigger_search()
        except Exception as e:
            logger.warning(f"启动无头浏览器失败: {e}。将自动降级至高拟态 API 模式。")
            browser_driver = None

    engine = KTMSniperEngine(
        task=task_config,
        notifier=notifier,
        repository=repo,
        browser_driver=browser_driver,
        authenticator=authenticator,
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
        if authenticator and browser_manager and browser_manager.page:
            try:
                authenticator.logout(browser_manager.page)
            except Exception:
                pass
        if browser_manager:
            browser_manager.close()

if __name__ == "__main__":
    main()