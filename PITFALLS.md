# KTMB (KITS) 抢票机器人开发踩坑与风控实战经验库 (PITFALLS.md)

本项目开发过程中总结的风控对抗、跨平台兼容性、无头浏览器、敏感隐私保护 (MyKad / 手机号 PII) 与自动化调度踩坑记录与已验证解决方案。

---

### 1. Windows 控制台中文与 Emoji 编码崩溃 (`charmap` Codec)
* **问题现象 (Root Cause)**：
  在 Windows PowerShell 或 CMD 环境下运行 `python main.py --help` 或交互式向导时，系统报：
  `UnicodeEncodeError: 'charmap' codec can't encode characters in position ...: character maps to <undefined>`。
  原因在于 Windows 默认标准输出流（stdout/stderr）使用系统代码页（如 `cp1252`），无法处理中文或 🚄、🚨 等 Emoji 字符。
* **已验证解决方案 (Verified Solution)**：
  在 `main.py` 入口最顶部对 Windows 平台重设 stdout 与 stderr 编码：
  ```python
  import sys
  if sys.platform == "win32":
      try:
          if hasattr(sys.stdout, "reconfigure"):
              sys.stdout.reconfigure(encoding="utf-8")
          if hasattr(sys.stderr, "reconfigure"):
              sys.stderr.reconfigure(encoding="utf-8")
      except Exception:
          pass
  ```

---

### 2. KITS 官网首页广告弹窗与 Select2 控件隐藏导致 `Page.fill: Timeout`
* **问题现象 (Root Cause)**：
  KTMB KITS 线上站点（`online.ktmb.com.my`）首页加载时常弹出广告营销模态框（如 `#CloseButtonAdvertisement`、`#popupModalOkButton`），阻断下层页面交互。
  同时，站点的始发站与目的站使用 jQuery `Select2` 渲染，原生 `<input>` 和 `<select>` 被添加了 `select2-hidden-accessible`，若使用常规的 `page.fill("input#originStation")` 会因为元素不可见触发 30 秒超时挂起。
* **已验证解决方案 (Verified Solution)**：
  1. 页面加载完毕后，优先执行 `dismiss_modals()` 自动关闭弹窗：
     ```python
     for selector in ["#CloseButtonAdvertisement", "#popupModalOkButton", "#popupModalCloseButton"]:
         if page.locator(selector).is_visible(timeout=1000):
             page.click(selector, timeout=1500)
     ```
  2. 针对 Select2 控件，结合 DOM 属性注入与 `select2('val')` 事件触发，快速完成站点代码绑定，避免长时间超时挂起。

---

### 3. 日期单数字格式崩溃 (`2027-02-4` vs `2027-02-04`)
* **问题现象 (Root Cause)**：
  用户在向导或命令行中输入日期时，常会省略前导 0（例如输入 `2027-02-4`、`2027-2-4` 或 `2027/2/4`）。如果采用严格的 `^\d{4}-\d{2}-\d{2}$` 正则校验，会直接抛出 `ValueError: Invalid date format` 崩溃退出。
* **已验证解决方案 (Verified Solution)**：
  在 `ktm_sniper/models.py` 中引入容错自动补零函数 `normalize_date`：
  ```python
  m = re.match(r"^(\d{4})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])$", cleaned)
  if m:
      return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
  ```
  自动将 `2027-02-4` 或 `2027/2/4` 规范化为合规的 `2027-02-04`，并在向导中提供循环容错重试。

---

### 4. 身份证号 (MyKad) 与手机号 (Phone) 等全维度 PII 泄露防御
* **问题现象 (Root Cause)**：
  用户的手机号和身份证号均属于高敏感度 PII。若在代码中未做保护，会导致：
  1. 打印数据对象或日志（`repr(passenger)`）将真实手机号码和身份证号刷入日志；
  2. 终端历史命令记录泄露（命令行明文 `--ic` 或 `--phone` 记录在 Shell 历史文件中）；
  3. 交互式控制台输入时被旁窥；
  4. SQLite 本地数据库落盘保存明文电话与证件。
* **已验证解决方案 (Verified Solution)**：
  实施全维度 PII 纵深防御体系：
  1. **手机号与证件号统一脱敏 (Masked Phone & IC)**：
     - `mask_ic`：脱敏身份证（如 `900101-14-****`）
     - `mask_phone`：脱敏手机号（如 `+601****789`、`0192****445`）
     - 重写 `Passenger.__repr__`，打印对象时两者均自动脱敏展示。
  2. **双环境变量防 Shell 历史泄露**：
     同时支持 `KTM_PASSENGER_IC` 和 `KTM_PASSENGER_PHONE` 环境变量注入，无需在终端参数中显式输入明文。
  3. **交互输入防旁窥**：使用 `getpass` 隐藏身份证输入。
  4. **落盘加密 (Encryption at Rest)**：所有包含证件与手机号的数据在存入 SQLite 前均经由 `Fernet` (AES-GCM) 密文存储。
  5. **敏感文件阻断**：严格将 `.ktm_key`、`*.db`、`.env` 纳入 `.gitignore`。

---

### 5. Python `sqlite3` 上下文管理未关闭连接导致 `ResourceWarning`
* **问题现象 (Root Cause)**：
  在实现 `TaskRepository` 时，使用 `with self._get_connection() as conn:`，许多开发者误以为退出 `with` 块会自动关闭连接。实际上，Python 的 `sqlite3.Connection.__exit__` 仅负责 `commit()` 或 `rollback()`，并不关闭连接，导致 pytest 测试退出时触发：
  `ResourceWarning: unclosed database in <sqlite3.Connection object>`。
* **已验证解决方案 (Verified Solution)**：
  始终在显式 `try...finally: conn.close()` 中管理生命周期：
  ```python
  conn = self._get_connection()
  try:
      with conn:
          conn.execute(...)
  finally:
      conn.close()
  ```

---

### 6. Cloudflare 静态拦截与 JA3/JA4 TLS 指纹
* **问题现象 (Root Cause)**：
  KTMB KITS 线上服务部署了 Cloudflare。标准 `requests` 或 `urllib` 发起 HTTPS 请求时，Client Hello 报文的 TLS 密码套件、扩展顺序暴露了标准 Python 特征，直接触发 403 Forbidden。
* **已验证解决方案 (Verified Solution)**：
  采用“无头浏览器为先 + 高拟态 API 为辅”的双层对抗：
  - **首选 Playwright Chromium**：通过真实浏览器的 Blink 渲染引擎和网络栈直接通过 Cloudflare JS 质询与 Turnstile。
  - **备选 `curl_cffi`**：底层绑定 `curl-impersonate`，模拟真实 Chrome 120 的 JA3/JA4 指纹及 HTTP/2 帧头特征。

---

### 7. 15 分钟锁票倒计时与安全边界
* **问题现象 (Root Cause)**：
  KTMB 官方席位预占成功后，仅提供 15 分钟的支付保留窗口；若由机器人全自动代扣银行卡，极易面临银行 3D Secure 验证死锁或扣款争议。
* **已验证解决方案 (Verified Solution)**：
  确立“锁座即唤醒 (Lock & Alert)”原则：
  - 机器人只负责毫秒级锁定席位并提取 `booking_id`；
  - 立即生成带有效期的直达支付链接 `https://online.ktmb.com.my/v2/payment/checkout?bookingId=...`；
  - 通过 Telegram 即时推送脱敏富文本与无头浏览器现场截图，用户直接在手机点击链接并在 15 分钟内通过手机银行或电子钱包支付。

---

### 8. 车站别名与模糊匹配的短路贪婪问题 (Greedy Substring Match vs Multi-candidate Disambiguation)
* **问题现象 (Root Cause)**：
  当用户在终端输入泛用词（例如 `sentral`, `bukit`, `kuala`）时，若直接使用单次遍历判断 `if keyword in station_name` 并立刻 `return`，会永远被字典遍历中的第一个车站（如 `KL SENTRAL`）截胡，导致无法选择 `JB SENTRAL` 或 `KEPONG SENTRAL`。
* **已验证解决方案 (Verified Solution)**：
  采用“精确命中优先，多重匹配呈单”策略：
  1. **第一优先级**：严格全字匹配车站代码（如 `KLS`）、官方全称（如 `KL SENTRAL`）或精确别名（如 `BM` $\to$ `BUKIT MERTAJAM`、`PG` $\to$ `BUTTERWORTH`、`大山脚`）；
  2. **第二优先级**：执行 `search_stations()` 收集全部候选集。若仅有 1 个匹配项，直接自动选中；若有多项匹配，在终端渲染清晰的编号候选菜单供用户输入数字选择。

---

### 9. 往返双程票 (Round Trip) 调度中的断点衔接与状态流转
* **问题现象 (Root Cause)**：
  往返双程票跨越两个不同日期与相反方向。如果在去程票成功锁定后主进程直接返回退出，用户需要重新手动配置返程任务，容易因间隔延迟错过返程放票窗口。
* **已验证解决方案 (Verified Solution)**：
  在 `KTMSniperEngine` 中实现无缝任务接力：
  1. 去程锁定成功并发送 Telegram 告警后，引擎提取 `outbound_result`；
  2. 自动调用 `self.task.get_return_task()` 生成对应方向的返程任务（设置 `leg_type="RETURN"`），并写入 SQLite 审计表；
  3. 若启动了无头浏览器，自动触发 `fill_search_criteria` 填入返程日期与反转路线；
  4. 轮询循环不中断，无缝继续监控返程票，直至双程全部锁定后汇总返回完整结果。

---

### 10. KITS 登录认证 — CSRF Token 与 Cookie 绑定、Session 过期检测

* **问题现象 (Root Cause)**：
  KTMB KITS 登录页面 (`/Account/Login`) 使用 ASP.NET Core 的 `__RequestVerificationToken` 进行 CSRF 防护。该 Token 与当前 Session Cookie 绑定，如果直接使用裸 API POST 发送登录请求（携带从其他请求提取的 Token），会因为 Cookie 不匹配而被 403 拒绝。此外，登录成功后 KITS 依赖服务端 Session Cookie 维持认证状态，长时间轮询后 Session 可能过期导致后续预定请求失败。

* **已验证解决方案 (Verified Solution)**：
  1. **通过 Playwright 浏览器执行登录**：而非裸 HTTP 请求。浏览器自动处理 Cookie 与 CSRF Token 的绑定关系，填写 `#Email` 和 `#Password` 后点击 `#LoginButton`，完全模拟真人操作。
  2. **登录状态验证**：通过检测导航栏中 "Login / sign up" 链接的存在/消失来判断是否已认证。
  3. **Session 过期自动重连**：在 `KTMSniperEngine` 的轮询循环中，每 10 轮执行一次 `browser_driver.is_logged_in()` 检查。若检测到 Session 失效，调用 `authenticator.ensure_authenticated()` 自动重新登录。
  4. **重试与告警**：登录失败时最多重试 3 次（递增延迟），全部失败后通过 Telegram 推送告警通知用户检查凭证。

---

### 11. KITS 单会话排他限制 ("Not allow multiple login")

* **问题现象 (Root Cause)**：
  KTMB KITS 线上系统严格执行单设备/单会话排他策略。若用户在个人电脑浏览器、手机 App 或其他终端已登录该账号且未显式退出（Sign out），机器人尝试登录时会被服务端拦截并弹窗报错：
  `"Not allow multiple login. Please use \"Forget Password\" if you are unable to login."`。
* **已验证解决方案 (Verified Solution)**：
  1. **人工前置登出**：在启动抢票机器人前，必须在个人浏览器或手机端点击「Log Out / 退出登录」，释放服务端绑定的 Active Session。
  2. **精准错误捕获与提示**：在 `KTMAuthenticator` 捕获到 `"Not allow multiple login"` 弹窗文字时，在日志中向用户明确提示：「检测到账号在其他设备已登录，请先在浏览器/手机登出后再运行」。
  3. **忘记密码强制重置踢出会话**：若服务端 Session 挂起或被锁死，通过官方忘记密码流程（`/Account/ForgetPassword`）重置密码，KTMB 服务端会立即强制踢除所有挂起会话并解锁。
  4. **机器人关闭时优雅登出**：在 `main.py` 的 `finally:` 块中通过 `authenticator.logout()` 访问 `/Account/Logout`，确保每次守护退出时均显式释放服务端会话，保障下次启动无缝登录。
  5. **推送支付二维码后立即自动登出**：在机器人锁定席位并在官方结账台生成 DuitNow 支付二维码推送到 Telegram 后，后台立即自动调用 `/Account/Logout`，彻底释放单点登录锁，保障用户能在手机 App 或电脑端随意登录付款。

---

### 12. KTMB /Book 内部 POST 路由与临时会话购物车机制 (HTTP 405)

* **问题现象 (Root Cause)**：
  1. KTMB KITS 票务系统的 `/Book` 端点仅支持表单内部 POST 请求，不提供任何 GET 接口。直接在外部浏览器打开或从 Telegram 链接访问 `/Book` 时，服务器返回 `HTTP ERROR 405 Method Not Allowed`。
  2. KTMB 没有类似于电商平台的持久化未支付订单（Unpaid Cart）。用户在 `Upcoming trips` 中只能查看到已经扣款成功的车票。未支付的锁定位仅存在于当前浏览器的临时结账会话中。
* **已验证解决方案 (Verified Solution)**：
  1. **禁止回传 `/Book`**：在 `driver.py` 中严格过滤支付 URL，杜绝 `/Book` 链接，改用官方行程待办列表 `UpcomingList` 或带订单号的 `Payment/Checkout?bookingId=...`。
  2. **官方结账台调起 DuitNow QR**：在到达官方结账台后，机器人直接点击并调起 DuitNow QR 通道，截取付款二维码高清图片直发 Telegram。
  3. **手机原生扫码付款**：用户在手机 Telegram 保存二维码图片，通过 Touch 'n Go 或银行 App 的扫一扫选择相册识别付款，无需在任何浏览器重复登录。

---

### 13. 动态控制模块缺包与作用域陷阱 (`name 'os' is not defined`)

* **问题现象 (Root Cause)**：
  在 `ktm_sniper/telegram_bot.py` 中处理 `/seat` 指令发送支付二维码图片时，调用了 `os.path.exists(qr_path)`，但文件头部未导入 `os` 模块，导致在用户发出选座指令时触发 `NameError: name 'os' is not defined` 导致下单流程中断。
* **已验证解决方案 (Verified Solution)**：
  1. 在 `ktm_sniper/telegram_bot.py` 顶部显式补齐 `import os`，并对 `ktm_sniper/browser/driver.py` 进行全局 `import os, shutil` 模块级规范化。
  2. 为 Telegram `/seat` 包含 QR 图片与自动登出的完整控制流补充自动化单元测试（`test_command_handler_seat_with_qr_and_autologout`），纳入 CI 回归基线。


